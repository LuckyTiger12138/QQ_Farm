"""异步任务执行器 — 后台线程 + 优先级调度（移植自 qq-farm-copilot）

核心能力：
  - 每个任务独立配置（间隔/时段/优先级/重试）
  - 成功/失败间隔分离
  - 时间范围过滤（enabled_time_range）+ 下次启动时间计算
  - daily 触发模式（每日定时）
  - 失败计数 + 最大连续失败
  - 热更新任务参数（无需重启）
  - 线程安全（RLock 保护内部状态）
  - 快照（pending/waiting）供 GUI 展示
"""
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta
from typing import Callable, Any

from loguru import logger

from models.config import (
    TaskScheduleItemConfig,
    ExecutorConfig,
    TaskTriggerType,
    normalize_task_enabled_time_range,
)


@dataclass
class TaskItem:
    """运行时任务项"""
    name: str
    enabled: bool
    priority: int
    next_run: datetime
    success_interval: int
    failure_interval: int
    trigger: str = TaskTriggerType.INTERVAL.value
    enabled_time_range: str = "00:00:00-23:59:59"
    daily_time: str = "00:01"
    max_failures: int = 3
    failure_count: int = 0
    last_run: datetime | None = None
    last_result: str = ""
    features: dict = field(default_factory=dict)


@dataclass
class TaskResult:
    """任务执行结果"""
    success: bool
    next_run_seconds: int | None = None
    need_recover: bool = False
    error: str = ""


@dataclass
class TaskContext:
    """任务执行上下文"""
    task_name: str
    started_at: datetime


@dataclass
class TaskSnapshot:
    """任务快照（供 GUI 展示，copilot 风格）"""
    running_task: str | None
    pending_tasks: list[TaskItem]
    waiting_tasks: list[TaskItem]


# ── 辅助函数 ──────────────────────────────────────────────


def _parse_time_range_seconds(value: str) -> tuple[int, int]:
    """将 HH:MM:SS-HH:MM:SS 启用时间段转换为秒范围。"""
    normalized = normalize_task_enabled_time_range(value)
    start_text, end_text = normalized.split("-", 1)
    sh, sm, ss = start_text.strip().split(":", 2)
    eh, em, es = end_text.strip().split(":", 2)
    return int(sh) * 3600 + int(sm) * 60 + int(ss), int(eh) * 3600 + int(em) * 60 + int(es)


def _is_in_time_range(now: datetime, time_range: str) -> bool:
    """检查当前时间是否在允许时段内"""
    start, end = _parse_time_range_seconds(time_range)
    if start == end:
        return True
    current = now.hour * 3600 + now.minute * 60 + now.second
    if start < end:
        return start <= current <= end
    return current >= start or current <= end


def _next_enabled_time_start(task: TaskItem, now: datetime) -> datetime:
    """计算任务下一个可执行时间段起点（移植自 copilot）。"""
    start, end = _parse_time_range_seconds(task.enabled_time_range)
    if start == end:
        return now

    start_hour = start // 3600
    start_minute = (start % 3600) // 60
    start_second = start % 60
    start_dt = now.replace(hour=start_hour, minute=start_minute, second=start_second, microsecond=0)
    current = now.hour * 3600 + now.minute * 60 + now.second
    if start < end:
        if current < start:
            return start_dt
        return start_dt + timedelta(days=1)

    # 跨天区间
    if end < current < start:
        return start_dt
    return start_dt + timedelta(days=1)


def _compute_next_daily(daily_time: str, now: datetime) -> datetime:
    """计算下一次 daily 触发时间"""
    parts = daily_time.strip().split(":")
    hour, minute = int(parts[0]), int(parts[1])
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _normalize_trigger_text(trigger) -> str:
    """将任务触发类型规范化为 interval/daily。"""
    if isinstance(trigger, TaskTriggerType):
        return trigger.value
    raw = str(trigger or "").strip().lower()
    if raw in {TaskTriggerType.INTERVAL.value, TaskTriggerType.DAILY.value}:
        return raw
    if "." in raw:
        tail = raw.split(".")[-1]
        if tail in {TaskTriggerType.INTERVAL.value, TaskTriggerType.DAILY.value}:
            return tail
    return raw


def build_task_item(name: str, cfg: TaskScheduleItemConfig) -> TaskItem:
    """从配置构建 TaskItem"""
    now = datetime.now()
    if cfg.next_run:
        try:
            next_run = datetime.fromisoformat(cfg.next_run)
            if next_run < now:
                next_run = now
        except (ValueError, TypeError):
            next_run = now
    else:
        next_run = now

    return TaskItem(
        name=name,
        enabled=cfg.enabled,
        priority=cfg.priority,
        next_run=next_run,
        success_interval=cfg.interval_seconds,
        failure_interval=cfg.failure_interval_seconds,
        trigger=cfg.trigger,
        enabled_time_range=cfg.enabled_time_range,
        daily_time=cfg.daily_time,
        max_failures=3,
        features=cfg.features,
    )


# runner 函数签名：接收 TaskContext，返回 TaskResult
TaskRunner = Callable[[TaskContext], TaskResult]


class TaskExecutor:
    """异步任务执行器：后台线程 + 优先级调度（移植自 copilot）"""

    def __init__(
        self,
        tasks: list[TaskItem],
        runners: dict[str, TaskRunner],
        *,
        executor_cfg: ExecutorConfig | None = None,
        on_snapshot: Callable[[TaskSnapshot], None] | None = None,
        on_task_done: Callable[[str, TaskResult], None] | None = None,
        on_task_error: Callable[[str, str], None] | None = None,
        on_idle: Callable[[], None] | None = None,
        cross_bus: Any = None,
        instance_id: str = "",
    ):
        self._tasks = {t.name: t for t in tasks}
        self._runners = runners
        self._executor_cfg = executor_cfg or ExecutorConfig()

        # 回调
        self._on_snapshot = on_snapshot
        self._on_task_done = on_task_done
        self._on_task_error = on_task_error
        self._on_idle = on_idle

        # 跨实例通讯
        self._cross_bus = cross_bus
        self._instance_id = instance_id

        # 线程控制
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._wake_event = threading.Event()
        self._running_task: str | None = None
        self._last_idle_at: float = 0.0

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self):
        """启动后台线程"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._pause_event.set()
            self._wake_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        logger.info("TaskExecutor 已启动")

    def stop(self, wait_timeout: float = 5.0) -> bool:
        """停止后台线程"""
        self._stop_event.set()
        self._wake_event.set()
        self._pause_event.set()
        th = self._thread
        if th and th.is_alive():
            th.join(timeout=max(0.1, float(wait_timeout)))
        stopped = not (th and th.is_alive())
        if stopped:
            with self._lock:
                if self._thread is th:
                    self._thread = None
                self._running_task = None
        logger.info("TaskExecutor 已停止")
        return stopped

    def pause(self):
        """暂停调度"""
        self._pause_event.clear()
        logger.info("TaskExecutor 已暂停")

    def resume(self):
        """恢复调度"""
        self._pause_event.set()
        self._wake_event.set()
        logger.info("TaskExecutor 已恢复")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── 热更新 ──────────────────────────────────────────────

    def update_task(self, name: str, **kwargs):
        """热更新任务参数（线程安全）"""
        with self._lock:
            task = self._tasks.get(name)
            if not task:
                return
            for k, v in kwargs.items():
                if hasattr(task, k):
                    setattr(task, k, v)
        logger.debug(f"任务 {name} 已更新: {kwargs}")

    def task_delay(self, name: str, *, seconds: int | None = None, target_time: datetime | None = None) -> bool:
        """延后任务执行（移植自 copilot：支持相对秒数或绝对时间）"""
        with self._lock:
            task = self._tasks.get(name)
            if not task:
                return False
            candidates: list[datetime] = []
            if seconds is not None:
                candidates.append(datetime.now() + timedelta(seconds=max(0, int(seconds))))
            if target_time is not None:
                candidates.append(target_time)
            if not candidates:
                return False
            task.next_run = min(candidates)
        self._wake_event.set()
        return True

    def is_task_enabled(self, name: str) -> bool:
        """检查指定任务是否启用（线程安全）"""
        with self._lock:
            task = self._tasks.get(name)
            return bool(task and task.enabled)

    def task_call(self, name: str, force_call: bool = True) -> bool:
        """立即触发任务（移植自 copilot：可选强制启用任务）"""
        with self._lock:
            task = self._tasks.get(name)
            if not task:
                return False
            if not task.enabled and not force_call:
                return False
            if force_call:
                task.enabled = True
            task.next_run = datetime.now()
        self._wake_event.set()
        logger.info(f"任务 {name} 立即触发")
        return True

    def sync_tasks(self, configs: dict[str, TaskScheduleItemConfig]):
        """从配置同步任务列表（增/删/改）"""
        with self._lock:
            existing = set(self._tasks.keys())
            new_names = set(configs.keys())

            for name in existing - new_names:
                del self._tasks[name]
                logger.debug(f"移除任务: {name}")

            for name, cfg in configs.items():
                if name in self._tasks:
                    task = self._tasks[name]
                    task.enabled = cfg.enabled
                    task.priority = cfg.priority
                    task.success_interval = cfg.interval_seconds
                    task.failure_interval = cfg.failure_interval_seconds
                    task.trigger = cfg.trigger
                    task.enabled_time_range = cfg.enabled_time_range
                    task.daily_time = cfg.daily_time
                    task.max_failures = 3
                    task.features = cfg.features
                    # 同步 next_run（用户手动修改 DateTimeEdit 时需要）
                    if cfg.next_run:
                        try:
                            nr = datetime.fromisoformat(cfg.next_run)
                            if nr != task.next_run:
                                task.next_run = nr
                        except (ValueError, TypeError):
                            pass
                else:
                    self._tasks[name] = build_task_item(name, cfg)
                    logger.debug(f"新增任务: {name}")

    def set_empty_queue_policy(self, policy: str):
        """设置空队列策略"""
        with self._lock:
            self._executor_cfg.empty_queue_policy = str(policy or "stay")

    # ── 快照（copilot 风格） ──────────────────────────────────

    def snapshot(self, now: datetime | None = None) -> TaskSnapshot:
        """生成当前快照（copilot 风格：running + pending + waiting）"""
        with self._lock:
            return self._snapshot_locked(now or datetime.now())

    def flat_snapshot(self) -> list[dict]:
        """生成扁平快照列表（兼容旧 GUI 展示）"""
        with self._lock:
            now = datetime.now()
            result = []
            for t in self._tasks.values():
                if not t.enabled:
                    status = "disabled"
                elif self._running_task == t.name:
                    status = "running"
                elif t.next_run <= now:
                    status = "pending"
                else:
                    status = "waiting"
                result.append({
                    "name": t.name,
                    "status": status,
                    "next_run": t.next_run,
                    "priority": t.priority,
                    "last_result": t.last_result,
                    "failure_count": t.failure_count,
                })
            return result

    def _snapshot_locked(self, now: datetime) -> TaskSnapshot:
        """持锁快照（移植自 copilot）"""
        pending: list[TaskItem] = []
        waiting: list[TaskItem] = []
        for task in self._tasks.values():
            if not task.enabled:
                continue
            if task.next_run <= now:
                pending.append(self._clone_item(task))
            else:
                waiting.append(self._clone_item(task))
        pending.sort(key=lambda t: t.priority)
        waiting.sort(key=lambda t: t.next_run)
        return TaskSnapshot(
            running_task=self._running_task,
            pending_tasks=pending,
            waiting_tasks=waiting,
        )

    @staticmethod
    def _clone_item(item: TaskItem) -> TaskItem:
        """拷贝任务对象，用于快照输出避免外部改写内部状态（移植自 copilot）"""
        return TaskItem(
            name=item.name,
            enabled=item.enabled,
            priority=item.priority,
            next_run=item.next_run,
            success_interval=item.success_interval,
            failure_interval=item.failure_interval,
            trigger=item.trigger,
            enabled_time_range=item.enabled_time_range,
            daily_time=item.daily_time,
            max_failures=item.max_failures,
            failure_count=item.failure_count,
            last_run=item.last_run,
            last_result=item.last_result,
            features=dict(item.features) if item.features else {},
        )

    # ── 结果应用（移植自 copilot） ────────────────────────────

    def _apply_task_result(self, task: TaskItem, result: TaskResult):
        """根据任务结果更新失败次数与下一次执行时间（移植自 copilot）"""
        now = datetime.now()
        if result.success:
            task.failure_count = 0
            task.last_result = "success"
            interval = int(task.success_interval)
        else:
            task.failure_count += 1
            task.last_result = "failure"
            interval = int(task.failure_interval)

        # runner 显式给出下一次延迟时优先使用
        if result.next_run_seconds is not None:
            interval = int(result.next_run_seconds)

        if result.success and _normalize_trigger_text(task.trigger) == TaskTriggerType.DAILY.value:
            task.next_run = _compute_next_daily(task.daily_time, now)
        else:
            task.next_run = now + timedelta(seconds=max(1, interval))

    # ── 快照推送 ──────────────────────────────────────────────

    def _emit_snapshot(self):
        if not self._on_snapshot:
            return
        try:
            self._on_snapshot(self.snapshot())
        except Exception as exc:
            logger.debug(f"snapshot hook error: {exc}")

    # ── 跨实例偷菜任务注入 ──────────────────────────────────────

    def _poll_and_inject_steal_tasks(self):
        """轮询跨实例消息总线，将偷菜通知转化为高优先级任务"""
        if not self._cross_bus or not self._instance_id:
            return
        try:
            alerts = self._cross_bus.poll_alerts(self._instance_id)
        except Exception:
            return
        if not alerts:
            return

        for alert in alerts:
            task_name = f"steal_{alert.source_instance_id}"
            logger.info(
                f"[大小号通讯📥] 收到来自 [{alert.source_name}] 的偷菜通知: "
                f"好友[{alert.friend_name}] | 地块: {','.join(alert.plot_ids)} | "
                f"最近成熟: {alert.earliest_maturity_seconds}s"
            )
            with self._lock:
                if task_name in self._tasks:
                    # 已有偷菜任务，更新 features 中的 alert 数据
                    task = self._tasks[task_name]
                    task.features["friend_name"] = alert.friend_name
                    task.features["source_name"] = alert.source_name
                    task.next_run = datetime.now()
                    task.enabled = True
                    logger.info(f"[大小号通讯📥] 更新偷菜任务: {task_name} → 好友[{alert.friend_name}]")
                else:
                    # 动态创建偷菜任务
                    steal_task = TaskItem(
                        name=task_name,
                        enabled=True,
                        priority=5,  # 高于 main=10，仅次于 popup
                        next_run=datetime.now(),
                        success_interval=1800,  # 30分钟后可重试
                        failure_interval=300,    # 失败5分钟后重试
                        trigger=TaskTriggerType.INTERVAL.value,
                        features={
                            "friend_name": alert.friend_name,
                            "source_name": alert.source_name,
                        },
                    )
                    self._tasks[task_name] = steal_task
                    # 注册 steal runner（优先使用 _run_task_steal，其次尝试 steal）
                    if "steal" in self._runners and task_name not in self._runners:
                        self._runners[task_name] = self._runners["steal"]
                    logger.info(
                        f"[大小号通讯📥] 注入偷菜任务: {task_name} → 好友[{alert.friend_name}] "
                        f"(优先级=5)"
                    )
            self._wake_event.set()

    # ── 主循环（移植自 copilot） ──────────────────────────────

    def _loop(self):
        """后台线程主循环"""
        self._emit_snapshot()
        try:
            while not self._stop_event.is_set():
                # 暂停态只保活线程
                if self._pause_event.is_set():
                    # pause_event.set() 表示不暂停（复用 copilot 语义）
                    pass
                else:
                    time.sleep(0.08)
                    continue

                # 跨实例通讯：轮询偷菜通知并注入任务
                self._poll_and_inject_steal_tasks()

                now = datetime.now()
                with self._lock:
                    snap = self._snapshot_locked(now)
                    task = snap.pending_tasks[0] if snap.pending_tasks else None
                    if task:
                        self._running_task = task.name
                    else:
                        self._running_task = None

                self._emit_snapshot()

                # 时间范围过滤：不在时段则推迟到下一个时段起点
                if task and not _is_in_time_range(now, task.enabled_time_range):
                    with self._lock:
                        item = self._tasks.get(task.name)
                        if item:
                            next_start = _next_enabled_time_start(item, now)
                            item.next_run = max(now + timedelta(seconds=1), next_start)
                            logger.debug(
                                f"任务 {item.name} 不在启用时段({item.enabled_time_range})，"
                                f"推迟到 {item.next_run.strftime('%H:%M:%S')}"
                            )
                        self._running_task = None
                    self._emit_snapshot()
                    time.sleep(0.03)
                    continue

                if not task:
                    # 空队列策略
                    policy = str(getattr(self._executor_cfg, "empty_queue_policy", "stay"))
                    if (
                        policy == "goto_main"
                        and self._on_idle
                        and time.time() - self._last_idle_at > 2.0
                    ):
                        self._last_idle_at = time.time()
                        try:
                            self._on_idle()
                        except Exception as exc:
                            logger.debug(f"idle hook error: {exc}")
                    time.sleep(0.12)
                    continue

                runner = self._runners.get(task.name)
                if not runner:
                    with self._lock:
                        item = self._tasks.get(task.name)
                        if item:
                            self._apply_task_result(
                                item,
                                TaskResult(success=False, error=f"未注册 runner: {task.name}"),
                            )
                            self._running_task = None
                    self._emit_snapshot()
                    continue

                # 执行任务
                ctx = TaskContext(task_name=task.name, started_at=now)
                try:
                    result = runner(ctx)
                    if not isinstance(result, TaskResult):
                        result = TaskResult(success=False, error=f"runner 返回无效结果: {type(result)}")
                except Exception as e:
                    logger.exception(f"任务 {task.name} 异常: {e}")
                    if self._on_task_error:
                        try:
                            self._on_task_error(task.name, str(e))
                        except Exception:
                            pass
                    result = TaskResult(success=False, error=str(e))

                with self._lock:
                    item = self._tasks.get(task.name)
                    if item:
                        item.last_run = now
                        self._apply_task_result(item, result)
                    self._running_task = None

                if result.success:
                    if self._on_task_done:
                        try:
                            self._on_task_done(task.name, result)
                        except Exception as exc:
                            logger.debug(f"task done hook error: {exc}")
                else:
                    if self._on_task_error:
                        try:
                            self._on_task_error(task.name, result.error or "任务失败")
                        except Exception:
                            pass

                self._emit_snapshot()
                time.sleep(0.03)
        finally:
            with self._lock:
                self._running_task = None
                current = threading.current_thread()
                if self._thread is current:
                    self._thread = None
            self._emit_snapshot()

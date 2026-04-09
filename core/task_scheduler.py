"""任务调度器 - 管理自动化任务的执行周期"""
import time
from datetime import datetime
from enum import Enum
from loguru import logger

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class BotState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ANALYZING = "analyzing"
    EXECUTING = "executing"
    WAITING = "waiting"
    ERROR = "error"


class TaskScheduler(QObject):
    """基于QTimer的任务调度器，与Qt事件循环集成"""

    state_changed = pyqtSignal(str)  # 状态变化信号
    farm_check_triggered = pyqtSignal()  # 农场检查触发
    friend_check_triggered = pyqtSignal()  # 好友检查触发
    stats_updated = pyqtSignal(dict)  # 统计数据更新
    window_lost = pyqtSignal()  # 游戏窗口丢失信号

    def __init__(self):
        super().__init__()
        self._state = BotState.IDLE
        self._farm_timer = QTimer(self)
        self._friend_timer = QTimer(self)
        self._window_monitor_timer = QTimer(self)
        self._farm_timer.timeout.connect(self._on_farm_timer)
        self._friend_timer.timeout.connect(self._on_friend_timer)
        self._window_monitor_timer.timeout.connect(self._on_window_monitor)
        self._window_monitor_interval_ms = 5000  # 每5秒检查一次
        self._window_check_fn = None  # 外部注入的窗口检查函数
        self._window_monitor_paused = False  # 窗口监控暂停标志
        self._remote_login_cooldown_until = 0.0  # 异地登录冷却截止时间戳

        # 统计
        self._start_time: float = 0
        self._stats = {
            "harvest": 0, "plant": 0, "water": 0,
            "weed": 0, "bug": 0, "steal": 0,
            "sell": 0, "fertilize": 0, "total_actions": 0,
        }
        self._next_farm_check: float = 0
        self._next_friend_check: float = 0

    @property
    def state(self) -> BotState:
        return self._state

    def _set_state(self, state: BotState):
        self._state = state
        self.state_changed.emit(state.value)

    def start(self, farm_interval_ms: int = 300000,
              friend_interval_ms: int = 1800000):
        """启动调度器"""
        if self._state == BotState.RUNNING:
            return
        self._start_time = time.time()
        self._set_state(BotState.RUNNING)

        # 立即执行一次农场检查
        self._farm_timer.start(farm_interval_ms)
        self._friend_timer.start(friend_interval_ms)
        self._next_farm_check = time.time()
        self._next_friend_check = time.time() + friend_interval_ms / 1000

        # 首次立即触发农场检查
        QTimer.singleShot(500, self._on_farm_timer)

        # 首次好友检查：设置 next_friend_check 为当前时间，
        # 等农场首轮完成后由 _on_task_finished 补触发
        self._next_friend_check = time.time()
        QTimer.singleShot(10000, self._on_friend_timer)

        # 启动窗口监控
        self._window_monitor_timer.start(self._window_monitor_interval_ms)

        logger.info(f"调度器已启动 (农场:{farm_interval_ms//1000}s, 好友:{friend_interval_ms//1000}s)")

    def stop(self):
        """停止调度器"""
        self._farm_timer.stop()
        self._friend_timer.stop()
        self._set_state(BotState.IDLE)
        logger.info("调度器已停止")

    def pause(self):
        """暂停"""
        if self._state == BotState.RUNNING:
            self._farm_timer.stop()
            self._friend_timer.stop()
            self._window_monitor_timer.stop()
            self._set_state(BotState.PAUSED)
            logger.info("调度器已暂停")

    def resume(self):
        """恢复"""
        if self._state == BotState.PAUSED:
            self._farm_timer.start()
            self._friend_timer.start()
            self._window_monitor_timer.start(self._window_monitor_interval_ms)
            self._set_state(BotState.RUNNING)
            logger.info("调度器已恢复")

    def run_once(self):
        """手动触发一次农场检查"""
        logger.info("手动触发农场检查")
        self.farm_check_triggered.emit()

    def run_friend_once(self):
        """手动触发一次好友检查"""
        logger.info("手动触发好友巡查")
        self.friend_check_triggered.emit()

    def set_farm_interval(self, seconds: int):
        """动态调整农场检查间隔（秒）"""
        ms = max(3000, seconds * 1000)
        self._farm_timer.setInterval(ms)
        self._next_farm_check = time.time() + seconds
        if seconds >= 60:
            logger.info(f"农场检查间隔调整为 {seconds // 60}分{seconds % 60}秒")
        else:
            logger.info(f"农场检查间隔调整为 {seconds}秒")

    def _on_farm_timer(self):
        if self._state not in (BotState.RUNNING,):
            return
        self._next_farm_check = time.time() + self._farm_timer.interval() / 1000
        self.farm_check_triggered.emit()

    def _on_friend_timer(self):
        if self._state not in (BotState.RUNNING,):
            return
        self._next_friend_check = time.time() + self._friend_timer.interval() / 1000
        self.friend_check_triggered.emit()

    def set_window_check_fn(self, fn):
        """设置窗口检查函数，返回 True 表示窗口存在"""
        self._window_check_fn = fn

    def _on_window_monitor(self):
        """定期检查游戏窗口是否存在"""
        if self._state != BotState.RUNNING:
            return
        if self._window_monitor_paused:
            return
        if time.time() < self._remote_login_cooldown_until:
            return  # 异地登录冷却期间，禁止窗口监控触发
        if self._window_check_fn and not self._window_check_fn():
            logger.warning("窗口监控：游戏窗口已丢失")
            self.window_lost.emit()

    def set_remote_login_cooldown(self, seconds: int):
        """设置异地登录冷却时间，期间窗口监控不会触发"""
        self._remote_login_cooldown_until = time.time() + seconds
        logger.info(f"窗口监控冷却 {seconds}s（异地登录等待中）")

    def pause_window_monitor(self):
        """暂停窗口监控（用于异地登录等待等场景）"""
        self._window_monitor_paused = True
        logger.info("窗口监控已暂停")

    def resume_window_monitor(self):
        """恢复窗口监控"""
        self._window_monitor_paused = False
        logger.info("窗口监控已恢复")

    def record_action(self, action_type: str, count: int = 1):
        """记录操作统计"""
        if action_type in self._stats:
            self._stats[action_type] += count
        self._stats["total_actions"] += count
        self.stats_updated.emit(self.get_stats())

    def get_stats(self) -> dict:
        """获取统计数据"""
        elapsed = time.time() - self._start_time if self._start_time else 0
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        return {
            **self._stats,
            "elapsed": f"{hours}小时{minutes}分",
            "next_farm_check": datetime.fromtimestamp(self._next_farm_check).strftime("%H:%M:%S") if self._next_farm_check else "--",
            "next_friend_check": datetime.fromtimestamp(self._next_friend_check).strftime("%H:%M:%S") if self._next_friend_check else "--",
            "state": self._state.value,
        }

    def reset_stats(self):
        for key in self._stats:
            self._stats[key] = 0

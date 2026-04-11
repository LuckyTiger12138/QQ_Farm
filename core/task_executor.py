"""统一任务执行器 - 基于优先级的任务调度

参考 qq-farm-copilot 的设计，将各个策略（收获、维护、播种等）抽象为任务项。
每轮循环根据优先级排序，执行第一个满足条件的任务。
"""
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass
from loguru import logger


@dataclass
class TaskItem:
    """任务项"""
    name: str                       # 任务名称
    priority: int                   # 优先级（数字越小越高）
    enabled_fn: Optional[Callable[[], bool]] = None  # 是否启用检查
    check_fn: Optional[Callable[[Dict], bool]] = None  # 是否可执行检查 (接收 detections/context)
    run_fn: Optional[Callable[[Dict], str | None]] = None  # 执行函数，返回操作描述或 None
    cooldown_fn: Optional[Callable[[], bool]] = None # 是否处于冷却中


class TaskExecutor:
    """任务执行器"""

    def __init__(self):
        self._tasks: list[TaskItem] = []
        self._current_task_name: str = ""

    def add_task(self, task: TaskItem):
        """添加任务"""
        self._tasks.append(task)
        # 按优先级排序
        self._tasks.sort(key=lambda t: t.priority)
        logger.debug(f"已添加任务: {task.name} (优先级: {task.priority})")

    def execute(self, context: Dict[str, Any] = None) -> str | None:
        """
        执行一轮任务调度
        :param context: 上下文数据（如 detections, scene 等）
        :return: 操作描述字符串，如果无任务执行则返回 None
        """
        if context is None:
            context = {}

        # 1. 筛选出启用的任务
        enabled_tasks = []
        for task in self._tasks:
            if task.enabled_fn and not task.enabled_fn():
                continue
            if task.cooldown_fn and task.cooldown_fn():
                continue
            enabled_tasks.append(task)

        # 2. 按优先级顺序尝试执行任务，直到有一个成功
        for task in enabled_tasks:
            if task.check_fn and not task.check_fn(context):
                continue

            self._current_task_name = task.name
            logger.debug(f"执行任务: {task.name} (优先级: {task.priority})")

            if task.run_fn:
                result = task.run_fn(context)
                if result:  # 任务实际执行了操作
                    return result
                # 如果返回 None，继续尝试下一个优先级的任务

        self._current_task_name = ""
        return None

    @property
    def current_task_name(self) -> str:
        return self._current_task_name

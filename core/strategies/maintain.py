"""P1 维护 — 一键除草/除虫/浇水（统一循环）"""
import time
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy, SCALES_FAST

_MAINTAIN_BUTTONS = [
    ("btn_weed", "auto_weed", ActionType.WEED),
    ("btn_bug", "auto_bug", ActionType.BUG),
    ("btn_water", "auto_water", ActionType.WATER),
]

_CONFIRM_TIMEOUT = 0.5
_CONFIRM_MIN_ROUNDS = 2


class _SimpleTimer:
    __slots__ = ("_limit", "_count", "_start_time")

    def __init__(self, limit: float, count: int):
        self._limit = limit
        self._count = count
        self._start_time: float = 0.0

    def start(self):
        self._start_time = time.monotonic()

    def clear(self):
        self._start_time = 0.0

    @property
    def started(self) -> bool:
        return self._start_time > 0.0

    @property
    def reached(self) -> bool:
        return self.started and (time.monotonic() - self._start_time) >= self._limit


class MaintainStrategy(BaseStrategy):
    """维护策略：一键除草/除虫/浇水（统一循环，共享确认计时器）

    对齐 copilot 优化：将 3 个独立操作合并为 1 个循环，
    一次截图检测所有按钮，共享确认计时器，减少截图次数和等待时间。
    """

    def try_maintain(self, detections: list[DetectResult],
                     features: dict) -> str | None:
        if self.stopped:
            return None
        for btn_name, feature_key, action_type in _MAINTAIN_BUTTONS:
            if self.stopped:
                return None
            if not features.get(feature_key, True):
                continue
            btn = self.find_by_name(detections, btn_name)
            if btn:
                self.click(btn.x, btn.y, f"一键{btn_name.replace('btn_', '')}", action_type)
                return btn_name
        return None

    def try_maintain_direct(self, rect: tuple, features: dict) -> str | None:
        """统一维护循环：一次截图检测所有按钮，共享确认计时器。"""
        if self.stopped:
            return None

        action_specs = [
            (btn, ft, at)
            for btn, ft, at in _MAINTAIN_BUTTONS
            if features.get(ft, True)
        ]
        if not action_specs:
            return None
        target_names = [s[0] for s in action_specs]

        cv_img, dets = self.quick_detect(rect, target_names, scales=SCALES_FAST)
        if cv_img is None:
            return None

        found = [s for s in action_specs if self.find_by_name(dets, s[0])]
        if not found:
            return None

        logger.info(
            "一键维护流程: 开始 | 除草={} 除虫={} 浇水={}",
            features.get("auto_weed", True),
            features.get("auto_bug", True),
            features.get("auto_water", True),
        )

        timer = _SimpleTimer(_CONFIRM_TIMEOUT, _CONFIRM_MIN_ROUNDS)
        rounds_stable = 0

        while not self.stopped:
            cv_img, dets = self.quick_detect(rect, target_names, scales=SCALES_FAST)
            if cv_img is None:
                time.sleep(0.15)
                continue

            clicked = False
            for btn_name, _ft, action_type in action_specs:
                btn = self.find_by_name(dets, btn_name)
                if btn:
                    self.click(btn.x, btn.y, f"一键{btn_name.replace('btn_', '')}", action_type)
                    clicked = True
                    break

            if clicked:
                timer.clear()
                rounds_stable = 0
                time.sleep(0.3)
                continue

            any_left = any(self.find_by_name(dets, s[0]) for s in action_specs)
            if not any_left:
                if not timer.started:
                    timer.start()
                rounds_stable += 1
                if timer.reached and rounds_stable >= _CONFIRM_MIN_ROUNDS:
                    logger.info("一键维护流程: 完成")
                    return "一键维护"
            else:
                timer.clear()
                rounds_stable = 0

            time.sleep(0.15)

        return None

"""P1 维护 — 一键务农"""
import time
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy, SCALES_FAST

_MAINTAIN_BTN = "btn_一键务农"
_MAINTAIN_FEATURE = "auto_maintain"
_CONFIRM_TIMEOUT = 0.5
_CONFIRM_MIN_ROUNDS = 2


class _SimpleTimer:
    __slots__ = ("_limit", "_start_time")

    def __init__(self, limit: float):
        self._limit = limit
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
    """维护策略：一键务农"""

    def try_maintain(self, detections: list[DetectResult],
                     features: dict) -> str | None:
        if self.stopped:
            return None
        if not features.get(_MAINTAIN_FEATURE, True):
            return None
        btn = self.find_by_name(detections, _MAINTAIN_BTN)
        if btn:
            self.click(btn.x, btn.y, "一键务农", ActionType.MAINTAIN)
            return _MAINTAIN_BTN
        return None

    def try_maintain_direct(self, rect: tuple, features: dict) -> str | None:
        """一键务农：检测并点击 btn_一键务农，等待消失确认。"""
        if self.stopped:
            return None

        if not features.get(_MAINTAIN_FEATURE, True):
            return None

        cv_img, dets = self.quick_detect(rect, [_MAINTAIN_BTN], scales=SCALES_FAST)
        if cv_img is None:
            return None

        btn = self.find_by_name(dets, _MAINTAIN_BTN)
        if not btn:
            return None

        logger.info("一键务农流程: 开始")

        timer = _SimpleTimer(_CONFIRM_TIMEOUT)
        rounds_stable = 0

        while not self.stopped:
            cv_img, dets = self.quick_detect(rect, [_MAINTAIN_BTN], scales=SCALES_FAST)
            if cv_img is None:
                time.sleep(0.15)
                continue

            btn = self.find_by_name(dets, _MAINTAIN_BTN)
            if btn:
                self.click(btn.x, btn.y, "一键务农", ActionType.MAINTAIN)
                timer.clear()
                rounds_stable = 0
                time.sleep(0.3)
                continue

            if not timer.started:
                timer.start()
            rounds_stable += 1
            if timer.reached and rounds_stable >= _CONFIRM_MIN_ROUNDS:
                logger.info("一键务农流程: 完成")
                return "一键务农"

            time.sleep(0.15)

        return None

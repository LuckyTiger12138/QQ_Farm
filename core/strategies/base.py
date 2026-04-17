"""策略基类 — 公共方法"""
import time
from loguru import logger

from models.farm_state import Action, ActionType
from core.cv_detector import CVDetector, DetectResult


class BaseStrategy:
    def __init__(self, cv_detector: CVDetector):
        self.cv_detector = cv_detector
        self.action_executor = None
        self._capture_fn = None
        self._stop_requested = False

    def set_capture_fn(self, fn):
        self._capture_fn = fn

    @property
    def stopped(self) -> bool:
        return self._stop_requested

    def capture(self, rect: tuple):
        if self._capture_fn:
            return self._capture_fn(rect, save=False)
        return None, [], None

    def click(self, x: int, y: int, desc: str = "",
              action_type: str = ActionType.NAVIGATE) -> bool:
        if not self.action_executor or self._stop_requested:
            return False
        action = Action(type=action_type, click_position={"x": x, "y": y},
                        priority=0, description=desc)
        result = self.action_executor.execute_action(action)
        if result.success:
            logger.info(f"✓ {desc}")
        else:
            logger.warning(f"✗ {desc}: {result.message}")
        return result.success

    def find_by_name(self, detections: list[DetectResult], name: str) -> DetectResult | None:
        for d in detections:
            if d.name == name:
                return d
        return None

    def find_by_prefix_first(self, detections: list[DetectResult], prefix: str) -> DetectResult | None:
        for d in detections:
            if d.name.startswith(prefix):
                return d
        return None

    def find_any(self, detections: list[DetectResult], names: list[str]) -> DetectResult | None:
        name_set = set(names)
        for d in detections:
            if d.name in name_set:
                return d
        return None

    def click_blank(self, rect: tuple):
        """点击天空区域关闭弹窗"""
        w, h = rect[2], rect[3]
        # X 轴 +5% 错开，避免误触个人信息按钮
        x, y = int(w * 0.55), int(h * 0.15)
        self.click(x, y, "点击空白处")

    def pinch_zoom_out(self, rect: tuple, steps: int = 3) -> bool:
        """在窗口中心执行缩小操作（Ctrl+鼠标滚轮），用于空地检测前缩小视野

        Args:
            rect: 窗口区域 (left, top, width, height)
            steps: 滚轮步数

        Returns:
            是否成功
        """
        if not self.action_executor or self._stop_requested:
            logger.warning("缩小视野跳过：执行器未就绪或已停止")
            return False
        # 缩放中心为窗口中心
        cx = rect[2] // 2
        cy = rect[3] // 2
        result = self.action_executor.pinch_zoom(cx, cy, zoom_out=True, steps=steps)
        if result:
            logger.info(f"✓ 缩小视野完成 ({steps}步)，等待缩放动画")
            time.sleep(0.8)  # 等待缩放动画完成
        else:
            logger.warning("✗ 缩小视野失败")
        return result

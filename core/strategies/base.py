"""策略基类 — 公共方法"""
import os
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
        # 截图标记点击位置
        import cv2
        import numpy as np
        from PIL import Image as PILImage
        from core.screen_capture import ScreenCapture
        sc = ScreenCapture()
        pil_img = sc.capture_region(rect)
        if pil_img is not None:
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            # 画红点标记点击位置
            cv2.circle(cv_img, (x, y), 10, (0, 0, 255), -1)
            # 保存截图
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"blank_click_{ts}.png"
            filepath = os.path.join(sc._save_dir, filename)
            pil_img_marked = PILImage.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))
            from utils.image_utils import save_screenshot
            save_screenshot(pil_img_marked, filepath)
            logger.info(f"点击空白处位置：({x}, {y})，截图已保存：{filepath}")

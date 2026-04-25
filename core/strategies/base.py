"""策略基类 — 公共方法"""
import time
from loguru import logger

from models.farm_state import Action, ActionType
from core.cv_detector import CVDetector, DetectResult


# 默认精简尺度集合（只检测 3 个尺度，比全量检测 5 个尺度更快）
SCALES_FAST = [1.0, 0.9, 1.1]


class BaseStrategy:
    def __init__(self, cv_detector: CVDetector):
        self.cv_detector = cv_detector
        self.action_executor = None
        self._capture_fn = None
        self._stop_requested = False
        self.navigator = None
        self._click_last: dict[str, float] = {}  # 按钮名 → 上次点击时间，防重复点击

    def set_capture_fn(self, fn):
        self._capture_fn = fn

    @property
    def stopped(self) -> bool:
        return self._stop_requested

    def capture(self, rect: tuple):
        if self._capture_fn:
            return self._capture_fn(rect, save=False)
        return None, [], None

    def quick_capture(self, rect: tuple):
        """快速截屏，只返回 cv_img"""
        if self._capture_fn:
            cv_img, _, _ = self._capture_fn(rect, save=False)
            return cv_img
        return None

    def quick_detect(self, rect: tuple, names: list[str],
                     thresholds: dict[str, float] | None = None,
                     scales: list[float] | None = None,
                     roi_map: dict[str, tuple[int, int, int, int]] | None = None) -> tuple:
        """快速截屏 + 按需检测指定模板
        
        Args:
            rect: 窗口区域
            names: 要检测的模板名列表
            thresholds: 单模板阈值覆盖
            scales: 自定义尺度集合，默认 [1.0, 0.9, 1.1]
            roi_map: ROI 区域映射 {template_name: (x1, y1, x2, y2)}
        
        Returns:
            tuple: (cv_img, detections)
        """
        cv_img = self.quick_capture(rect)
        if cv_img is None:
            return None, []
        
        detections = self.cv_detector.detect_targeted(
            cv_img,
            names=names,
            thresholds=thresholds,
            scales=scales or SCALES_FAST,
            roi_map=roi_map
        )
        return cv_img, detections

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

    def _click_interval_ok(self, key: str, interval: float) -> bool:
        """检查按钮点击间隔是否满足，防重复点击（移植自 copilot appear_then_click 模式）"""
        import time
        last = self._click_last.get(key, 0.0)
        return (time.time() - last) >= interval

    def _click_interval_hit(self, key: str) -> None:
        """记录按钮点击时间"""
        import time
        self._click_last[key] = time.time()

    def click_with_interval(self, detections: list[DetectResult], name: str,
                            desc: str = "", interval: float = 1.0,
                            action_type: str = ActionType.NAVIGATE) -> bool:
        """检测到目标后点击，带间隔防重复（移植自 copilot appear_then_click）

        Args:
            detections: 当前帧检测结果
            name: 模板名称
            desc: 操作描述
            interval: 最小点击间隔（秒）
            action_type: 动作类型

        Returns:
            是否成功点击
        """
        if not self._click_interval_ok(name, interval):
            return False
        target = self.find_by_name(detections, name)
        if not target:
            return False
        ok = self.click(target.x, target.y, desc or name, action_type)
        if ok:
            self._click_interval_hit(name)
        return ok

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

"""P0 收益 — 一键收获 + 特殊作物成熟图标收获"""
import time
from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy, SCALES_FAST


class HarvestStrategy(BaseStrategy):
    """收获策略
    
    优先使用一键收获按钮（btn_harvest）收获普通作物；
    无一键收获时，通过点击成熟图标（icon_mature）逐个收获特殊作物。

    性能优化：使用 detect_targeted() 只检测所需模板（2 个）
    而不是全量扫描 100+ 模板。每个尺度减少 40% 检测次数。
    """

    def try_harvest(self, detections: list[DetectResult]) -> str | None:
        """从已有检测结果中查找并点击收获（兼容旧接口）"""
        if self.stopped:
            return None
        btn = self.find_by_name(detections, "btn_harvest")
        if btn:
            self.click(btn.x, btn.y, "一键收获", ActionType.HARVEST)
            return "一键收获"
        return self._harvest_mature_icons(detections)

    def try_harvest_direct(self, rect: tuple) -> str | None:
        """快速检测并点击收获（不依赖全量检测）

        同时检测 btn_harvest 和 icon_mature：
        - btn_harvest 命中 → 一键收获（普通作物）
        - icon_mature 命中 → 逐个点击收获特殊作物

        Args:
            rect: 窗口区域

        Returns:
            str | None: 操作描述，未找到返回 None
        """
        if self.stopped:
            return None

        cv_img, dets = self.quick_detect(
            rect, ["btn_harvest", "icon_mature"], scales=SCALES_FAST
        )
        if cv_img is None:
            return None

        btn = self.find_by_name(dets, "btn_harvest")
        if btn:
            self.click(btn.x, btn.y, "一键收获", ActionType.HARVEST)
            return "一键收获"

        return self._harvest_mature_icons(dets)

    def _harvest_mature_icons(
        self, detections: list[DetectResult]
    ) -> str | None:
        """点击成熟图标收获特殊作物（无一键收获按钮时使用）

        遍历所有 icon_mature 命中点逐个点击，间隔 0.3 秒。

        Args:
            detections: 检测结果列表

        Returns:
            str | None: 操作描述，未找到返回 None
        """
        mature_icons = [d for d in detections if d.name == "icon_mature"]
        if not mature_icons:
            return None

        clicked = 0
        for icon in mature_icons:
            if self.stopped:
                break
            if self.click(icon.x, icon.y, "收获特殊作物", ActionType.HARVEST):
                clicked += 1
            time.sleep(0.3)

        if clicked > 0:
            return f"特殊作物收获x{clicked}"
        return None

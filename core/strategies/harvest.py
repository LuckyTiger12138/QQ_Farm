"""P0 收益 — 一键收获 + 特殊作物成熟图标收获"""
import time
from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy, SCALES_FAST

_HARVEST_ICON_NAMES = ("icon_mature", "icon_caiji")


class HarvestStrategy(BaseStrategy):
    """收获策略
    
    优先使用一键收获按钮（btn_harvest）收获普通作物；
    无一键收获时，通过点击成熟图标（icon_mature / icon_caiji）逐个收获特殊作物。
    一键收获后若有残留 icon_caiji，再逐个点击。
    """

    def try_harvest(self, detections: list[DetectResult]) -> str | None:
        """从已有检测结果中查找并点击收获（兼容旧接口）"""
        if self.stopped:
            return None
        btn = self.find_by_name(detections, "btn_harvest")
        if btn:
            self.click(btn.x, btn.y, "一键收获", ActionType.HARVEST)
            desc = "一键收获"
            # 一键收获后检查残留 icon_caiji
            extra = self._harvest_remaining_icons(detections)
            if extra:
                desc = f"{desc} + {extra}"
            return desc
        return self._harvest_mature_icons(detections)

    def try_harvest_direct(self, rect: tuple) -> str | None:
        """快速检测并点击收获（不依赖全量检测）

        同时检测 btn_harvest、icon_mature、icon_caiji：
        - btn_harvest 命中 → 一键收获（普通作物），收获后复查残留 icon_caiji
        - icon_mature / icon_caiji 命中 → 逐个点击收获特殊作物
        """
        if self.stopped:
            return None

        cv_img, dets = self.quick_detect(
            rect, ["btn_harvest"] + list(_HARVEST_ICON_NAMES), scales=SCALES_FAST
        )
        if cv_img is None:
            return None

        btn = self.find_by_name(dets, "btn_harvest")
        if btn:
            self.click(btn.x, btn.y, "一键收获", ActionType.HARVEST)
            desc = "一键收获"
            # 一键收获后等待并复查残留 icon_caiji
            time.sleep(0.5)
            extra = self._harvest_remaining_icons(rect)
            if extra:
                desc = f"{desc} + {extra}"
            return desc

        return self._harvest_mature_icons(dets)

    def _harvest_mature_icons(
        self, detections: list[DetectResult]
    ) -> str | None:
        """点击成熟图标收获特殊作物（无一键收获按钮时使用）

        匹配 icon_mature 和 icon_caiji，遍历所有命中点逐个点击。
        """
        icons = [d for d in detections if d.name in _HARVEST_ICON_NAMES]
        if not icons:
            return None

        clicked = 0
        for icon in icons:
            if self.stopped:
                break
            label = "采集特殊作物" if icon.name == "icon_caiji" else "收获特殊作物"
            if self.click(icon.x, icon.y, label, ActionType.HARVEST):
                clicked += 1
            time.sleep(0.3)

        if clicked > 0:
            return f"特殊作物收获x{clicked}"
        return None

    def _harvest_remaining_icons(self, rect_or_dets) -> str | None:
        """一键收获后复查残留 icon_caiji / icon_mature"""
        if self.stopped:
            return None
        if isinstance(rect_or_dets, tuple):
            cv_img, dets = self.quick_detect(
                rect_or_dets, list(_HARVEST_ICON_NAMES), scales=SCALES_FAST
            )
            if cv_img is None:
                return None
        else:
            dets = rect_or_dets

        icons = [d for d in dets if d.name in _HARVEST_ICON_NAMES]
        if not icons:
            return None

        clicked = 0
        for icon in icons:
            if self.stopped:
                break
            label = "采集特殊作物" if icon.name == "icon_caiji" else "收获特殊作物"
            if self.click(icon.x, icon.y, label, ActionType.HARVEST):
                clicked += 1
            time.sleep(0.3)

        if clicked > 0:
            return f"残留特殊作物x{clicked}"
        return None

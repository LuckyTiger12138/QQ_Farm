"""P0 收益 — 一键收获"""
from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy, SCALES_FAST


class HarvestStrategy(BaseStrategy):
    """一键收获策略
    
    性能优化：使用 detect_targeted() 只检测 btn_harvest 模板（1 个）
    而不是全量扫描 100+ 模板。每个尺度减少 40% 检测次数。
    """

    def try_harvest(self, detections: list[DetectResult]) -> str | None:
        """从已有检测结果中查找并点击一键收获（兼容旧接口）"""
        if self.stopped:
            return None
        btn = self.find_by_name(detections, "btn_harvest")
        if btn:
            self.click(btn.x, btn.y, "一键收获", ActionType.HARVEST)
            return "一键收获"
        return None

    def try_harvest_direct(self, rect: tuple) -> str | None:
        """快速检测并点击一键收获（不依赖全量检测）
        
        只检测 btn_harvest 模板，使用精简尺度集合 [1.0, 0.9, 1.1]
        
        Args:
            rect: 窗口区域
        
        Returns:
            str | None: 操作描述，未找到返回 None
        """
        if self.stopped:
            return None

        cv_img, dets = self.quick_detect(rect, ["btn_harvest"], scales=SCALES_FAST)
        if cv_img is None:
            return None

        btn = self.find_by_name(dets, "btn_harvest")
        if btn:
            self.click(btn.x, btn.y, "一键收获", ActionType.HARVEST)
            return "一键收获"
        return None

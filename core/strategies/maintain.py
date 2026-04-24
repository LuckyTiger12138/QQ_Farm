"""P1 维护 — 一键除草/除虫/浇水"""
from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy, SCALES_FAST


class MaintainStrategy(BaseStrategy):
    """维护策略：一键除草/除虫/浇水
    
    性能优化：使用 detect_targeted() 只检测启用的按钮模板（最多 3 个）
    而不是全量扫描 100+ 模板。
    """

    def try_maintain(self, detections: list[DetectResult],
                     features: dict) -> str | None:
        """从已有检测结果中查找并点击维护按钮（兼容旧接口）"""
        if self.stopped:
            return None
        buttons = [
            ("btn_weed", "一键除草", "auto_weed", ActionType.WEED),
            ("btn_bug", "一键除虫", "auto_bug", ActionType.BUG),
            ("btn_water", "一键浇水", "auto_water", ActionType.WATER),
        ]
        for btn_name, desc, feature_key, action_type in buttons:
            if self.stopped:
                return None
            if not features.get(feature_key, True):
                continue
            btn = self.find_by_name(detections, btn_name)
            if btn:
                self.click(btn.x, btn.y, desc, action_type)
                return desc
        return None

    def try_maintain_direct(self, rect: tuple, features: dict) -> str | None:
        """快速检测并点击维护按钮（不依赖全量检测）
        
        只检测启用的按钮模板（1-3 个），使用精简尺度集合
        
        Args:
            rect: 窗口区域
            features: 功能开关字典 {"auto_weed": True, "auto_bug": True, "auto_water": True}
        
        Returns:
            str | None: 操作描述，未找到返回 None
        """
        if self.stopped:
            return None

        # 根据开关构建需要检测的模板名列表
        target_names = []
        for btn_name, desc, feature_key, action_type in [
            ("btn_weed", "一键除草", "auto_weed", ActionType.WEED),
            ("btn_bug", "一键除虫", "auto_bug", ActionType.BUG),
            ("btn_water", "一键浇水", "auto_water", ActionType.WATER),
        ]:
            if features.get(feature_key, True):
                target_names.append(btn_name)

        if not target_names:
            return None

        # 按需检测：只检测启用的按钮（1-3 个）
        cv_img, dets = self.quick_detect(rect, target_names, scales=SCALES_FAST)
        if cv_img is None:
            return None

        # 按优先级查找
        for btn_name, desc, feature_key, action_type in [
            ("btn_weed", "一键除草", "auto_weed", ActionType.WEED),
            ("btn_bug", "一键除虫", "auto_bug", ActionType.BUG),
            ("btn_water", "一键浇水", "auto_water", ActionType.WATER),
        ]:
            if features.get(feature_key, True):
                btn = self.find_by_name(dets, btn_name)
                if btn:
                    self.click(btn.x, btn.y, desc, action_type)
                    return desc
        return None

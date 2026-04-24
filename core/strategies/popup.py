"""P-1 异常处理 — 关闭弹窗/商店/任务奖励分享"""
import time
import pyautogui
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy, SCALES_FAST

# 弹窗相关模板（优先级从高到低）
POPUP_TEMPLATES = [
    "btn_share",      # 分享（拿双倍奖励）
    "btn_claim",      # 领取
    "btn_confirm",    # 确认
    "btn_close",      # 关闭
    "btn_cancel",     # 取消
]


class PopupStrategy(BaseStrategy):
    """弹窗处理策略
    
    性能优化：使用 detect_targeted() 只检测弹窗相关模板（最多 5 个）
    而不是全量扫描 100+ 模板。
    """

    def handle_popup(self, detections: list[DetectResult]) -> str | None:
        """处理弹窗：分享 (双倍奖励) > 领取 > 确认 > 关闭 > 取消（兼容旧接口）"""
        if self.stopped:
            return None
        # 优先检测分享按钮（任务奖励弹窗，拿双倍）
        share_btn = self.find_by_name(detections, "btn_share")
        if share_btn:
            return self._share_and_cancel(share_btn)

        for btn_name in ["btn_claim", "btn_confirm", "btn_close", "btn_cancel"]:
            if self.stopped:
                return None
            det = self.find_by_name(detections, btn_name)
            if det:
                label = btn_name.replace("btn_", "")
                self.click(det.x, det.y, f"关闭弹窗 ({label})", ActionType.CLOSE_POPUP)
                return f"关闭弹窗 ({label})"
        return None

    def handle_popup_direct(self, rect: tuple) -> str | None:
        """快速检测并处理弹窗（不依赖全量检测）
        
        只检测弹窗相关模板（最多 5 个），使用精简尺度集合
        
        Args:
            rect: 窗口区域
        
        Returns:
            str | None: 操作描述，未找到返回 None
        """
        if self.stopped:
            return None

        cv_img, dets = self.quick_detect(rect, POPUP_TEMPLATES, scales=SCALES_FAST)
        if cv_img is None:
            return None

        # 优先分享（拿双倍奖励）
        share_btn = self.find_by_name(dets, "btn_share")
        if share_btn:
            return self._share_and_cancel(share_btn)

        # 按优先级关闭
        for btn_name, label in [
            ("btn_claim", "领取"),
            ("btn_confirm", "确认"),
            ("btn_close", "关闭"),
            ("btn_cancel", "取消"),
        ]:
            det = self.find_by_name(dets, btn_name)
            if det:
                self.click(det.x, det.y, f"关闭弹窗 ({label})", ActionType.CLOSE_POPUP)
                return f"关闭弹窗 ({label})"
        return None

    def _share_and_cancel(self, share_btn: DetectResult) -> str:
        """点分享 → 等微信窗口弹出 → 点取消 → 回游戏，拿双倍奖励

        微信分享窗口"取消"按钮在窗口右下角，位置相对固定。
        点取消后游戏不检测是否真的分享了，直接发放双倍奖励。
        """
        self.click(share_btn.x, share_btn.y, "点击分享 (双倍奖励)", ActionType.CLOSE_POPUP)
        for _ in range(40):
            if self.stopped:
                return "停止"
            time.sleep(0.05)

        # 按 Escape 关闭微信分享窗口（比找取消按钮更可靠）
        pyautogui.press("escape")
        for _ in range(20):
            if self.stopped:
                return "停止"
            time.sleep(0.05)

        logger.info("任务奖励：分享→取消，领取双倍奖励")
        return "领取双倍任务奖励"

    def close_shop(self, rect: tuple):
        """关闭商店页面"""
        if self.stopped:
            return
        for _ in range(3):
            if self.stopped:
                return
            cv_img, dets = self.quick_detect(rect, ["btn_shop_close", "btn_close"],
                                              scales=SCALES_FAST)
            if cv_img is None:
                return
            shop_close = self.find_by_name(dets, "btn_shop_close")
            close_btn = shop_close if shop_close else self.find_by_name(dets, "btn_close")
            if close_btn:
                self.click(close_btn.x, close_btn.y, "关闭商店", ActionType.CLOSE_POPUP)
                for _ in range(6):
                    if self.stopped:
                        return
                    time.sleep(0.05)
            else:
                return

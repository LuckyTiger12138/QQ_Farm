"""P3 资源 — 扩建土地 + 领取任务"""
import time
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy


class ExpandStrategy(BaseStrategy):

    def __init__(self, cv_detector):
        super().__init__(cv_detector)
        self._expand_failed = False
        self._upgrade_failed = False  # 自动升级防重复标志

    def try_expand(self, rect: tuple, detections: list[DetectResult]) -> str | None:
        """检测可扩建并执行"""
        if self._expand_failed:
            return None
        if self.stopped:
            return None
        btn = self.find_by_name(detections, "btn_expand")
        if not btn:
            return None

        self.click(btn.x, btn.y, "点击可扩建")
        time.sleep(0.5)

        for _ in range(5):
            if self.stopped:
                return None
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return None

            confirm = self.find_by_name(dets, "btn_expand_confirm")
            if confirm:
                self.click(confirm.x, confirm.y, "直接扩建")
                time.sleep(0.5)
                self._expand_failed = False
                cv_img2, dets2, _ = self.capture(rect)
                if cv_img2 is not None:
                    close = self.find_any(dets2, ["btn_close", "btn_claim"])
                    if close:
                        self.click(close.x, close.y, "关闭扩建弹窗", ActionType.CLOSE_POPUP)
                return "直接扩建"
            time.sleep(0.3)

        self._expand_failed = True
        logger.info("扩建条件不满足，暂停扩建检测")
        return None

    def try_claim_task(self, rect: tuple) -> str | None:
        """自动领取任务奖励（待实现：需要 btn_task 模板）"""
        # TODO: 打开任务页面 → 检测可领取 → 点击领取
        return None

    def try_upgrade(self, rect: tuple, detections: list[DetectResult]) -> str | None:
        """自动升级：检测升级图标并点击升级"""
        if self._upgrade_failed:
            return None
        if self.stopped:
            return None

        levelup = self.find_by_name(detections, "icon_levelup")
        if not levelup:
            return None

        logger.info("自动升级: 检测到升级图标")
        acted = False

        for _ in range(10):
            if self.stopped:
                return None
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return None

            # 点击升级图标
            btn = self.find_by_name(dets, "icon_levelup")
            if btn:
                self.click(btn.x, btn.y, "点击升级")
                acted = True
                time.sleep(0.3)
                continue

            # 点击确认/直接领取
            confirm = self.find_any(dets, ["btn_confirm", "btn_expand_direct_confirm", "btn_claim"])
            if confirm:
                self.click(confirm.x, confirm.y, "确认升级")
                acted = True
                time.sleep(0.3)
                continue

            # 关闭弹窗
            close = self.find_any(dets, ["btn_close", "btn_info_close"])
            if close:
                self.click(close.x, close.y, "关闭升级弹窗")
                time.sleep(0.3)

            # 升级图标消失 = 完成
            if not any(d.name == "icon_levelup" for d in dets):
                self._upgrade_failed = False
                return "自动升级" if acted else None

        return "自动升级" if acted else None

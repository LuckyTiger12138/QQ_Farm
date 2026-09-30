"""P3.6 礼品 — QQSVIP礼包 + 商城免费 + 邮件领取

流程：
  1. QQSVIP 入口 → 会员礼包 → 专属商城每日免费肥料
  2. 导航到商城 → 领取免费商品
  3. 导航到菜单 → 邮件 → 一键领取

需要模板：
  btn_qqsvip                        — QQSVIP 礼包入口
  btn_svip_exclusive_mall           — SVIP 页面“专属商城”标签
  ui_svip_not_member                — 非会员页“开通SVIP会员”特征（命中则整段跳过）
  ui_svip_daily_free                 — “SVIP商城每日免费”商品卡标题
  btn_svip_daily_free                — 左侧每日免费肥料卡的“免费”按钮
  ui_svip_daily_ready / done         — 同卡片每日限购 1/1 / 0/1
  btn_mall_free / btn_mall_free_done — 商城免费商品
  ui_shangcheng / mall_check         — 商城页面确认
  menu_check / menu_goto_mail        — 菜单页面 → 邮件入口
  mail_check                         — 邮件页面确认
  btn_oneclick_open                  — 邮件一键打开
  btn_claim / btn_close              — 通用领取/关闭
"""
import os
import time
from loguru import logger

import cv2
import numpy as np

from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy, SCALES_FAST
from core.ui.page import page_main, page_mall, page_mail, ALL_PAGES


class GiftStrategy(BaseStrategy):
    """礼品领取策略：SVIP礼包 + 商城免费 + 邮件"""

    def try_gift(self, rect: tuple, detections: list[DetectResult],
                 auto_svip_gift: bool = True,
                 auto_mall_gift: bool = True,
                 auto_mail: bool = True) -> list[str]:
        """执行礼品领取流程，只执行已启用的子功能"""
        if self.stopped:
            return []

        actions = []

        # 1. QQSVIP 礼包及专属商城每日免费（也支持已打开的 SVIP 页面）
        if auto_svip_gift:
            result = self._run_qqsvip_gift(rect)
            if result:
                actions.append(result)

        # 2. 商城免费商品 — 先导航到商城页面
        if auto_mall_gift:
            result = self._run_mall_gift(rect)
            if result:
                actions.append(result)

        # 3. 邮件领取 — 先导航到菜单→邮件页面
        if auto_mail:
            result = self._run_mail_gift(rect)
            if result:
                actions.append(result)

        # 4. 回到主页
        self._navigate_back_to_main(rect)

        return actions

    def _navigate_back_to_main(self, rect: tuple):
        """确保回到主页：用精确检测查找返回按钮，避免全屏误检"""
        logger.info("礼品: 开始返回主页")
        for attempt in range(5):
            if self.stopped:
                logger.info("礼品: 收到停止信号，中断返回主页")
                return
            # 用 quick_detect 只检测页面标识和返回按钮，避免误检
            _, dets = self.quick_detect(
                rect,
                names=["ui_farm_overview", "btn_land_right", "btn_land_left",
                       "ui_shangcheng", "mall_check", "mail_check", "menu_check",
                       "mall_goto_main", "btn_shangcehng_fanhui",
                       "btn_close", "btn_shop_close"],
            )
            names = [d.name for d in dets]
            logger.debug(f"礼品: 返回主页第{attempt+1}轮, 检测到 {names}")

            # 已在主页
            if any(n in names for n in ["ui_farm_overview", "btn_land_right", "btn_land_left"]):
                logger.info("礼品: 已回到主页")
                return

            # 商城页面 → 点击返回
            back = self.find_any(dets, ["mall_goto_main", "btn_shangcehng_fanhui"])
            if back:
                self.click(back.x, back.y, "从商城返回主页")
                time.sleep(1.0)
                continue

            # 通用关闭按钮（弹窗/其他页面）
            close = self.find_any(dets, ["btn_close", "btn_shop_close"])
            if close:
                self.click(close.x, close.y, "关闭弹窗/返回")
                time.sleep(0.8)
                continue

            # 未识别，尝试关闭弹窗
            logger.debug("礼品: 未识别页面，尝试点击空白")
            self.click_blank(rect)
            time.sleep(1.0)

        logger.warning("礼品: 未能返回主页")

    def _detect_template(self, rect: tuple, name: str) -> bool:
        """快速检测单个模板是否存在"""
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return False
        return any(d.name == name for d in dets)

    def _run_qqsvip_gift(self, rect: tuple) -> str | None:
        """先检查会员礼包，再检查专属商城每日免费肥料。"""
        for _ in range(3):
            if self.stopped:
                return None
            cv_img, dets = self._svip_detect(rect, ["btn_qqsvip", "ui_svip_page"])
            if cv_img is None:
                return None
            if self.find_by_name(dets, "ui_svip_page"):
                break
            btn = self.find_by_name(dets, "btn_qqsvip")
            if not btn or self.stopped or not self.click(btn.x, btn.y, "QQSVIP入口"):
                return None
            time.sleep(0.8)
        else:
            logger.warning("礼品: 未能确认SVIP页面，停止本次领取")
            return None

        claimed = []
        # 非会员账号的会员礼包页显示“开通SVIP会员，可获得以下奖励”+“开通会员”
        # 按钮，无任何可领取项；命中该特征时整段跳过，避免误点“开通会员”。
        _, dets = self._svip_detect(rect, ["ui_svip_page", "ui_svip_not_member"])
        if self.find_by_name(dets, "ui_svip_not_member"):
            logger.info("礼品: 非SVIP会员账号，跳过SVIP礼包领取")
        else:
            if self._run_svip_member_gift(rect):
                claimed.append("QQSVIP礼包")
            if not self.stopped:
                _, dets = self._svip_detect(rect, ["ui_svip_page", "btn_svip_exclusive_mall"])
                tab = self.find_by_name(dets, "btn_svip_exclusive_mall")
                if (self.find_by_name(dets, "ui_svip_page") and tab and not self.stopped
                        and self.click(tab.x, tab.y, "打开SVIP专属商城")):
                    time.sleep(0.6)
                    if self._run_svip_exclusive_mall(rect):
                        claimed.append("SVIP专属商城每日免费肥料")
            else:
                logger.info("礼品: 未确认专属商城页签，跳过免费肥料")

        # 会员弹窗要在普通商城/邮件导航之前关闭。
        if not self.stopped:
            _, dets = self._svip_detect(rect, ["ui_svip_page", "btn_svip_close"])
            close = self.find_by_name(dets, "btn_svip_close")
            if self.find_by_name(dets, "ui_svip_page") and close and not self.stopped:
                self.click(close.x, close.y, "关闭SVIP页面")
                time.sleep(0.3)
        return "、".join(claimed) if claimed else None

    def _svip_detect(self, rect: tuple, names: list[str],
                     roi_map: dict[str, tuple[int, int, int, int]] | None = None
                     ) -> tuple[np.ndarray | None, list[DetectResult]]:
        """按需检测 SVIP 模板，兼容截图缩放，不改变全局识别阈值。"""
        thresholds = {name: 0.86 for name in names}
        for name in ("ui_svip_daily_ready", "ui_svip_daily_done"):
            thresholds[name] = 0.93
        return self.quick_detect(
            rect, names, thresholds=thresholds,
            scales=[0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3],
            roi_map=roi_map,
        )

    def _dismiss_svip_reward(self, rect: tuple) -> None:
        """只在识别到“恭喜获得”遮罩后点击其空白区。"""
        if self.stopped:
            return
        _, dets = self._svip_detect(rect, ["ui_svip_reward"])
        if self.find_by_name(dets, "ui_svip_reward") and not self.stopped:
            self.click(rect[2] // 2, int(rect[3] * 0.86), "关闭SVIP奖励提示")
            time.sleep(0.3)

    def _run_svip_member_gift(self, rect: tuple) -> bool:
        """通用领取模板仅允许在会员礼包页底部按钮区域内匹配。"""
        _, dets = self._svip_detect(
            rect, ["ui_svip_page", "ui_svip_member_rewards", "btn_svip_member_tab"],
        )
        if not self.find_by_name(dets, "ui_svip_page") or self.stopped:
            return False
        anchor = self.find_by_name(dets, "ui_svip_member_rewards")
        if not anchor:
            tab = self.find_by_name(dets, "btn_svip_member_tab")
            if not tab or self.stopped or not self.click(tab.x, tab.y, "打开SVIP会员礼包"):
                return False
            time.sleep(0.6)
            _, dets = self._svip_detect(rect, ["ui_svip_member_rewards"])
            anchor = self.find_by_name(dets, "ui_svip_member_rewards")
        if not anchor or self.stopped:
            return False
        # 2026-09 改版：anchor="今日可领取特权奖励"(w≈298)，领取按钮位于其下方 0.52w~0.88w
        roi = (int(anchor.x - anchor.w * 0.5), int(anchor.y + anchor.w * 0.52),
               int(anchor.x + anchor.w * 0.5), int(anchor.y + anchor.w * 0.88))
        _, dets = self._svip_detect(
            rect, ["btn_claim", "ui_svip_member_done"],
            roi_map={"btn_claim": roi, "ui_svip_member_done": roi},
        )
        if self.find_by_name(dets, "ui_svip_member_done"):
            logger.info("礼品: 会员礼包显示明日再来")
            return False
        claim = self.find_by_name(dets, "btn_claim")
        if not claim or self.stopped or not self.click(claim.x, claim.y, "领取SVIP会员礼包"):
            return False
        time.sleep(0.6)
        self._dismiss_svip_reward(rect)
        if self.stopped:
            return False
        _, dets = self._svip_detect(
            rect, ["ui_svip_member_done"], roi_map={"ui_svip_member_done": roi},
        )
        return self.find_by_name(dets, "ui_svip_member_done") is not None

    @staticmethod
    def _green_ratio(cv_img: np.ndarray, roi: tuple[int, int, int, int]) -> float:
        """计算 ROI 内绿色像素占比，用于判断按钮是否处于可领取状态。"""
        x1, y1, x2, y2 = roi
        patch = cv_img[max(0, y1):y2, max(0, x1):x2]
        if patch.size == 0:
            return 0.0
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, np.array([35, 100, 60]), np.array([90, 255, 255]))
        return np.count_nonzero(green) / green.size

    def _run_svip_exclusive_mall(self, rect: tuple) -> bool:
        """仅领取专属商城左侧“SVIP商城每日免费”商品。

        限购数字 1/1 与 0/1 灰度几乎一致，模板无法区分，因此改用
        “免费”按钮的绿色占比判定可领状态（可领≈0.085，已领≈0）。
        """
        if self.stopped:
            return False
        _, dets = self._svip_detect(rect, ["ui_svip_page", "ui_svip_daily_free"])
        anchor = self.find_by_name(dets, "ui_svip_daily_free")
        if not self.find_by_name(dets, "ui_svip_page") or not anchor:
            logger.info("礼品: 未确认专属商城每日免费卡片，不点击任何商品")
            return False
        # 用真实商品标题的宽度缩放卡片边界，只检测同卡片的免费按钮。
        # 2026-09 改版：anchor="SVIP商城每日免费"(w≈176)，免费按钮在 1.36w~1.78w
        left, right = int(anchor.x - anchor.w * 0.68), int(anchor.x + anchor.w * 0.68)
        button_roi = (left, int(anchor.y + anchor.w * 1.36), right, int(anchor.y + anchor.w * 1.78))
        cv_img, dets = self._svip_detect(rect, ["btn_svip_daily_free"],
                                         roi_map={"btn_svip_daily_free": button_roi})
        if cv_img is None or self.stopped:
            return False
        claim = self.find_by_name(dets, "btn_svip_daily_free")
        if not claim:
            logger.info("礼品: 未确认免费按钮，跳过")
            return False
        if self._green_ratio(cv_img, button_roi) < 0.04:
            logger.info("礼品: 免费按钮非可领取绿色状态（今日已领），跳过")
            return False
        if self.stopped or not self.click(claim.x, claim.y, "领取专属商城每日免费肥料"):
            return False
        time.sleep(0.6)
        self._dismiss_svip_reward(rect)
        # 确认领取成功：免费按钮由绿变灰
        for _ in range(3):
            if self.stopped:
                return False
            cv_img, dets = self._svip_detect(rect, ["btn_svip_daily_free"],
                                             roi_map={"btn_svip_daily_free": button_roi})
            if cv_img is None:
                return False
            if not self.find_by_name(dets, "btn_svip_daily_free"):
                time.sleep(0.3)
                continue
            if self._green_ratio(cv_img, button_roi) < 0.04:
                logger.info("✓ 专属商城每日免费肥料已领取（免费按钮已变灰）")
                return True
            time.sleep(0.3)
        logger.warning("礼品: 未确认免费按钮变灰，不报告成功，也不重复点击")
        return False

    def _run_mall_gift(self, rect: tuple) -> str | None:
        """领取商城免费商品"""
        logger.info("礼品: 检查商城免费商品")

        # 导航到商城页面
        if self.navigator:
            if not self.navigator.ui_ensure(page_mall, rect, timeout=15.0):
                logger.info("礼品: 无法到达商城页面")
                return None

        claimed = False
        loop_count = 0
        while not self.stopped:
            loop_count += 1
            # 只检测商城相关模板，避免全屏误检
            _, dets = self.quick_detect(
                rect,
                names=["btn_mall_free", "btn_mall_free_done", "btn_claim",
                       "btn_close", "btn_info_close", "btn_click_to_close",
                       "mall_check", "ui_shangcheng"],
            )
            if not dets:
                break

            names = [d.name for d in dets]
            logger.debug(f"礼品/商城 循环#{loop_count}: 检测到 {names}")

            # 检查是否全部领完
            if self.find_by_name(dets, "btn_mall_free_done"):
                logger.info("礼品: 商城免费商品已全部领取")
                break

            # 处理弹窗（如确认领取弹窗）
            close = self.find_any(dets, ["btn_click_to_close", "btn_close", "btn_info_close"])
            if close and loop_count > 1:
                # 第一个循环不关弹窗（可能是商城页面自身的关闭按钮）
                self.click(close.x, close.y, "关闭商城弹窗")
                time.sleep(0.5)
                continue

            # 点击免费领取
            free = self.find_by_name(dets, "btn_mall_free")
            if free:
                self.click(free.x, free.y, "领取商城免费商品")
                claimed = True
                time.sleep(1.0)

                # 重新截图，检查确认领取按钮
                _, dets2 = self.quick_detect(
                    rect,
                    names=["btn_claim", "btn_click_to_close", "btn_close",
                           "btn_mall_free", "btn_mall_free_done"],
                )
                if dets2:
                    claim = self.find_by_name(dets2, "btn_claim")
                    if claim:
                        self.click(claim.x, claim.y, "确认领取")
                        time.sleep(0.5)
                continue

            break

        logger.info("礼品: 商城免费流程结束")
        return "商城免费" if claimed else None

    def _run_mail_gift(self, rect: tuple) -> str | None:
        """领取邮件"""
        logger.info("礼品: 检查邮件领取")

        # 导航到邮件页面（主页→菜单→邮件）
        if self.navigator:
            if not self.navigator.ui_ensure(page_mail, rect, timeout=15.0):
                logger.info("礼品: 无法到达邮件页面")
                return None

        clicker = 0
        while not self.stopped:
            # 只检测邮件相关模板
            _, dets = self.quick_detect(
                rect,
                names=["btn_oneclick_open", "btn_close", "btn_click_to_close",
                       "mail_check"],
            )
            if not dets:
                break

            names = [d.name for d in dets]
            logger.debug(f"礼品/邮件 循环#{clicker}: 检测到 {names}")

            # 处理弹窗
            close = self.find_any(dets, ["btn_click_to_close", "btn_close"])
            if close:
                self.click(close.x, close.y, "关闭邮件弹窗")
                time.sleep(0.3)
                continue

            if clicker > 1:
                break

            # 一键打开
            open_btn = self.find_by_name(dets, "btn_oneclick_open")
            if not open_btn:
                break
            self.click(open_btn.x, open_btn.y, "一键打开邮件")
            clicker += 1
            time.sleep(0.5)

        logger.info("礼品: 邮件领取流程结束")
        return "邮件领取" if clicker > 0 else None

    @staticmethod
    def _save_debug_screenshot(cv_img, prefix: str):
        """保存调试截图到 screenshots 目录"""
        if cv_img is None:
            return
        try:
            save_dir = "screenshots/debug"
            os.makedirs(save_dir, exist_ok=True)
            ts = time.strftime("%H%M%S")
            path = os.path.join(save_dir, f"{prefix}_{ts}.png")
            # cv2.imencode 支持中文路径
            ret, buf = cv2.imencode('.png', cv_img)
            if ret:
                buf.tofile(path)
        except Exception as e:
            logger.debug(f"保存调试截图失败: {e}")

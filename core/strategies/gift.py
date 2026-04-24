"""P3.6 礼品 — QQSVIP礼包 + 商城免费 + 邮件领取

流程：
  1. 检测 QQSVIP 入口 → 领取礼包
  2. 导航到商城 → 领取免费商品
  3. 导航到菜单 → 邮件 → 一键领取

需要模板：
  btn_qqsvip                        — QQSVIP 礼包入口
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

        # 1. QQSVIP 礼包（在主页就能检测到）
        if auto_svip_gift:
            if self._detect_template(rect, "btn_qqsvip"):
                result = self._run_qqsvip_gift(rect)
                if result:
                    actions.append(result)
            else:
                logger.info("礼品: 未检测到QQSVIP入口，跳过SVIP礼包")

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
        """领取 QQSVIP 礼包"""
        logger.info("礼品: 检查QQSVIP礼包")
        claimed = False
        clicked_entry = False
        for _ in range(10):
            if self.stopped:
                return None
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return None

            # 关闭弹窗
            close = self.find_any(dets, ["btn_close", "btn_info_close"])
            if close:
                self.click(close.x, close.y, "关闭SVIP弹窗")
                time.sleep(0.3)
                continue

            # 点击领取（优先处理弹出的领取按钮）
            claim = self.find_by_name(dets, "btn_claim")
            if claim:
                self.click(claim.x, claim.y, "领取SVIP礼包")
                claimed = True
                time.sleep(0.5)
                continue

            # 如果已经点过入口但没找到领取按钮，说明已领完或弹窗已关闭
            if clicked_entry:
                logger.info("礼品: SVIP入口已点击但未找到领取按钮，流程结束")
                break

            # 点击 QQSVIP 入口
            btn = self.find_by_name(dets, "btn_qqsvip")
            if not btn:
                logger.info("礼品: 未找到QQSVIP入口按钮")
                break
            self.click(btn.x, btn.y, "QQSVIP入口")
            clicked_entry = True
            time.sleep(1.0)

        logger.info(f"礼品: QQSVIP流程结束, 已领取={claimed}")
        return "QQSVIP礼包" if claimed else None

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

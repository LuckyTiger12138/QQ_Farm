"""P3 任务 — 点击任务条，自动领取奖励或执行子任务（如售卖果实）

流程：
  检测 btn_task（左下角任务条）→ 点击 →
    - 任务奖励弹窗 → 分享领双倍 / 直接领取
    - 仓库页面（售卖任务）→ 根据配置批量或选择性出售
    - 其他弹窗 → 关闭

需要模板：
  btn_task          — 左下角任务提示条
  btn_batch_sell    — 仓库"批量出售"按钮
  btn_sell          — 仓库"出售"按钮
  shop_xx           — 仓库果实图标（复用商店模板）
"""
import time
import pyautogui
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy


class TaskStrategy(BaseStrategy):

    def __init__(self, cv_detector):
        super().__init__(cv_detector)

    def try_sell_direct(self, rect: tuple) -> list[str]:
        """直接出售入口（不依赖任务条），移植自 copilot 的独立出售流程

        检测当前场景：
        - 仓库页面 → 直接出售
        - 农场主界面 → 点击 btn_warehouse 进入仓库后出售
        - 其他 → 跳过
        """
        import time as _time

        if self.stopped:
            return []

        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return []

        names = {d.name for d in dets}

        # 已在仓库页面
        if "btn_zhongzi" in names and "btn_warehouse" in names:
            warehouse_btn = self.find_by_name(dets, "btn_warehouse")
            if warehouse_btn:
                self.click(warehouse_btn.x, warehouse_btn.y, "打开仓库")
                _time.sleep(0.8)
                return self._do_sell(rect)

        # 在农场主界面 → 点仓库按钮
        warehouse_btn = self.find_by_name(dets, "btn_warehouse")
        if warehouse_btn:
            self.click(warehouse_btn.x, warehouse_btn.y, "打开仓库")
            _time.sleep(0.8)
            return self._do_sell(rect)

        logger.info("出售: 当前场景无法直接出售，跳过")
        return []

    def _do_sell(self, rect: tuple) -> list[str]:
        """进入仓库后执行批量出售 — 只检测出售相关模板，速度快"""
        import time as _time

        sell_templates = ["btn_batch_sell", "btn_confirm", "btn_close", "btn_shop_close"]

        # 等待仓库页面加载，检测批量出售按钮
        for _ in range(5):
            if self.stopped:
                return []
            cv_img, dets = self.quick_detect(rect, sell_templates)
            if cv_img is None:
                return []
            if self.find_by_name(dets, "btn_batch_sell"):
                return self._batch_sell(rect)
            _time.sleep(0.3)

        logger.warning("出售: 进入仓库后未检测到批量出售按钮")
        self._close_page(rect)
        return []

    def try_task(self, rect: tuple, detections: list[DetectResult]) -> list[str]:
        """检测任务条并执行"""
        if self.stopped:
            return []
        btn = self.find_by_name(detections, "btn_task")
        if not btn:
            return []

        self.click(btn.x, btn.y, "点击任务")
        time.sleep(1.0)  # 等待任务弹窗或页面跳转

        return self._handle_task_result(rect)

    def _handle_task_result(self, rect: tuple) -> list[str]:
        """根据点击任务后的页面判断执行什么"""
        actions = []

        for attempt in range(5):
            if self.stopped:
                return actions
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return actions

            names = {d.name for d in dets}

            # 任务奖励弹窗 → 分享或领取（由 popup 策略处理）
            if {"btn_share", "btn_claim"} & names:
                share = self.find_by_name(dets, "btn_share")
                if share:
                    self._share_and_cancel(share)
                    actions.append("领取双倍任务奖励")
                else:
                    claim = self.find_by_name(dets, "btn_claim")
                    if claim:
                        self.click(claim.x, claim.y, "直接领取", ActionType.CLOSE_POPUP)
                        actions.append("领取任务奖励")
                time.sleep(0.5)
                return actions

            # 仓库页面（售卖任务）→ 批量出售
            batch_sell = self.cv_detector.detect_single_template(
                cv_img, "btn_batch_sell", threshold=self.cv_detector.get_template_threshold("btn_batch_sell"))
            if batch_sell:
                sell_actions = self._batch_sell(rect)
                actions.extend(sell_actions)
                return actions

            # 其他弹窗 → 关闭
            close = self.find_any(dets, ["btn_close", "btn_confirm", "btn_cancel"])
            if close:
                self.click(close.x, close.y, "关闭弹窗", ActionType.CLOSE_POPUP)
                return actions

            time.sleep(0.3)

        return actions

    def _share_and_cancel(self, share_btn: DetectResult):
        """点分享 → 按 Escape 关闭微信窗口 → 拿双倍奖励"""
        self.click(share_btn.x, share_btn.y, "点击分享(双倍奖励)", ActionType.CLOSE_POPUP)
        time.sleep(2.0)  # 等待微信分享窗口弹出
        pyautogui.press("escape")
        time.sleep(1.0)  # 等待回到游戏
        logger.info("任务: 分享→取消，领取双倍奖励")

    def _batch_sell(self, rect: tuple) -> list[str]:
        """批量出售：点批量出售 → 自动全选 → 点确认

        用 quick_detect 只检测出售相关模板，速度快。
        """
        import time as _time

        sell_templates = ["btn_batch_sell", "btn_confirm", "btn_close", "btn_shop_close"]

        logger.info("出售流程: 批量出售")
        batch_clicked = False
        max_wait = 10.0  # 最长等待 10 秒
        t0 = _time.time()

        while not self.stopped and (_time.time() - t0) < max_wait:
            cv_img, dets = self.quick_detect(rect, sell_templates)
            if cv_img is None:
                break

            # 点击批量出售按钮（带间隔防重复）
            if self.click_with_interval(dets, "btn_batch_sell", "批量出售", interval=1.0):
                batch_clicked = True
                _time.sleep(0.5)  # 等待全选动画
                continue

            # 已点击批量出售 → 找确认按钮
            if batch_clicked:
                confirm = self.find_by_name(dets, "btn_confirm")
                if confirm and self._click_interval_ok("btn_confirm", 1.0):
                    self.click(confirm.x, confirm.y, "确认出售", ActionType.SELL)
                    self._click_interval_hit("btn_confirm")
                    logger.info("✓ 批量出售完成")
                    _time.sleep(0.5)
                    self._close_page(rect)
                    return ["批量出售果实"]

            # 超时兜底：尝试关闭页面
            close = self.find_any(dets, ["btn_close", "btn_shop_close"])
            if close and batch_clicked:
                self.click(close.x, close.y, "关闭页面", ActionType.CLOSE_POPUP)
                _time.sleep(0.3)
                continue

            _time.sleep(0.3)

        if not batch_clicked:
            logger.warning("出售: 未找到批量出售按钮")
        else:
            logger.warning("出售: 等待确认按钮超时")
        self._close_page(rect)
        return []

    def _close_page(self, rect: tuple):
        """关闭当前页面 — 优先点关闭按钮，找不到则按 Escape"""
        import time as _time

        cv_img, dets = self.quick_detect(rect, ["btn_close", "btn_shop_close"])
        if cv_img is not None:
            close = self.find_any(dets, ["btn_close", "btn_shop_close"])
            if close:
                self.click(close.x, close.y, "关闭页面", ActionType.CLOSE_POPUP)
                _time.sleep(0.3)
                return

        # 兜底：按 Escape 关闭
        import pyautogui
        pyautogui.press("escape")
        _time.sleep(0.3)

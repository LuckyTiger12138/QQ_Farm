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
from typing import Any
import pyautogui
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.strategies.base import BaseStrategy


class TaskStrategy(BaseStrategy):

    # 每日任务页上的领取按钮会随窗口 DPI/缩放变化，OCR 定位比固定模板更稳。
    _DAILY_ENTRY_TEMPLATES = ["btn_task", "btn_rw"]
    _DAILY_CLAIM_TEMPLATES = [
        "btn_claim_task", "btn_claim_yellow", "btn_claim", "btn_direct_claim",
    ]

    def __init__(self, cv_detector, ocr_tool=None):
        super().__init__(cv_detector)
        self.ocr_tool = ocr_tool

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
        """兼容旧调用方，但始终走每日任务安全流程。"""
        return self.try_daily_tasks(rect, detections)

    def try_daily_tasks(self, rect: tuple,
                        detections: list[DetectResult] | None = None) -> list[str]:
        """只领取“每日任务”页的已完成奖励。

        任务页同时包含“成长任务”，所以不能看到通用 ``领取`` 按钮就直接点击。
        必须先通过 OCR 确认页面标题为“每日任务”；OCR 不可用或标题不明确时采用
        fail-closed 策略，不执行任何领取操作。
        """
        if self.stopped:
            return []

        cv_img, page_dets, _ = self.capture(rect)
        if cv_img is None:
            return []

        # 已经在任务页时直接校验；否则从农场入口打开任务页。
        daily_state = self._daily_page_state(cv_img)
        if daily_state != "daily":
            entry_dets = detections or page_dets
            entry = self.find_any(entry_dets, self._DAILY_ENTRY_TEMPLATES)
            if entry is None:
                _, entry_dets = self.quick_detect(rect, self._DAILY_ENTRY_TEMPLATES)
                entry = self.find_any(entry_dets, self._DAILY_ENTRY_TEMPLATES)
            if entry is None:
                return []

            if not self.click(entry.x, entry.y, "打开每日任务"):
                return []
            time.sleep(0.8)

            # 等待页面动画完成，同时把成长任务和 OCR 失败都挡在领取之前。
            for _ in range(5):
                if self.stopped:
                    return []
                cv_img, _, _ = self.capture(rect)
                if cv_img is None:
                    return []
                daily_state = self._daily_page_state(cv_img)
                if daily_state != "unknown":
                    break
                time.sleep(0.25)

        if daily_state != "daily":
            logger.info("任务页不是每日任务或标题无法确认，跳过领取")
            self._close_daily_page(rect)
            return []

        actions: list[str] = []
        # 每次点击后重新 OCR，避免列表刷新后沿用旧坐标。
        for _ in range(12):
            if self.stopped:
                break
            cv_img, _, _ = self.capture(rect)
            if cv_img is None or self._daily_page_state(cv_img) != "daily":
                break

            claim = self._find_daily_claim(cv_img)
            if claim is None:
                break
            if not self.click(claim[0], claim[1], "领取每日任务奖励", ActionType.CLOSE_POPUP):
                break
            actions.append("领取每日任务奖励")
            time.sleep(0.35)

        self._close_daily_page(rect)
        if actions:
            logger.info(f"每日任务：领取 {len(actions)} 项奖励")
        else:
            logger.info("每日任务：没有可领取奖励")
        return actions

    def _ensure_ocr(self) -> Any | None:
        """懒加载 OCR；缺少可选依赖时返回 None。"""
        if self.ocr_tool is not None:
            return self.ocr_tool
        try:
            from utils.ocr_provider import get_ocr_tool
            self.ocr_tool = get_ocr_tool(scope="task")
        except Exception as exc:
            logger.debug(f"每日任务 OCR 不可用: {exc}")
            self.ocr_tool = False
        return self.ocr_tool or None

    @staticmethod
    def _ocr_item_text(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("text", ""))
        return str(getattr(item, "text", ""))

    @staticmethod
    def _ocr_item_center(item: Any) -> tuple[int, int] | None:
        box = item.get("box") if isinstance(item, dict) else getattr(item, "box", None)
        if not box:
            return None
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            return int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
        except (TypeError, ValueError, IndexError, ZeroDivisionError):
            return None

    def _ocr_items(self, cv_img: Any) -> list[Any]:
        ocr = self._ensure_ocr()
        if ocr is None:
            return []
        try:
            if hasattr(ocr, "detect"):
                return ocr.detect(cv_img, scale=1.5, alpha=1.1, beta=0.0) or []
            text, _ = ocr.detect_text(cv_img, scale=1.5, alpha=1.1, beta=0.0)
            return [{"text": text, "box": []}] if text else []
        except Exception as exc:
            logger.debug(f"每日任务 OCR 失败: {exc}")
            return []

    def _daily_page_state(self, cv_img: Any) -> str:
        """返回 daily/growth/unknown；unknown 必须按禁止领取处理。"""
        items = self._ocr_items(cv_img)
        if not items:
            return "unknown"
        text = "".join(self._ocr_item_text(item).replace(" ", "") for item in items)
        # 真实任务面板会同时显示“成长任务”和“每日任务”。只要每日任务
        # 分区存在，就按每日任务页处理；不能因为顶部的成长任务标题而跳过。
        if "每日任务" in text:
            return "daily"
        if "成长任务" in text:
            return "growth"
        return "unknown"

    def _daily_section_top(self, items: list[Any]) -> int | None:
        """返回每日任务分区标题的 y 坐标，找不到时禁止领取。"""
        tops: list[int] = []
        for item in items:
            text = self._ocr_item_text(item).replace(" ", "")
            center = self._ocr_item_center(item)
            if center is not None and "每日任务" in text:
                tops.append(center[1])
        return min(tops) if tops else None

    def _find_daily_claim(self, cv_img: Any) -> tuple[int, int] | None:
        """在已确认的每日任务页中定位最上方领取按钮。"""
        items = self._ocr_items(cv_img)
        w = cv_img.shape[1]
        daily_top = self._daily_section_top(items)
        if daily_top is None:
            return None
        claims: list[tuple[int, int]] = []
        for item in items:
            text = self._ocr_item_text(item).replace(" ", "")
            center = self._ocr_item_center(item)
            if center is None or "领取" not in text:
                continue
            # 领取按钮位于每行右侧，过滤标题/奖励说明中的同名文字。
            if center[0] < int(w * 0.45) or center[1] <= daily_top:
                continue
            claims.append(center)
        if claims:
            return min(claims, key=lambda p: p[1])

        # OCR 没识别到按钮文字时，仍只在已确认的每日页使用模板兜底。
        dets = self.cv_detector.detect_targeted(
            cv_img,
            self._DAILY_CLAIM_TEMPLATES,
            scales=[0.8, 0.9, 1.0, 1.1, 1.2],
        )
        candidates = [d for d in dets if d.x >= int(w * 0.45) and d.y > daily_top]
        if not candidates:
            return None
        candidate = min(candidates, key=lambda d: d.y)
        return candidate.x, candidate.y

    def _close_daily_page(self, rect: tuple) -> None:
        """离开任务页，避免后续通用弹窗逻辑误处理页面按钮。"""
        cv_img, dets = self.quick_detect(rect, ["btn_rw_close", "btn_close"])
        if cv_img is not None:
            close = self.find_any(dets, ["btn_rw_close", "btn_close"])
            if close:
                self.click(close.x, close.y, "关闭每日任务", ActionType.CLOSE_POPUP)
                time.sleep(0.25)
                return

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

"""定点捣乱策略 — 大小号通讯接收端

游戏机制：
  - 捣乱只能对已种下作物的地块（非空地），点击地块 → 弹出菜单 → 放草/放虫
  - 每块地同一时间只能有草或虫（不能同时存在），需对方一键务农清除后才能再放
  - 每轮只放一种类型（草或虫），交替进行：放草→等对方清理→放虫→等清理→循环
  - 每日上限 100 次，每块地每次算 1 次

流程：进入好友农场 → 网格检测可捣乱地块 → 放草/放虫拖拽 → 返回主页 → 通知对方清理
"""

import re
import time
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.scene_detector import Scene, identify_scene
from core.strategies.base import BaseStrategy

try:
    from utils.friend_name_ocr import FriendNameOCR

    HAS_OCR = True
except ImportError:
    HAS_OCR = False

REF_WINDOW_SIZE = (581, 1054)

FRIEND_BTN_POS = (470, 878)
VISIT_BTN_POS = (461, 304)
HOME_BTN_POS = (487, 713)
CLOSE_BTN_POS = (510, 71)

SWIPE_START = (405, 920)
SWIPE_END = (150, 920)

FRIEND_NAME_OCR_X1 = 150
FRIEND_NAME_OCR_X2 = 400
FRIEND_NAME_OCR_Y1 = 265
FRIEND_NAME_OCR_Y2 = 780
FRIEND_NAME_ABOVE_Y_WINDOW = 40

MAX_SCROLL_FIND = 8

_SCALES_FAST = [1.0, 0.9, 1.1]


def _scale_pos(pos: tuple, img_h: int, img_w: int) -> tuple[int, int]:
    return (
        int(pos[0] * img_w / REF_WINDOW_SIZE[0]),
        int(pos[1] * img_h / REF_WINDOW_SIZE[1]),
    )


def _normalize_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return text.lower()


class TargetedPrankStrategy(BaseStrategy):
    """定点捣乱：定位目标好友 → 网格检测可捣乱地块 → 放草/放虫"""

    def __init__(self, cv_detector):
        super().__init__(cv_detector)
        self._friend_name_ocr = FriendNameOCR() if HAS_OCR else None

    def prank_friend(self, friend_name: str, rect: tuple,
                     daily_remaining: int) -> dict:
        """定点捣乱主流程：放草 + 放虫一次完成

        Args:
            friend_name: 目标好友昵称
            rect: 游戏窗口矩形 (left, top, width, height)
            daily_remaining: 今日剩余捣乱次数

        Returns:
            {"success": bool, "message": str, "prank_count": int}
        """
        result = {"success": False, "message": "", "prank_count": 0}
        if not friend_name:
            result["message"] = "好友昵称为空"
            return result
        if self.stopped:
            result["message"] = "已停止"
            return result
        if daily_remaining <= 0:
            result["message"] = "今日捣乱次数已用完"
            return result

        logger.info(f"[大小号捣乱🌿] 定点捣乱: 好友[{friend_name}] 剩余{daily_remaining}次")

        if not self._enter_friend_list(rect):
            result["message"] = "无法进入好友列表"
            return result

        found = self._find_target_friend(friend_name, rect)
        if not found:
            self._back_to_home(rect)
            result["message"] = f"未找到好友 [{friend_name}]"
            logger.warning(f"定点捣乱: 在好友列表中未找到 [{friend_name}]")
            return result

        time.sleep(0.5)
        total_pranked = self._do_prank(rect, daily_remaining)
        self._back_to_home(rect)

        if total_pranked > 0:
            result["success"] = True
            result["prank_count"] = total_pranked
            result["message"] = (
                f"[大小号捣乱🌿] ✓ 捣乱成功: 好友[{friend_name}] {total_pranked}次"
            )
            logger.info(f"[大小号捣乱🌿] 捣乱成功: 好友[{friend_name}] {total_pranked}次")
        else:
            result["success"] = True
            result["message"] = (
                f"[大小号捣乱🌿] 无捣乱目标: 好友[{friend_name}] (可能未种作物或已放满)"
            )
            logger.info(f"[大小号捣乱🌿] 捣乱完成: 好友[{friend_name}] 无可用地块")

        return result

    # ── 导航 ────────────────────────────────────────────────────

    def _enter_friend_list(self, rect: tuple) -> bool:
        for attempt in range(3):
            if self.stopped:
                return False

            cv_img, dets = self._quick_detect(rect, [
                "ui_goto_friend", "btn_haoyou",
                "btn_shop", "btn_warehouse", "btn_home",
                "btn_close", "btn_claim", "btn_confirm",
            ])
            if cv_img is None:
                time.sleep(0.3)
                continue

            scene = identify_scene(dets, self.cv_detector, cv_img)
            if scene in (Scene.FRIEND_FARM, Scene.FRIEND_LIST):
                return True

            if scene == Scene.POPUP:
                from core.strategies.popup import PopupStrategy

                ps = PopupStrategy(self.cv_detector)
                ps.action_executor = self.action_executor
                ps.handle_popup(dets)
                time.sleep(0.3)
                continue

            if scene in (Scene.FARM_OVERVIEW, Scene.UNKNOWN):
                btn = self._find_any_name(dets, ["ui_goto_friend", "btn_haoyou"])
                if btn:
                    self.click(btn.x, btn.y, f"点击好友按钮({btn.name})")
                else:
                    h, w = cv_img.shape[:2]
                    fx, fy = _scale_pos(FRIEND_BTN_POS, h, w)
                    self.click(fx, fy, "点击好友按钮(坐标兜底)")

                time.sleep(1.0)
                if self._wait_left_farm(rect, timeout=4.0):
                    return True

        logger.warning("定点捣乱: 进入好友列表失败")
        return False

    def _wait_left_farm(self, rect: tuple, timeout: float = 4.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self.stopped:
                return False
            cv_img, dets = self._quick_detect(rect, [
                "btn_home", "btn_shop", "btn_warehouse",
                "ui_goto_friend", "btn_claim", "btn_confirm",
            ])
            if cv_img is None:
                time.sleep(0.3)
                continue

            names = {d.name for d in dets}
            if "btn_home" in names:
                return True

            scene = identify_scene(dets, self.cv_detector, cv_img)
            if scene in (Scene.FRIEND_FARM, Scene.FRIEND_LIST):
                return True

            has_farm_ui = names & {"btn_shop", "btn_warehouse"}
            if not has_farm_ui and scene != Scene.FARM_OVERVIEW:
                return True

            if scene == Scene.POPUP:
                from core.strategies.popup import PopupStrategy

                ps = PopupStrategy(self.cv_detector)
                ps.action_executor = self.action_executor
                ps.handle_popup(dets)

            time.sleep(0.3)
        return False

    # ── 好友搜索 ────────────────────────────────────────────────

    def _find_target_friend(self, target_name: str, rect: tuple) -> bool:
        target_norm = _normalize_name(target_name)
        if not target_norm:
            return False

        for scroll_round in range(MAX_SCROLL_FIND):
            if self.stopped:
                return False

            cv_img, dets = self._quick_detect(rect, [
                "btn_visit_first", "btn_home",
                "icon_steal_in_friend_detail",
            ])
            if cv_img is None:
                time.sleep(0.3)
                continue

            if any(d.name == "btn_home" for d in dets):
                return True

            visit_buttons = [d for d in dets if d.name == "btn_visit_first"]
            if not visit_buttons:
                self._swipe_friend_list(rect)
                time.sleep(0.5)
                continue

            for vbtn in visit_buttons:
                if self.stopped:
                    return False
                name = self._detect_friend_name_near_visit(cv_img, vbtn.x, vbtn.y)
                name_norm = _normalize_name(name)
                if name_norm and (
                    target_norm.startswith(name_norm)
                    or name_norm.startswith(target_norm)
                ):
                    logger.info(f"🎯 找到目标好友 [{name}]，点击进入")
                    self.click(vbtn.x, vbtn.y, f"进入好友[{name}]")
                    time.sleep(1.0)
                    for _ in range(6):
                        if self.stopped:
                            return False
                        cv2_img, d2 = self._quick_detect(rect, ["btn_home"])
                        if cv2_img is not None and any(
                            d.name == "btn_home" for d in d2
                        ):
                            return True
                        time.sleep(0.3)
                    return True

            self._swipe_friend_list(rect)
            time.sleep(0.5)

        return False

    def _detect_friend_name_near_visit(self, cv_img, visit_x: int, visit_y: int) -> str:
        if self._friend_name_ocr is None or cv_img is None:
            return ""
        h, w = cv_img.shape[:2]
        x1 = max(0, FRIEND_NAME_OCR_X1)
        y1 = max(0, FRIEND_NAME_OCR_Y1)
        x2 = min(w, FRIEND_NAME_OCR_X2)
        y2 = min(h, FRIEND_NAME_OCR_Y2)
        if x2 <= x1 or y2 <= y1:
            return ""
        items = self._friend_name_ocr.detect_items(
            cv_img, region=(x1, y1, x2, y2)
        )
        y_low = float(visit_y - FRIEND_NAME_ABOVE_Y_WINDOW)
        y_high = float(visit_y)
        candidates: list[tuple[float, str]] = []
        for item in items:
            text = str(item.text or "").strip()
            if not text:
                continue
            ys = [point[1] for point in item.box]
            center_y = float(min(ys) + max(ys)) / 2.0
            if not (y_low <= center_y <= y_high):
                continue
            min_x = float(min(point[0] for point in item.box))
            candidates.append((min_x, text))
        candidates.sort(key=lambda c: c[0])
        name = "".join([c[1] for c in candidates]).strip()
        return name

    # ── 捣乱 ────────────────────────────────────────────────────

    def _do_prank(self, rect: tuple, daily_remaining: int) -> int:
        """执行捣乱：放草 + 放虫，一次访问完成两种

        流程：网格定位 → 遍历找可捣乱地块 → 拖拽放草 → 点空白 → 再点地块 → 拖拽放虫
        """
        all_lands = self._get_grid_positions(rect)
        if not all_lands:
            logger.info("定点捣乱: 好友农场无可捣乱地块")
            return 0

        # 遍历网格找第一个弹菜单的地块
        first = None
        found = None
        for i, pt in enumerate(all_lands):
            if self.stopped:
                return 0
            self.click(pt.x, pt.y, f"探测{i+1}/{len(all_lands)}", ActionType.PRANK)
            time.sleep(0.5)

            cv_img, dets = self._quick_detect(rect, [
                "btn_fangcao", "btn_fangchong",
                "btn_plant", "btn_remove", "btn_fertilize",
            ])
            if dets:
                first = pt
                found = dets
                break
            self.click_blank(rect)
            time.sleep(0.15)

        if not found:
            logger.info(f"定点捣乱: 遍历 {len(all_lands)} 个网格，无可捣乱地块")
            return 0

        total = 0

        # 第一轮：放草
        weed_remaining = daily_remaining - total
        if weed_remaining > 0:
            btn = self._find_any_name(found, ["btn_fangcao"])
            if not btn:
                btn = found[0]
                logger.info(f"定点捣乱: 兜底放草按钮 ({btn.x},{btn.y})")

            count = min(len(all_lands), weed_remaining)
            logger.info(f"定点捣乱: 放草拖拽 {count} 块")
            weed_count = self._drag_prank_to_lands(btn, all_lands[:count])
            total += weed_count
            logger.info(f"定点捣乱: 放草完成 {weed_count} 次")

        # 第二轮：放虫（需要重新点地块开菜单）
        bug_remaining = daily_remaining - total
        if bug_remaining > 0 and not self.stopped:
            time.sleep(0.5)
            self.click_blank(rect)
            time.sleep(0.3)

            # 重新点地块开菜单
            self.click(first.x, first.y, "重新点地块(放虫)", ActionType.PRANK)
            time.sleep(0.5)

            cv_img2, dets2 = self._quick_detect(rect, [
                "btn_fangcao", "btn_fangchong",
                "btn_plant", "btn_remove", "btn_fertilize",
            ])
            if cv_img2 is not None and dets2:
                btn2 = self._find_any_name(dets2, ["btn_fangchong"])
                if not btn2:
                    btn2 = dets2[0]
                    logger.info(f"定点捣乱: 兜底放虫按钮 ({btn2.x},{btn2.y})")

                count2 = min(len(all_lands), bug_remaining)
                logger.info(f"定点捣乱: 放虫拖拽 {count2} 块")
                bug_count = self._drag_prank_to_lands(btn2, all_lands[:count2])
                total += bug_count
                logger.info(f"定点捣乱: 放虫完成 {bug_count} 次")
            else:
                logger.info("定点捣乱: 重新点地块后未检测到菜单，跳过放虫")

        return total

    def _detect_prankable_by_template(self, rect: tuple,
                                      btn_name: str) -> list[DetectResult]:
        """通过模板匹配检测可捣乱地块（地块上显示的放草/放虫图标）"""
        cv_img, dets = self._quick_detect(rect, [btn_name])
        if cv_img is None or not dets:
            return []
        results = [d for d in dets if d.name == btn_name]
        results.sort(key=lambda d: (d.y, d.x))
        return results

    def _get_grid_positions(self, rect: tuple) -> list[DetectResult]:
        """锚点检测 → 推算 4x6=24 网格坐标（与施肥 _detect_lands_by_anchor 一致，含重试）"""
        from utils.land_grid import get_lands_from_land_anchor

        anchor_right = None
        anchor_left = None

        for attempt in range(3):
            if self.stopped:
                return []
            cv_img = self._capture_only(rect)
            if cv_img is None:
                time.sleep(1.0)
                continue

            anchors = self.cv_detector.detect_targeted(
                cv_img, ['btn_land_right', 'btn_land_left'],
                scales=[1.0, 0.9, 1.1],
            )
            for det in anchors:
                if det.name == 'btn_land_right':
                    anchor_right = (int(det.x), int(det.y))
                elif det.name == 'btn_land_left':
                    anchor_left = (int(det.x), int(det.y))

            if anchor_right or anchor_left:
                break
            logger.debug(f"定点捣乱: 锚点检测重试 {attempt + 1}/3")
            time.sleep(1.0)

        if not anchor_right and not anchor_left:
            logger.warning("定点捣乱: 锚点检测失败 (btn_land_right / btn_land_left)")
            return []

        cells = get_lands_from_land_anchor(anchor_right, anchor_left, rows=4, cols=6)
        if not cells:
            logger.warning("定点捣乱: 锚点网格推算返回 0 个地块")
            return []

        lands = [
            DetectResult(
                name=f"land_anchor_{cell.label}", category="land",
                x=cell.center[0], y=cell.center[1],
                w=0, h=0, confidence=1.0,
            )
            for cell in cells
        ]
        logger.info(f"定点捣乱: 锚点检测成功，推算 {len(lands)} 个地块")
        return lands

    def _execute_prank(self, targets: list[DetectResult], rect: tuple,
                       btn_name: str, prank_type: str) -> int:
        """执行捣乱：点击第一块 → 选择放草/放虫 → 拖拽到其他地块"""
        if not targets:
            return 0

        first = targets[0]
        self.click(first.x, first.y, f"点击地块({prank_type})", ActionType.PRANK)
        time.sleep(0.5)

        # 检测菜单按钮（优先 btn_fangcao/btn_fangchong，兜底通用菜单按钮）
        cv_img, dets = self._quick_detect(rect, [
            "btn_fangcao", "btn_fangchong",
            "btn_plant", "btn_remove", "btn_fertilize",
        ])
        if cv_img is None:
            self.click_blank(rect)
            return 0

        prank_btn = self._find_any_name(dets, [btn_name])
        if not prank_btn:
            # 兜底：取第一个检测到的菜单按钮位置作为点击目标
            if dets:
                prank_btn = dets[0]
                type_label = "放草" if prank_type == "weed" else "放虫"
                logger.info(f"定点捣乱: 未检测到 {type_label} 模板，使用兜底位置 ({prank_btn.x},{prank_btn.y})")
            else:
                self.click_blank(rect)
                return 0

        self.click(prank_btn.x, prank_btn.y, f"选择{prank_type}", ActionType.PRANK)
        time.sleep(0.3)

        if len(targets) <= 1:
            return 1

        return self._drag_prank_to_lands(first, targets[1:]) + 1

    def _drag_prank_to_lands(self, source: DetectResult,
                             targets: list[DetectResult]) -> int:
        """拖拽放草/放虫到多个目标地块"""
        if not self.action_executor or not targets:
            return 0

        start_abs = self.action_executor.relative_to_absolute(
            source.x, source.y
        )
        points = [
            self.action_executor.relative_to_absolute(t.x, t.y)
            for t in targets
        ]

        done = self.action_executor.drag_multi_points(
            *start_abs, points,
            check_stopped=lambda: self.stopped,
        )
        count = len(targets) if done else 0
        if done:
            logger.info(f"定点捣乱: 拖拽完成 {count} 块地")
        return count

    # ── 返回主页 ────────────────────────────────────────────────

    def _back_to_home(self, rect: tuple) -> bool:
        for attempt in range(5):
            if self.stopped:
                return False

            cv_img, dets = self._quick_detect(rect, [
                "btn_home", "btn_close", "btn_rw_close",
                "btn_shop", "btn_warehouse", "ui_goto_friend",
            ])
            if cv_img is None:
                time.sleep(0.3)
                continue

            names = {d.name for d in dets}
            if names & {"btn_shop", "btn_warehouse", "ui_goto_friend"}:
                return True

            home_btn = self._find_any_name(
                dets, ["btn_home", "btn_close", "btn_rw_close"]
            )
            if home_btn:
                self.click(home_btn.x, home_btn.y, f"返回({home_btn.name})")
            else:
                h, w = cv_img.shape[:2]
                fx, fy = _scale_pos(HOME_BTN_POS, h, w)
                self.click(fx, fy, "返回主页(坐标兜底)")
            time.sleep(0.8)

        return False

    # ── 工具 ────────────────────────────────────────────────────

    def _quick_detect(self, rect: tuple, template_names: list[str]):
        if not self.cv_detector._loaded:
            self.cv_detector.load_templates()
        cv_img = self._capture_only(rect)
        if cv_img is None:
            return None, []
        detections = self.cv_detector.detect_targeted(
            cv_img, template_names, scales=_SCALES_FAST
        )
        return cv_img, detections

    def _capture_only(self, rect: tuple):
        if self._capture_fn:
            cv_img, _, _ = self._capture_fn(rect, save=False)
            if cv_img is not None:
                return cv_img
        return None

    def _swipe_friend_list(self, rect: tuple):
        if not self.action_executor:
            return
        h, w = REF_WINDOW_SIZE[1], REF_WINDOW_SIZE[0]
        sx, sy = _scale_pos(SWIPE_START, h, w)
        ex, ey = _scale_pos(SWIPE_END, h, w)
        dx, dy = ex - sx, ey - sy
        abs_sx = self.action_executor._window_left + sx
        abs_sy = self.action_executor._window_top + sy
        self.action_executor.drag(abs_sx, abs_sy, dx, dy, duration=0.3, steps=10)

    def _find_any_name(self, dets: list[DetectResult],
                       names: list[str]) -> DetectResult | None:
        name_set = set(names)
        for d in dets:
            if d.name in name_set:
                return d
        return None

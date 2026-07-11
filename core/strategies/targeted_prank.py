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
import unicodedata
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

# 好友列表翻页：必须纵向上滑。旧值是横向拖拽 (405,920)->(150,920)，
# 只会左右拖动列表区域，导致翻页失败，一直在同一页找好友。
SWIPE_START = (290, 820)
SWIPE_END = (290, 360)

FRIEND_NAME_OCR_X1 = 150
FRIEND_NAME_OCR_X2 = 400
# 好友列表第一条可见行在搜索框下方约 y=110~230；旧值 265 会裁掉首行，
# 例如昵称 "!!!" 明明可见却无法进入。
FRIEND_NAME_OCR_Y1 = 100
FRIEND_NAME_OCR_Y2 = 780
FRIEND_NAME_ABOVE_Y_WINDOW = 70

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
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return text.lower()


def _compact_name(value: str) -> str:
    """保留符号的轻量归一化，用于 !!! / 纯符号昵称匹配。"""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"\s+", "", text)


def _name_matches(target_name: str, candidate_name: str) -> bool:
    """好友名匹配：优先文本归一化，纯符号昵称回退到原样压缩匹配。"""
    target_norm = _normalize_name(target_name)
    candidate_norm = _normalize_name(candidate_name)
    if target_norm and candidate_norm:
        return (
            target_norm.startswith(candidate_norm)
            or candidate_norm.startswith(target_norm)
        )

    target_raw = _compact_name(target_name)
    candidate_raw = _compact_name(candidate_name)
    if not target_raw or not candidate_raw:
        return False
    return target_raw.startswith(candidate_raw) or candidate_raw.startswith(target_raw)


def _is_symbol_only_name(value: str) -> bool:
    """昵称归一化后没有文字/数字时，视为纯符号昵称。"""
    return bool(_compact_name(value)) and not bool(_normalize_name(value))


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
        target_raw = _compact_name(target_name)
        if not target_raw:
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
                logger.info(
                    f"定点捣乱: 拜访按钮({vbtn.x},{vbtn.y})附近识别昵称=[{name}]"
                )
                if _name_matches(target_name, name):
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

            symbol_visit = self._find_symbol_friend_visit_fallback(
                target_name, cv_img, visit_buttons
            )
            if symbol_visit:
                visit_x, visit_y, reason = symbol_visit
                logger.info(
                    f"🎯 纯符号昵称兜底命中 [{target_name}]：{reason}，点击拜访"
                )
                self.click(visit_x, visit_y, f"进入好友[{target_name}](符号昵称兜底)")
                time.sleep(1.0)
                for _ in range(6):
                    if self.stopped:
                        return False
                    cv2_img, d2 = self._quick_detect(rect, ["btn_home"])
                    if cv2_img is not None and any(d.name == "btn_home" for d in d2):
                        return True
                    time.sleep(0.3)
                return True

            logger.info(
                f"定点捣乱: 第 {scroll_round + 1}/{MAX_SCROLL_FIND} 页未找到好友 "
                f"[{target_name}]，继续滑动查找"
            )
            self._swipe_friend_list(rect)
            time.sleep(0.5)

        return False

    def _find_symbol_friend_visit_fallback(
        self,
        target_name: str,
        cv_img,
        visit_buttons: list[DetectResult],
    ) -> tuple[int, int, str] | None:
        """纯符号昵称兜底。

        OCR 常会忽略 "!!!" 这类纯标点昵称；顶部第一行的拜访按钮也可能因
        遮挡/裁切无法被 btn_visit_first 模板命中。此时根据已检测到的
        拜访按钮行距推断漏检行，并点击昵称 OCR 为空的行。
        """
        if not _is_symbol_only_name(target_name) or not visit_buttons:
            return None

        ys = sorted({int(btn.y) for btn in visit_buttons})
        xs = sorted(int(btn.x) for btn in visit_buttons)
        visit_x = xs[len(xs) // 2]

        spacing = 128
        if len(ys) >= 2:
            diffs = [b - a for a, b in zip(ys, ys[1:]) if 60 <= b - a <= 180]
            if diffs:
                spacing = int(round(sum(diffs) / len(diffs)))

        candidate_rows: list[tuple[int, str]] = []
        first_y = ys[0] - spacing
        if 80 <= first_y <= cv_img.shape[0] - 40:
            candidate_rows.append((first_y, "模板漏检的上一行"))
        candidate_rows.extend((y, "已检测拜访按钮行") for y in ys)

        seen: set[int] = set()
        for row_y, source in candidate_rows:
            if row_y in seen:
                continue
            seen.add(row_y)
            name = self._detect_friend_name_near_visit(cv_img, visit_x, row_y)
            logger.info(
                f"定点捣乱: 纯符号兜底检查行 y={row_y} source={source} OCR=[{name}]"
            )
            if _name_matches(target_name, name):
                return visit_x, row_y, f"{source} OCR直接匹配[{name}]"
            if not _compact_name(name):
                return visit_x, row_y, f"{source} OCR为空，疑似纯符号昵称"

        return None

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
        """执行捣乱：点击地块打开工具栏 → 像施肥一样拖拽放草/放虫到地块

        当前小程序机制与施肥一致：打开地块操作栏后，按住工具图标
        （放草/放虫）拖到各个地块。旧逻辑逐块点地找菜单，进入好友农场
        后如果菜单不随点击弹出，就会一直探测 24 块但没有真实操作。
        """
        all_lands = self._get_grid_positions(rect)
        if not all_lands:
            logger.info("定点捣乱: 好友农场无可捣乱地块")
            return 0
        all_lands = self._filter_visible_lands(all_lands, rect)
        if not all_lands:
            logger.info("定点捣乱: 地块坐标均超出窗口，跳过")
            return 0

        opener, found = self._open_prank_toolbar(rect, all_lands)
        if not opener or not found:
            logger.info(f"定点捣乱: 遍历 {len(all_lands)} 个网格，未打开放草/放虫工具栏")
            return 0
        target_lands = self._prioritize_land(all_lands, opener)

        total = 0

        # 第一轮：放草
        weed_remaining = daily_remaining - total
        if weed_remaining > 0:
            btn = self._find_any_name(found, ["btn_fangcao"])
            if not btn:
                logger.info("定点捣乱: 未检测到放草按钮，跳过放草")
            else:
                count = min(len(target_lands), weed_remaining)
                logger.info(f"定点捣乱: 放草拖拽 {count} 块")
                weed_count = self._drag_prank_to_lands(btn, target_lands[:count])
                total += weed_count
                logger.info(f"定点捣乱: 放草完成 {weed_count} 次")

        # 第二轮：放虫（拖拽后工具栏可能关闭，需要重新打开）
        bug_remaining = daily_remaining - total
        if bug_remaining > 0 and not self.stopped:
            time.sleep(0.5)
            self.click_blank(rect)
            time.sleep(0.3)

            _, dets2 = self._open_prank_toolbar(rect, all_lands, preferred=opener)
            if dets2:
                btn2 = self._find_any_name(dets2, ["btn_fangchong"])
                if btn2:
                    count2 = min(len(target_lands), bug_remaining)
                    logger.info(f"定点捣乱: 放虫拖拽 {count2} 块")
                    bug_count = self._drag_prank_to_lands(btn2, target_lands[:count2])
                    total += bug_count
                    logger.info(f"定点捣乱: 放虫完成 {bug_count} 次")
                else:
                    logger.info("定点捣乱: 未检测到放虫按钮，跳过放虫")
            else:
                logger.info("定点捣乱: 重新打开工具栏失败，跳过放虫")

        return total

    def _prioritize_land(
        self,
        lands: list[DetectResult],
        first: DetectResult,
    ) -> list[DetectResult]:
        """把已成功打开工具栏的地块排到拖拽目标首位。"""
        ordered = [first]
        ordered.extend(
            land for land in lands
            if not (land.x == first.x and land.y == first.y)
        )
        return ordered

    def _open_prank_toolbar(
        self,
        rect: tuple,
        lands: list[DetectResult],
        preferred: DetectResult | None = None,
    ) -> tuple[DetectResult | None, list[DetectResult]]:
        """点击一块地，打开包含放草/放虫的工具栏。"""
        candidates = [preferred] if preferred else []
        candidates.extend([land for land in lands if preferred is None or land is not preferred])
        for i, pt in enumerate(candidates):
            if self.stopped:
                return None, []
            if pt is None:
                continue
            self.click(pt.x, pt.y, f"打开捣乱工具栏{i+1}/{len(candidates)}", ActionType.PRANK)
            start = time.time()
            while time.time() - start < 1.3:
                if self.stopped:
                    return None, []
                time.sleep(0.2)
                cv_img, dets = self._quick_detect(rect, [
                    "btn_fangcao", "btn_fangchong",
                    "btn_plant", "btn_remove", "btn_fertilize",
                ])
                if cv_img is None:
                    continue
                prank_dets = [
                    det for det in dets
                    if det.name in {"btn_fangcao", "btn_fangchong"}
                ]
                if prank_dets:
                    names = [det.name for det in prank_dets]
                    elapsed = time.time() - start
                    logger.info(f"定点捣乱: 工具栏已打开，检测到 {names}，耗时 {elapsed:.1f}s")
                    return pt, dets
            self.click_blank(rect)
            time.sleep(0.15)
        return None, []

    def _filter_visible_lands(
        self,
        lands: list[DetectResult],
        rect: tuple,
    ) -> list[DetectResult]:
        """过滤锚点推算中落到窗口外的地块，避免拖拽到无效坐标。"""
        width, height = rect[2], rect[3]
        margin = 8
        visible = [
            land for land in lands
            if margin <= land.x <= width - margin and margin <= land.y <= height - margin
        ]
        dropped = len(lands) - len(visible)
        if dropped:
            logger.info(f"定点捣乱: 过滤窗口外地块 {dropped} 块，保留 {len(visible)} 块")
        return visible

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
            anchor_right, anchor_left = self._select_land_anchor_pair(anchors)

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

    def _select_land_anchor_pair(
        self,
        anchors: list[DetectResult],
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        """从多个锚点候选中选择最符合土地网格跨度的一对。

        真实左右土地锚点的相对跨度接近 baseline: left - right ≈ (-439, 43)。
        好友农场画面里偶尔会把土地纹理误识别成 btn_land_left；旧逻辑直接取
        最后一个候选，会把整套 24 格坐标推偏，表现就是点开土地后又空点返回。
        """
        rights = [det for det in anchors if det.name == 'btn_land_right']
        lefts = [det for det in anchors if det.name == 'btn_land_left']
        if rights and lefts:
            expected_dx = -439
            expected_dy = 43
            best_pair = None
            best_score = float("inf")
            for right in rights:
                for left in lefts:
                    dx = int(left.x) - int(right.x)
                    dy = int(left.y) - int(right.y)
                    # y 方向假阳性危害更大，加权更高；置信度用于轻微打破平局。
                    score = (
                        abs(dx - expected_dx)
                        + abs(dy - expected_dy) * 3
                        - (float(right.confidence) + float(left.confidence)) * 20
                    )
                    if score < best_score:
                        best_score = score
                        best_pair = (right, left, dx, dy)
            if best_pair:
                right, left, dx, dy = best_pair
                logger.info(
                    "定点捣乱: 选择锚点对 "
                    f"right=({int(right.x)},{int(right.y)}) "
                    f"left=({int(left.x)},{int(left.y)}) span=({dx},{dy})"
                )
                return (int(right.x), int(right.y)), (int(left.x), int(left.y))

        if rights:
            best_right = max(rights, key=lambda det: float(det.confidence))
            return (int(best_right.x), int(best_right.y)), None
        if lefts:
            best_left = max(lefts, key=lambda det: float(det.confidence))
            return None, (int(best_left.x), int(best_left.y))
        return None, None

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
        h, w = rect[3], rect[2]
        sx, sy = _scale_pos(SWIPE_START, h, w)
        ex, ey = _scale_pos(SWIPE_END, h, w)
        dx, dy = ex - sx, ey - sy
        abs_sx = self.action_executor._window_left + sx
        abs_sy = self.action_executor._window_top + sy
        logger.info(
            f"定点捣乱: 好友列表纵向上滑 ({sx},{sy})->({ex},{ey})"
        )
        self.action_executor.drag(abs_sx, abs_sy, dx, dy, duration=0.3, steps=10)

    def _find_any_name(self, dets: list[DetectResult],
                       names: list[str]) -> DetectResult | None:
        name_set = set(names)
        for d in dets:
            if d.name in name_set:
                return d
        return None

"""定点偷菜策略 — 大小号通讯接收端

接收 CrossInstanceBus 的 StealAlert，通过好友昵称 OCR 定位目标好友，
进入其农场执行偷菜，然后返回主页。

复用 FriendStrategy 的导航逻辑（进入好友列表、返回主页）。
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

# 固定坐标（基于 581x1054 窗口）
REF_WINDOW_SIZE = (581, 1054)

FRIEND_BTN_POS = (470, 878)     # main_goto_friend
VISIT_BTN_POS = (461, 304)      # btn_visit_first
STEAL_BTN_POS = (266, 706)      # btn_steal
HOME_BTN_POS = (487, 713)       # btn_home
CLOSE_BTN_POS = (510, 71)       # btn_close

# 好友列表滑动区域
SWIPE_START = (405, 920)
SWIPE_END = (150, 920)

# OCR 识别好友昵称的区域
FRIEND_NAME_OCR_X1 = 150
FRIEND_NAME_OCR_X2 = 400
FRIEND_NAME_OCR_Y1 = 265
FRIEND_NAME_OCR_Y2 = 780
FRIEND_NAME_ABOVE_Y_WINDOW = 40

# 最大滑动查找次数
MAX_SCROLL_FIND = 8

_SCALES_FAST = [1.0, 0.9, 1.1]


def _scale_pos(pos: tuple, img_h: int, img_w: int) -> tuple[int, int]:
    return (
        int(pos[0] * img_w / REF_WINDOW_SIZE[0]),
        int(pos[1] * img_h / REF_WINDOW_SIZE[1]),
    )


def _normalize_name(value: str) -> str:
    """规范化昵称用于匹配"""
    text = str(value or '').strip()
    if not text:
        return ''
    text = re.sub(r'[\W_]+', '', text, flags=re.UNICODE)
    return text.lower()


class TargetedStealStrategy(BaseStrategy):
    """定点偷菜：通过好友昵称 OCR 定位目标好友并偷菜"""

    def __init__(self, cv_detector):
        super().__init__(cv_detector)
        self._friend_name_ocr = FriendNameOCR() if HAS_OCR else None

    def steal_from_friend(self, friend_name: str, rect: tuple) -> dict:
        """定点偷菜主流程

        Args:
            friend_name: 目标好友昵称
            rect: 游戏窗口矩形 (left, top, width, height)

        Returns:
            {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}
        if not friend_name:
            result["message"] = "好友昵称为空"
            return result
        if self.stopped:
            result["message"] = "已停止"
            return result

        logger.info(f"[大小号通讯🎯] 执行定点偷菜: 好友[{friend_name}]")

        # 1. 进入好友列表
        if not self._enter_friend_list(rect):
            result["message"] = "无法进入好友列表"
            return result

        # 2. 搜索目标好友
        found = self._find_target_friend(friend_name, rect)
        if not found:
            self._back_to_home(rect)
            result["message"] = f"未找到好友 [{friend_name}]"
            logger.warning(f"定点偷菜: 在好友列表中未找到 [{friend_name}]")
            return result

        # 3. 偷菜
        time.sleep(0.5)
        cv_img, dets = self._quick_detect(rect, ["btn_steal", "icon_steal_in_friend_detail", "btn_home"])
        if cv_img is None:
            self._back_to_home(rect)
            result["message"] = "截屏失败"
            return result

        stolen = self._do_steal(cv_img, dets, rect)

        # 4. 返回主页
        self._back_to_home(rect)

        if stolen:
            result["success"] = True
            result["message"] = f"[大小号通讯🎯] ✓ 偷菜成功: 好友[{friend_name}]"
            logger.info(f"[大小号通讯🎯] 偷菜成功: 好友[{friend_name}]")
        else:
            result["message"] = f"[大小号通讯🎯] 无可偷作物: 好友[{friend_name}]"
            logger.info(f"[大小号通讯🎯] 偷菜完成: 好友[{friend_name}] 无可偷作物")

        return result

    # ── 导航 ────────────────────────────────────────────────────

    def _enter_friend_list(self, rect: tuple) -> bool:
        """从主页进入好友列表"""
        for attempt in range(3):
            if self.stopped:
                return False

            cv_img, dets = self._quick_detect(rect,
                ["ui_goto_friend", "btn_haoyou",
                 "btn_shop", "btn_warehouse", "btn_home",
                 "btn_close", "btn_claim", "btn_confirm"])
            if cv_img is None:
                time.sleep(0.3)
                continue

            scene = identify_scene(dets, self.cv_detector, cv_img)

            # 已在好友页
            if scene in (Scene.FRIEND_FARM, Scene.FRIEND_LIST):
                return True

            # 弹窗处理
            if scene == Scene.POPUP:
                from core.strategies.popup import PopupStrategy
                ps = PopupStrategy(self.cv_detector)
                ps.action_executor = self.action_executor
                ps.handle_popup(dets)
                time.sleep(0.3)
                continue

            # 农场主页 → 点击好友按钮
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

        logger.warning("定点偷菜: 进入好友列表失败")
        return False

    def _wait_left_farm(self, rect: tuple, timeout: float = 4.0) -> bool:
        """等待离开农场主页"""
        start = time.time()
        while time.time() - start < timeout:
            if self.stopped:
                return False
            cv_img, dets = self._quick_detect(rect,
                ["btn_home", "btn_shop", "btn_warehouse",
                 "ui_goto_friend", "btn_claim", "btn_confirm"])
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
        """在好友列表中搜索目标好友

        策略：
        1. 在列表页用 OCR 识别所有可见好友昵称
        2. 匹配到则点击对应行的 visit 按钮进入
        3. 未匹配则滑动列表继续查找
        """
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

            # 如果已在好友农场（btn_home 存在），说明已经进入了
            if any(d.name == "btn_home" for d in dets):
                logger.debug("已在好友农场中")
                return True

            visit_buttons = [d for d in dets if d.name == "btn_visit_first"]
            if not visit_buttons:
                logger.debug(f"第{scroll_round+1}轮: 未检测到 visit 按钮，滑动重试")
                self._swipe_friend_list(rect)
                time.sleep(0.5)
                continue

            # OCR 识别每个 visit 按钮旁边的昵称
            for vbtn in visit_buttons:
                if self.stopped:
                    return False
                name = self._detect_friend_name_near_visit(cv_img, vbtn.x, vbtn.y)
                name_norm = _normalize_name(name)
                logger.debug(f"好友列表OCR: [{name}] → [{name_norm}] vs 目标 [{target_norm}]")
                if name_norm and (target_norm.startswith(name_norm) or name_norm.startswith(target_norm)):
                    logger.info(f"🎯 找到目标好友 [{name}]，点击进入")
                    self.click(vbtn.x, vbtn.y, f"进入好友[{name}]")
                    time.sleep(1.0)
                    # 等待进入
                    for _ in range(6):
                        if self.stopped:
                            return False
                        cv2_img, d2 = self._quick_detect(rect, ["btn_home"])
                        if cv2_img is not None and any(d.name == "btn_home" for d in d2):
                            return True
                        time.sleep(0.3)
                    return True

            # 当前页未匹配，滑动
            logger.debug(f"第{scroll_round+1}轮: 未匹配目标好友，滑动翻页")
            self._swipe_friend_list(rect)
            time.sleep(0.5)

        return False

    def _detect_friend_name_near_visit(self, cv_img, visit_x: int, visit_y: int) -> str:
        """OCR 识别 visit 按钮旁边的好友昵称"""
        if self._friend_name_ocr is None or cv_img is None:
            return ''
        h, w = cv_img.shape[:2]
        x1 = max(0, FRIEND_NAME_OCR_X1)
        y1 = max(0, FRIEND_NAME_OCR_Y1)
        x2 = min(w, FRIEND_NAME_OCR_X2)
        y2 = min(h, FRIEND_NAME_OCR_Y2)
        if x2 <= x1 or y2 <= y1:
            return ''
        items = self._friend_name_ocr.detect_items(cv_img, region=(x1, y1, x2, y2))
        y_low = float(visit_y - FRIEND_NAME_ABOVE_Y_WINDOW)
        y_high = float(visit_y)
        candidates: list[tuple[float, str]] = []
        for item in items:
            text = str(item.text or '').strip()
            if not text:
                continue
            ys = [point[1] for point in item.box]
            center_y = float(min(ys) + max(ys)) / 2.0
            if not (y_low <= center_y <= y_high):
                continue
            min_x = float(min(point[0] for point in item.box))
            candidates.append((min_x, text))
        candidates.sort(key=lambda c: c[0])
        name = ''.join([c[1] for c in candidates]).strip()
        if name:
            logger.debug(f"定点偷菜OCR: visit按钮({visit_x},{visit_y}) → 好友昵称: {name}")
        return name

    # ── 偷菜 ────────────────────────────────────────────────────

    def _do_steal(self, cv_img, dets: list[DetectResult], rect: tuple = None) -> bool:
        """执行偷菜"""
        steal_buttons = [d for d in dets if d.name == "btn_steal"]
        if steal_buttons:
            steal_buttons.sort(key=lambda d: (d.y, d.x))
            btn = steal_buttons[0]
            self.click(btn.x, btn.y, "偷菜", ActionType.STEAL)
            if rect:
                return self._confirm_action_disappear("btn_steal", rect, timeout=2.0)
            return True

        if any(d.name == "icon_steal_in_friend_detail" for d in dets):
            h, w = cv_img.shape[:2]
            fx, fy = _scale_pos(STEAL_BTN_POS, h, w)
            self.click(fx, fy, "偷菜(坐标)", ActionType.STEAL)
            if rect:
                return self._confirm_action_disappear("btn_steal", rect, timeout=2.0)
            return True

        return False

    def _confirm_action_disappear(self, btn_name: str, rect: tuple, timeout: float = 2.0) -> bool:
        """确认操作：等待按钮消失"""
        start_time = time.time()
        confirm_wait = 0.2

        while time.time() - start_time < timeout:
            if self.stopped:
                return False
            cv_img, dets = self._quick_detect(rect, [btn_name])
            if cv_img is None:
                time.sleep(0.1)
                continue
            btn_exists = any(d.name == btn_name for d in dets)
            if not btn_exists:
                time.sleep(confirm_wait)
                cv_img2, dets2 = self._quick_detect(rect, [btn_name])
                if cv_img2 is not None:
                    if not any(d.name == btn_name for d in dets2):
                        return True
            else:
                time.sleep(0.1)
        return False

    # ── 返回主页 ────────────────────────────────────────────────

    def _back_to_home(self, rect: tuple) -> bool:
        """返回主页"""
        for attempt in range(5):
            if self.stopped:
                return False

            cv_img, dets = self._quick_detect(rect,
                ["btn_home", "btn_close", "btn_rw_close",
                 "btn_shop", "btn_warehouse", "ui_goto_friend"])
            if cv_img is None:
                time.sleep(0.3)
                continue

            names = {d.name for d in dets}
            if names & {"btn_shop", "btn_warehouse", "ui_goto_friend"}:
                return True

            home_btn = self._find_any_name(dets, ["btn_home", "btn_close", "btn_rw_close"])
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
        """快速截屏+定向检测"""
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
        """仅截屏"""
        if self._capture_fn:
            cv_img, _, _ = self._capture_fn(rect, save=False)
            if cv_img is not None:
                return cv_img
        return None

    def _swipe_friend_list(self, rect: tuple):
        """滑动底部好友列表"""
        if not self.action_executor:
            return
        h, w = REF_WINDOW_SIZE[1], REF_WINDOW_SIZE[0]
        sx, sy = _scale_pos(SWIPE_START, h, w)
        ex, ey = _scale_pos(SWIPE_END, h, w)
        dx, dy = ex - sx, ey - sy
        abs_sx = self.action_executor._window_left + sx
        abs_sy = self.action_executor._window_top + sy
        self.action_executor.drag(abs_sx, abs_sy, dx, dy, duration=0.3, steps=10)

    def _find_any_name(self, dets: list[DetectResult], names: list[str]) -> DetectResult | None:
        name_set = set(names)
        for d in dets:
            if d.name in name_set:
                return d
        return None

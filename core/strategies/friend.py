"""P4 社交 — 好友巡查/偷菜/帮忙

参考 qq-farm-copilot 的 TaskFriend 实现：
  主页 → 固定坐标点击好友按钮 → 好友列表页
  → 固定坐标点击访问 → 进入好友农场
  → 一次截图检测所有动作 + 底部好友列表 icon
  → 执行偷菜/帮忙 → 点击 icon 切换好友（含滑动翻页）
  → 完成后模板匹配 btn_home 回家
"""
import time
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.scene_detector import Scene, identify_scene
from core.strategies.base import BaseStrategy

MAX_FRIENDS_PER_ROUND = 20

# 固定坐标（参考 qq-farm-copilot assets.py，基于 581x1054 窗口）
REF_WINDOW_SIZE = (581, 1054)

FRIEND_BTN_POS = (470, 878)     # main_goto_friend
VISIT_BTN_POS = (461, 304)      # btn_visit_first
STEAL_BTN_POS = (266, 706)      # btn_steal
HOME_BTN_POS = (487, 713)       # btn_home
CLOSE_BTN_POS = (510, 71)       # btn_close

# 底部好友列表滑动区域（detail 页面下的横向好友列表）
SWIPE_START = (405, 860)
SWIPE_END = (150, 860)

# 一次截图检测的全部好友相关模板
_ALL_FRIEND_TEMPLATES = [
    # 操作按钮
    "btn_water", "btn_weed", "btn_bug", "btn_steal",
    # 底部好友列表 icon
    "icon_steal_in_friend_detail",
    "icon_water_in_friend_detail",
    "icon_weed_in_friend_detail",
    "icon_bug_in_friend_detail",
    # 导航
    "btn_home", "btn_close", "btn_rw_close",
    # 场景判断
    "btn_shop", "btn_warehouse", "ui_goto_friend",
    "btn_claim", "btn_confirm", "btn_cancel",
    "btn_info", "btn_info_close", "btn_shop_close",
    "ui_remote_login", "ui_next_time",
]

_SCALES_FAST = [1.0, 0.9, 1.1]


def _scale_pos(pos: tuple, img_h: int, img_w: int) -> tuple[int, int]:
    return (
        int(pos[0] * img_w / REF_WINDOW_SIZE[0]),
        int(pos[1] * img_h / REF_WINDOW_SIZE[1]),
    )


class FriendStrategy(BaseStrategy):

    # ── 主入口 ──────────────────────────────────────────────────

    def run_friend_round(self, rect: tuple,
                         enable_steal: bool = True,
                         enable_help: bool = True) -> list[str]:
        actions: list[str] = []
        if self.stopped:
            return actions
        if not enable_steal and not enable_help:
            logger.info("好友流程: 未启用偷菜和帮忙，跳过")
            return actions

        logger.info(f"好友流程: 开始 | 偷菜={enable_steal} 帮忙={enable_help}")

        # 1. 进入好友列表
        if not self._enter_friend_list(rect):
            logger.warning("好友流程: 无法进入好友列表")
            return actions

        # 2. 进入第一个好友农场
        if not self._enter_friend_detail(rect):
            logger.info("好友流程: 未找到可访问的好友，结束")
            self._back_to_home(rect)
            return actions

        # 3. 遍历好友
        steal_count = 0
        help_count = 0
        for i in range(MAX_FRIENDS_PER_ROUND):
            if self.stopped:
                break

            # 一次截图 + 全量检测
            cv_img, dets = self._friend_detect(rect)
            if cv_img is None:
                break

            # 偷菜
            if enable_steal:
                if self._do_steal(cv_img, dets):
                    steal_count += 1

            # 帮忙
            if enable_help:
                helped = self._do_help(cv_img, dets)
                help_count += len(helped)

            # 切换下一位好友（含滑动）
            if not self._goto_next_friend(cv_img, dets, rect,
                                          enable_steal, enable_help):
                break

        # 4. 回家
        self._back_to_home(rect)

        compact = []
        if steal_count > 0:
            compact.append(f"偷菜x{steal_count}")
        if help_count > 0:
            compact.append(f"帮忙x{help_count}")
        logger.info(f"好友流程: 结束 | {', '.join(compact) or '无操作'}")
        return actions

    # ── 导航 ────────────────────────────────────────────────────

    def _enter_friend_list(self, rect: tuple) -> bool:
        """从主页点击好友按钮进入好友列表页（模板匹配优先）"""
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

            # 农场主页或未知场景 → 模板匹配找好友按钮
            if scene in (Scene.FARM_OVERVIEW, Scene.UNKNOWN):
                btn = self._find_any_name(dets, ["ui_goto_friend", "btn_haoyou"])
                if btn:
                    self.click(btn.x, btn.y, f"点击好友按钮({btn.name})")
                else:
                    # 模板匹配失败才用固定坐标兜底
                    h, w = cv_img.shape[:2]
                    fx, fy = _scale_pos(FRIEND_BTN_POS, h, w)
                    self.click(fx, fy, "点击好友按钮(坐标兜底)")

                time.sleep(1.0)
                if self._wait_left_farm(rect, timeout=4.0):
                    return True

        logger.warning("进入好友列表失败")
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

    def _enter_friend_detail(self, rect: tuple) -> bool:
        """从好友列表进入好友农场（模板匹配优先）"""
        for attempt in range(2):
            if self.stopped:
                return False

            cv_img, dets = self._quick_detect(rect, ["btn_visit_first", "btn_home"])
            if cv_img is None:
                time.sleep(0.3)
                continue

            # 已在好友农场
            if any(d.name == "btn_home" for d in dets):
                logger.info("已在好友农场")
                return True

            # 模板匹配找访问按钮
            btn = self._find_any_name(dets, ["btn_visit_first"])
            if btn:
                self.click(btn.x, btn.y, "访问好友")
            else:
                h, w = cv_img.shape[:2]
                fx, fy = _scale_pos(VISIT_BTN_POS, h, w)
                self.click(fx, fy, "访问好友(坐标兜底)")

            time.sleep(1.0)

            for _ in range(6):
                if self.stopped:
                    return False
                cv_img, dets = self._quick_detect(rect, ["btn_home"])
                if cv_img is None:
                    time.sleep(0.3)
                    continue
                if any(d.name == "btn_home" for d in dets):
                    logger.info("已进入好友农场")
                    return True
                time.sleep(0.3)

        return False

    # ── 偷菜 ────────────────────────────────────────────────────

    def _do_steal(self, cv_img, dets: list[DetectResult]) -> bool:
        """基于已有检测结果执行偷菜"""
        btn = self._find_any_name(dets, ["btn_steal"])
        if btn:
            self.click(btn.x, btn.y, "偷菜", ActionType.STEAL)
            time.sleep(0.3)
            return True

        # icon 确认 + 固定坐标
        if any(d.name == "icon_steal_in_friend_detail" for d in dets):
            h, w = cv_img.shape[:2]
            fx, fy = _scale_pos(STEAL_BTN_POS, h, w)
            self.click(fx, fy, "偷菜(坐标)", ActionType.STEAL)
            time.sleep(0.3)
            return True

        return False

    # ── 帮忙 ────────────────────────────────────────────────────

    def _do_help(self, cv_img, dets: list[DetectResult]) -> list[str]:
        """基于已有检测结果执行帮忙"""
        actions_done: list[str] = []

        for btn_name, desc, action_type in [
            ("btn_water", "帮好友浇水", ActionType.HELP_WATER),
            ("btn_weed", "帮好友除草", ActionType.HELP_WEED),
            ("btn_bug", "帮好友除虫", ActionType.HELP_BUG),
        ]:
            if self.stopped:
                break
            btn = self._find_any_name(dets, [btn_name])
            if btn:
                self.click(btn.x, btn.y, desc, action_type)
                actions_done.append(desc)
                time.sleep(0.3)

        return actions_done

    # ── 好友切换（含滑动翻页）────────────────────────────────────

    def _goto_next_friend(self, cv_img, dets: list[DetectResult],
                          rect: tuple,
                          enable_steal: bool, enable_help: bool) -> bool:
        """切换到下一位可操作的好友，无结果时滑动翻页"""
        if self.stopped:
            return False

        icon_names = []
        if enable_steal:
            icon_names.append("icon_steal_in_friend_detail")
        if enable_help:
            icon_names.extend([
                "icon_water_in_friend_detail",
                "icon_weed_in_friend_detail",
                "icon_bug_in_friend_detail",
            ])

        # 第一次尝试：当前截图的检测结果
        candidate = self._find_friend_icon(cv_img, dets, icon_names)
        if candidate:
            logger.info(f"切换好友: 点击 {candidate.name} ({candidate.x}, {candidate.y})")
            self.click(candidate.x, candidate.y, "切换好友")
            time.sleep(0.8)
            return True

        # 滑动翻页重试
        for swipe_attempt in range(2):
            if self.stopped:
                return False
            logger.debug(f"滑动好友列表 (第{swipe_attempt+1}次)")
            self._swipe_friend_list(rect)
            time.sleep(0.5)

            cv_img2, dets2 = self._quick_detect(rect, icon_names + ["btn_home"])
            if cv_img2 is None:
                continue
            candidate = self._find_friend_icon(cv_img2, dets2, icon_names)
            if candidate:
                logger.info(f"滑动后切换好友: 点击 {candidate.name} ({candidate.x}, {candidate.y})")
                self.click(candidate.x, candidate.y, "切换好友")
                time.sleep(0.8)
                return True

        logger.info("未找到更多可操作好友")
        return False

    def _find_friend_icon(self, cv_img, dets: list[DetectResult],
                          icon_names: list[str]) -> DetectResult | None:
        """在底部好友列表区域找可操作的 icon"""
        h, w = cv_img.shape[:2]
        x_min = int(w * 0.12)
        x_max = int(w * 0.93)
        y_min = int(h * 0.75)   # ~790, 涵盖 y=807
        y_max = int(h * 0.83)   # ~874, 排除 y=940+

        name_set = set(icon_names)
        # 日志：列出所有匹配 icon 的位置（不管是否在区域内）
        all_icons = [d for d in dets if d.name in name_set]
        if all_icons:
            icon_info = ", ".join(f"{d.name}({d.x},{d.y})" for d in all_icons)
            logger.debug(f"好友icon检测: 区域=[{x_min}-{x_max},{y_min}-{y_max}] | {icon_info}")

        candidates = []
        for d in dets:
            if d.name in name_set and x_min <= d.x <= x_max and y_min <= d.y <= y_max:
                candidates.append(d)

        if candidates:
            candidates.sort(key=lambda d: d.x)
            return candidates[0]
        return None

    def _swipe_friend_list(self, rect: tuple):
        """滑动底部好友列表（向左滑动查看更多好友）"""
        if not self.action_executor:
            return
        h, w = REF_WINDOW_SIZE[1], REF_WINDOW_SIZE[0]
        sx, sy = _scale_pos(SWIPE_START, h, w)
        ex, ey = _scale_pos(SWIPE_END, h, w)
        dx, dy = ex - sx, ey - sy

        abs_sx = self.action_executor._window_left + sx
        abs_sy = self.action_executor._window_top + sy
        logger.debug(f"滑动好友列表: ({abs_sx},{abs_sy}) dx={dx} dy={dy}")

        self.action_executor.drag(abs_sx, abs_sy, dx, dy,
                                  duration=0.3, steps=10)

    # ── 返回主页 ────────────────────────────────────────────────

    def _back_to_home(self, rect: tuple) -> bool:
        """返回主页（模板匹配 btn_home）"""
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
                logger.info("已返回主页")
                return True

            home_btn = self._find_any_name(dets, ["btn_home", "btn_close", "btn_rw_close"])
            if home_btn:
                self.click(home_btn.x, home_btn.y, f"返回({home_btn.name})")
            else:
                h, w = cv_img.shape[:2]
                fx, fy = _scale_pos(HOME_BTN_POS, h, w)
                self.click(fx, fy, "返回主页(坐标兜底)")
            time.sleep(0.8)

        logger.warning("返回主页失败")
        return False

    # ── 检测工具 ────────────────────────────────────────────────

    def _friend_detect(self, rect: tuple):
        """好友农场一次截图 + 全量检测"""
        if not self.cv_detector._loaded:
            self.cv_detector.load_templates()
        cv_img = self._capture_only(rect)
        if cv_img is None:
            return None, []
        detections = self.cv_detector.detect_targeted(
            cv_img, _ALL_FRIEND_TEMPLATES, scales=_SCALES_FAST
        )
        det_names = [f"{d.name}({d.confidence:.0%})" for d in detections[:10]]
        logger.debug(f"好友农场检测: {len(detections)}个 | {', '.join(det_names)}")
        return cv_img, detections

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
        """仅截屏返回 cv_image"""
        if self._capture_fn:
            cv_img, _, _ = self._capture_fn(rect, save=False)
            if cv_img is not None:
                return cv_img
        return None

    def _find_any_name(self, dets: list[DetectResult],
                       names: list[str]) -> DetectResult | None:
        name_set = set(names)
        for d in dets:
            if d.name in name_set:
                return d
        return None

    # ── 旧接口兼容 ──────────────────────────────────────────────

    def try_friend_help(self, rect: tuple,
                        detections: list[DetectResult]) -> list[str]:
        if self.stopped:
            return []
        btn = self._find_any_name(detections, ["btn_friend_help"])
        if not btn:
            return []
        self.click(btn.x, btn.y, "好友求助")
        time.sleep(0.3)
        # 需要重新截图检测帮忙按钮
        cv_img, dets = self._friend_detect(rect)
        if cv_img is None:
            return []
        return self._do_help(cv_img, dets)

    def try_steal(self, rect: tuple) -> bool:
        cv_img, dets = self._friend_detect(rect)
        if cv_img is None:
            return False
        return self._do_steal(cv_img, dets)

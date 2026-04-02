"""Bot引擎 — 主控编排层

四层架构：
  [1] 窗口控制层: window_manager + screen_capture
  [2] 图像识别层: cv_detector + scene_detector
  [3] 行为决策层: strategies/ (模块化策略)
  [4] 操作执行层: action_executor

优先级：
  P-1 异常处理: popup     — 关闭弹窗/商店/返回主界面
  P0  收益:     harvest   — 一键收获 + 自动出售
  P1  维护:     maintain  — 一键除草/除虫/浇水
  P2  生产:     plant     — 播种 + 购买种子 + 施肥
  P3  资源:     expand    — 扩建土地 + 领取任务
  P4  社交:     friend    — 好友巡查/帮忙/偷菜/同意好友
"""
import time
import cv2
import numpy as np
from PIL import Image as PILImage
from loguru import logger

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from models.config import AppConfig, PlantMode
from models.farm_state import ActionType
from models.game_data import get_best_crop_for_level, get_crop_by_name, format_grow_time
from core.window_manager import WindowManager
from core.screen_capture import ScreenCapture
from core.cv_detector import CVDetector, DetectResult
from core.action_executor import ActionExecutor
from core.task_scheduler import TaskScheduler, BotState
from core.scene_detector import Scene, identify_scene
from core.strategies import (
    PopupStrategy, HarvestStrategy, MaintainStrategy,
    PlantStrategy, ExpandStrategy, FriendStrategy, TaskStrategy,
)


class BotWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, engine: "BotEngine", task_type: str = "farm"):
        super().__init__()
        self.engine = engine
        self.task_type = task_type

    def run(self):
        try:
            if self.task_type == "farm":
                result = self.engine.check_farm()
            elif self.task_type == "friend":
                result = self.engine.check_friends()
            elif self.task_type == "test_fertilize":
                result = self.engine.test_fertilize_task()
            else:
                result = {"success": False, "message": "未知任务类型"}
            self.finished.emit(result)
        except Exception as e:
            logger.exception(f"任务执行异常: {e}")
            self.error.emit(str(e))


class BotEngine(QObject):
    log_message = pyqtSignal(str)
    screenshot_updated = pyqtSignal(object)
    state_changed = pyqtSignal(str)
    stats_updated = pyqtSignal(dict)
    detection_result = pyqtSignal(object)

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config

        # [1] 窗口控制层
        self.window_manager = WindowManager()
        self.screen_capture = ScreenCapture()

        # [2] 图像识别层
        self.cv_detector = CVDetector(templates_dir="templates")

        # [3] 行为决策层（按优先级）
        self.popup = PopupStrategy(self.cv_detector)       # P-1
        self.harvest = HarvestStrategy(self.cv_detector)    # P0
        self.maintain = MaintainStrategy(self.cv_detector)  # P1
        self.plant = PlantStrategy(self.cv_detector)        # P2
        self.expand = ExpandStrategy(self.cv_detector)      # P3
        self.task = TaskStrategy(self.cv_detector)          # P3.5
        self.friend = FriendStrategy(self.cv_detector)      # P4
        self._strategies = [self.popup, self.harvest, self.maintain,
                            self.plant, self.expand, self.task, self.friend]

        # [4] 操作执行层
        self.action_executor: ActionExecutor | None = None

        # 调度
        self.scheduler = TaskScheduler()
        self._worker: BotWorker | None = None
        self._is_busy = False
        self._planted = False  # 标记是否已播种完成，等待收获
        self._fertilized = False  # 标记是否已施肥


        self.scheduler.farm_check_triggered.connect(self._on_farm_check)
        self.scheduler.friend_check_triggered.connect(self._on_friend_check)
        self.scheduler.state_changed.connect(self.state_changed.emit)
        self.scheduler.stats_updated.connect(self.stats_updated.emit)

    def _init_strategies(self):
        """初始化所有策略的依赖"""
        for s in self._strategies:
            s.action_executor = self.action_executor
            s.set_capture_fn(self._capture_and_detect)
            s._stop_requested = False
        self.task.sell_config = self.config.sell
        self.plant.auto_buy_seed = self.config.features.auto_buy_seed
        self.plant.auto_fertilize = self.config.features.auto_fertilize

    def update_config(self, config: AppConfig):
        self.config = config
        self.task.sell_config = config.sell
        self.plant.auto_buy_seed = config.features.auto_buy_seed
        self.plant.auto_fertilize = self.config.features.auto_fertilize

    def _resolve_crop_name(self) -> str:
        """根据策略决定种植作物"""
        planting = self.config.planting
        if planting.strategy == PlantMode.BEST_EXP_RATE:
            best = get_best_crop_for_level(planting.player_level)
            if best:
                logger.info(f"策略选择: {best[0]} (经验效率 {best[4]/best[3]:.4f}/秒)")
                return best[0]
        return planting.preferred_crop

    def _clear_screen(self, rect: tuple):
        """点击窗口顶部天空区域，关闭残留弹窗/菜单/土地信息

        点击位置：水平居中，垂直 5% 处（天空区域，不会触发任何游戏操作）。
        连续点击 2 次，间隔 0.3 秒等待动画消失。
        """
        if not self.action_executor:
            return
        w, h = rect[2], rect[3]
        sky_x = w // 2
        sky_y = int(h * 0.05)
        for _ in range(2):
            if self.popup.stopped:
                return
            # 使用策略的 click 方法，自动检查停止标志
            self.popup.click(sky_x, sky_y, "清屏")
            time.sleep(0.3)


    def start(self) -> bool:
        # 重置状态
        self._planted = False
        self._fertilized = False

        self.cv_detector.load_templates()
        tpl_count = sum(len(v) for v in self.cv_detector._templates.values())
        if tpl_count == 0:
            self.log_message.emit("未找到模板图片，请先运行模板采集工具")
            return False

        window = self.window_manager.find_window(self.config.window_title_keyword, auto_launch=True, shortcut_path=self.config.planting.game_shortcut_path)
        if not window:
            self.log_message.emit("启动游戏失败，请检查快捷方式路径是否正确" if self.config.planting.game_shortcut_path else "未找到 QQ 农场窗口，请先打开微信小程序中的 QQ 农场")
            return False

        w, h = self.config.planting.window_width, self.config.planting.window_height
        if w > 0 and h > 0:
            # 等待游戏自适应完成
            time.sleep(2)
            self.window_manager.resize_window(w, h)
            time.sleep(0.5)
            # 使用缓存的窗口信息，不重新搜索
            window = self.window_manager._cached_window
            if window:
                self.log_message.emit(f"窗口已调整为 {window.width}x{window.height}")

        rect = (window.left, window.top, window.width, window.height)
        self.action_executor = ActionExecutor(
            window_rect=rect,
            delay_min=self.config.safety.random_delay_min,
            delay_max=self.config.safety.random_delay_max,
            click_offset=self.config.safety.click_offset_range,
        )
        self._init_strategies()

        farm_ms = self.config.schedule.farm_check_minutes * 60 * 1000
        friend_ms = self.config.schedule.friend_check_minutes * 60 * 1000
        self.scheduler.start(farm_ms, friend_ms)
        self.log_message.emit(f"Bot已启动 - 窗口: {window.title} | 模板: {tpl_count}个")
        return True

    def stop(self):
        """停止 Bot - 立即停止所有操作"""
        logger.info("停止请求：设置停止标志")
        # 1. 设置所有策略的停止标志
        for s in self._strategies:
            s._stop_requested = True

        # 2. 停止调度器（停止定时器）
        self.scheduler.stop()

        # 3. 循环等待当前正在运行的 Worker 完成，直到成功停止
        if self._worker and self._worker.isRunning():
            logger.info("等待当前任务完成...")
            retry_count = 0
            while self._worker.isRunning():
                # 每次等待 5 秒
                elapsed = 0
                while self._worker.isRunning() and elapsed < 5000:
                    time.sleep(0.1)
                    elapsed += 100

                if self._worker.isRunning():
                    retry_count += 1
                    logger.warning(f"任务未能及时停止 (第{retry_count}次重试)，继续尝试停止...")
                    # 重试停止流程：再次设置停止标志
                    for s in self._strategies:
                        s._stop_requested = True

            logger.info(f"任务已停止，共重试 {retry_count} 次")

        # 4. 重置状态（在 Worker 完成后）
        self._is_busy = False

        # 5. 重置策略停止标志（为下次启动做准备）
        for s in self._strategies:
            s._stop_requested = False

        self.log_message.emit("Bot 已停止")

    def pause(self):
        for s in self._strategies:
            s._stop_requested = True
        self.scheduler.pause()

    def resume(self):
        for s in self._strategies:
            s._stop_requested = False
        self.scheduler.resume()

    def run_once(self):
        self._on_farm_check()

    def test_fertilize(self):
        """测试施肥流程"""
        logger.info("=== 开始施肥测试 ===")

        # 先设置 _is_busy，阻止新任务启动
        self._is_busy = True

        # 停止调度器，防止定时器触发干扰测试
        self.scheduler.stop()

        # 设置停止标志，停止任何正在运行的任务
        for s in self._strategies:
            s._stop_requested = True

        # 等待当前任务停止（最多等待 10 秒）
        elapsed = 0
        while elapsed < 10000:
            time.sleep(0.1)
            elapsed += 100
            # 等待 Worker 停止
            if not (self._worker and self._worker.isRunning()):
                break

        # 额外等待一下确保任务完全退出
        time.sleep(0.5)

        # 先初始化窗口和 action_executor（如果尚未初始化）
        rect = self._prepare_window()
        if not rect:
            logger.warning("测试施肥：窗口未找到")
            self.log_message.emit("窗口未找到，请先打开 QQ 农场")
            self._is_busy = False
            return

        # 如果 action_executor 为空，创建新的实例
        if not self.action_executor:
            self.action_executor = ActionExecutor(
                window_rect=rect,
                delay_min=self.config.safety.random_delay_min,
                delay_max=self.config.safety.random_delay_max,
                click_offset=self.config.safety.click_offset_range,
            )
            logger.info("创建新的 action_executor")

        # 重置策略停止标志，让测试任务可以正常执行
        for s in self._strategies:
            s._stop_requested = False

        # 重新初始化策略依赖（确保 _capture_fn 和 action_executor 已设置）
        self._init_strategies()

        # 确保 action_executor 已设置（双重检查）
        for s in self._strategies:
            if not s.action_executor:
                s.action_executor = self.action_executor
                logger.info(f"修复 {s.__class__.__name__} 的 action_executor")

        logger.info(f"action_executor={self.action_executor is not None}, rect={rect}")

        # 创建测试 Worker
        self._worker = BotWorker(self, "test_fertilize")
        # 测试完成后只重置 _is_busy，不触发其他逻辑
        self._worker.finished.connect(lambda r: self._on_test_finished(r))
        self._worker.error.connect(self._on_task_error)
        self._worker.start()

    def _on_test_finished(self, result: dict):
        """测试任务完成后的处理"""
        self._is_busy = False
        logger.info(f"施肥测试完成：{result.get('message', '无结果')}")
        # 测试完成后不自动恢复调度器，保持停止状态

    def test_fertilize_task(self) -> dict:
        """执行施肥测试任务"""
        result = {"success": False, "actions_done": [], "message": ""}
        logger.info("开始执行施肥测试任务...")

        # 重置策略停止标志（确保测试任务可以正常执行）
        for s in self._strategies:
            s._stop_requested = False

        # 确保 action_executor 已设置
        for s in self._strategies:
            if not s.action_executor:
                s.action_executor = self.action_executor

        # 双重检查 PlantStrategy 的 action_executor
        if not self.plant.action_executor:
            self.plant.action_executor = self.action_executor
            logger.info("修复 PlantStrategy.action_executor")
        logger.info(f"PlantStrategy: action_executor={self.plant.action_executor is not None}, stopped={self.plant.stopped}")

        rect = self._prepare_window()
        if not rect:
            result["message"] = "窗口未找到"
            return result

        # 先检测所有地块（land_开头的模板）
        logger.info(f"开始截屏检测，窗口区域：{rect}")
        cv_img, dets, _ = self._capture_and_detect(rect, prefix="test", save=False)
        if cv_img is None:
            result["message"] = "截屏失败"
            logger.warning("施肥测试：截屏返回 None")
            return result

        logger.info(f"检测到 {len(dets)} 个模板")
        if dets:
            template_summary = ", ".join(f"{d.name}({d.confidence:.0%})" for d in dets[:10])
            logger.info(f"检测到的模板：{template_summary}")

        # 找所有土地（包括空地和已播种）
        land_dets = [d for d in dets if d.name.startswith("land_")]
        if not land_dets:
            result["message"] = "未找到任何地块"
            logger.warning(f"施肥测试：未找到 land_ 开头的模板，检测到 {len(dets)} 个模板")
            return result

        self.log_message.emit(f"检测到 {len(land_dets)} 块土地，开始施肥测试...")
        logger.info(f"检测到 {len(land_dets)} 块土地，开始遍历检测已播种地块...")

        # 调用施肥方法，传入 is_test=True 让它遍历检测所有地块
        fa = self.plant.fertilize_all(rect, lands=None, is_test=True)
        logger.info(f"施肥流程完成，执行了 {len(fa)} 个操作：{fa}")
        if fa:
            result["actions_done"].extend(fa)
            result["success"] = True
            result["message"] = f"施肥完成：{', '.join(fa)}"
        else:
            result["message"] = "施肥未完成，未找到已播种地块"

        return result

    def _on_farm_check(self):
        if self._is_busy:
            logger.debug("上一轮操作尚未完成，跳过")
            return
        self._is_busy = True
        self._worker = BotWorker(self, "farm")
        self._worker.finished.connect(self._on_task_finished)
        self._worker.error.connect(self._on_task_error)
        self._worker.start()

    def _on_friend_check(self):
        if self._is_busy:
            return
        if not self.config.features.auto_steal and not self.config.features.auto_help:
            return
        self._is_busy = True
        self._worker = BotWorker(self, "friend")
        self._worker.finished.connect(self._on_task_finished)
        self._worker.error.connect(self._on_task_error)
        self._worker.start()

    def _on_task_finished(self, result: dict):
        # 如果已停止，不处理结果
        if self.scheduler.state == BotState.IDLE:
            return
        self._is_busy = False
        actions = result.get("actions_done", [])
        if actions:
            self.log_message.emit(f"本轮完成: {', '.join(actions)}")
            # 记录每个操作的统计信息
            for action in actions:
                if "收获" in action:
                    self.scheduler.record_action("harvest")
                elif "播种" in action:
                    self.scheduler.record_action("plant")
                elif "浇水" in action:
                    self.scheduler.record_action("water")
                elif "除草" in action:
                    self.scheduler.record_action("weed")
                elif "除虫" in action:
                    self.scheduler.record_action("bug")
                elif "出售" in action:
                    self.scheduler.record_action("sell")
                elif "施肥" in action:
                    self.scheduler.record_action("fertilize")
        next_sec = result.get("next_check_seconds", 0)
        if next_sec > 0:
            self.scheduler.set_farm_interval(next_sec)

    def _on_task_error(self, error_msg: str):
        self._is_busy = False
        self.log_message.emit(f"操作异常: {error_msg}")

    # ============================================================
    # 截屏 + 检测
    # ============================================================

    def _prepare_window(self) -> tuple | None:
        # 优先使用缓存窗口，找不到再搜索
        window = self.window_manager._cached_window
        if not window:
            window = self.window_manager.refresh_window_info(
                self.config.window_title_keyword,
                auto_launch=True,
                shortcut_path=self.config.planting.game_shortcut_path
            )
        else:
            # 刷新缓存窗口的最新位置
            self.window_manager.find_window(self.config.window_title_keyword)
            window = self.window_manager._cached_window

        if not window:
            return None
        self.window_manager.activate_window()
        time.sleep(0.3)
        rect = (window.left, window.top, window.width, window.height)
        if self.action_executor:
            self.action_executor.update_window_rect(rect)
        return rect

    def _capture_and_detect(self, rect: tuple, prefix: str = "farm",
                            categories: list[str] | None = None,
                            save: bool = True
                            ) -> tuple[np.ndarray | None, list[DetectResult], PILImage.Image | None]:
        # 确保模板已加载
        if not self.cv_detector._loaded:
            logger.info("模板未加载，重新加载模板...")
            self.cv_detector.load_templates()
            logger.info(f"已加载 {len(self.cv_detector._templates)} 个类别的模板")

        if save:
            image, _ = self.screen_capture.capture_and_save(rect, prefix)
        else:
            image = self.screen_capture.capture_region(rect)
        if image is None:
            return None, [], None
        self.screenshot_updated.emit(image)
        cv_image = self.cv_detector.pil_to_cv2(image)

        if categories is not None:
            detections = []
            for cat in categories:
                detections += self.cv_detector.detect_category(cv_image, cat, threshold=0.8)
            detections = self.cv_detector._nms(detections, iou_threshold=0.5)
        else:
            detections = []
            for cat in self.cv_detector._templates:
                if cat in ("seed", "shop"):
                    continue
                if cat == "land":
                    thresh = 0.7
                elif cat == "button":
                    thresh = 0.8
                else:
                    thresh = 0.8
                detections += self.cv_detector.detect_category(cv_image, cat, threshold=thresh)
            detections = [d for d in detections
                          if d.name != "btn_shop_close"
                          and not (d.name == "btn_expand" and d.confidence < 0.85)]

        return cv_image, detections, image

    def _emit_annotated(self, cv_image: np.ndarray, detections: list[DetectResult]):
        if detections:
            annotated = self.cv_detector.draw_results(cv_image, detections)
            annotated_rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
            self.detection_result.emit(PILImage.fromarray(annotated_rgb))

    def _record_stat(self, action_type: str):
        type_map = {
            ActionType.HARVEST: "harvest", ActionType.PLANT: "plant",
            ActionType.WATER: "water", ActionType.WEED: "weed",
            ActionType.BUG: "bug", ActionType.STEAL: "steal",
            ActionType.SELL: "sell",
        }
        stat_key = type_map.get(action_type)
        if stat_key:
            self.scheduler.record_action(stat_key)


    # ============================================================
    # 主循环
    # ============================================================

    def check_farm(self) -> dict:
        result = {"success": False, "actions_done": [], "next_check_seconds": 5}
        features = self.config.features.model_dump()

        rect = self._prepare_window()
        if not rect:
            result["message"] = "窗口未找到"
            return result

        # 清屏：点击天空区域关闭残留弹窗/菜单
        if not self.popup.stopped:
            self._clear_screen(rect)

        idle_rounds = 0
        max_idle = 3

        for round_num in range(1, 51):
            if self.popup.stopped:
                logger.info("收到停止/暂停信号，中断当前操作")
                break

            # 每轮检测窗口是否存在，窗口关闭时快速退出
            window = self.window_manager.refresh_window_info(
                self.config.window_title_keyword,
                auto_launch=False,
                shortcut_path=""
            )
            if not window:
                logger.warning("游戏窗口已关闭，中断操作")
                result["message"] = "窗口已关闭"
                break

            cv_image, detections, _ = self._capture_and_detect(rect, save=False)
            if cv_image is None:
                result["message"] = "截屏失败"
                break

            scene = identify_scene(detections, self.cv_detector, cv_image)
            det_summary = ", ".join(f"{d.name}({d.confidence:.0%})" for d in detections[:6])
            logger.info(f"[轮{round_num}] 场景={scene.value} | {det_summary}")
            self._emit_annotated(cv_image, detections)

            action_desc = None

            # ---- P-1 异常处理 ----
            if scene == Scene.LEVEL_UP:
                action_desc = self.popup.handle_popup(detections)
                self.config.planting.player_level += 1
                self.config.save()
                new_level = self.config.planting.player_level
                self.log_message.emit(f"升级! Lv.{new_level - 1} → Lv.{new_level}")
                self.log_message.emit(f"当前种植: {self._resolve_crop_name()}")
            elif scene == Scene.POPUP:
                action_desc = self.popup.handle_popup(detections)
            elif scene == Scene.INFO_PAGE:
                if self.popup.stopped:
                    logger.info("收到停止/暂停信号，中断当前操作")
                    break
                # 优先使用 btn_close，其次 btn_info_close，再使用 btn_rw_close
                info_close = self.popup.find_any(detections, ["btn_close", "btn_info_close", "btn_rw_close"])
                if info_close:
                    self.popup.click(info_close.x, info_close.y, "关闭个人信息页面")
                    action_desc = "关闭个人信息页面"
                else:
                    # 找不到关闭按钮，可能是模板匹配问题，等待下一轮检测
                    logger.debug("个人信息页面：未找到关闭按钮，等待下轮检测")
                    action_desc = "等待关闭个人信息页面"
            elif scene == Scene.BUY_CONFIRM:
                action_desc = self.popup.handle_popup(detections)
            elif scene == Scene.SHOP_PAGE:
                self.popup.close_shop(rect)
                action_desc = "关闭商店"
            elif scene == Scene.PLOT_MENU:
                action_desc = self.popup.handle_popup(detections)

            # ---- 农场主页操作 ----
            elif scene == Scene.FARM_OVERVIEW:
                logger.debug(f"农场主页操作：auto_harvest={features.get('auto_harvest', True)}, _planted={self._planted}")
                # P0 收益：一键收获
                if not action_desc and features.get("auto_harvest", True):
                    action_desc = self.harvest.try_harvest(detections)
                    # 收获后重置播种和施肥状态，可以重新检测空地
                    if action_desc:
                        self._planted = False
                        self._fertilized = False

                # P1 维护：除草/除虫/浇水
                if not action_desc:
                    action_desc = self.maintain.try_maintain(detections, features)

                # P2 生产：播种（仅在未播种状态下执行）
                logger.debug(f"播种检查：action_desc={action_desc}, auto_plant={features.get('auto_plant', True)}, _planted={self._planted}")
                if not action_desc and features.get("auto_plant", True) and not self._planted:
                    crop_name = self._resolve_crop_name()
                    logger.info(f"开始播种：{crop_name}, auto_fertilize={features.get('auto_fertilize', False)}")
                    # 如果开启了自动施肥，传入 auto_fertilize=True，播种完成后自动施肥
                    pa = self.plant.plant_all(rect, crop_name,
                                              auto_fertilize=features.get("auto_fertilize", False))
                    if pa:
                        result["actions_done"].extend(pa)
                        action_desc = pa[-1]
                        self._planted = True  # 标记已播种
                        self._fertilized = True  # 如果施肥了也标记为已施肥
                    else:
                        logger.info("播种流程未执行任何操作（可能没有空地）")

                # P3 资源：扩建
                if not action_desc and features.get("auto_upgrade", True):
                    action_desc = self.expand.try_expand(rect, detections)

                # P3.5 任务：领取奖励 / 售卖果实
                if not action_desc and features.get("auto_task", True):
                    ta = self.task.try_task(rect, detections)
                    if ta:
                        result["actions_done"].extend(ta)
                        action_desc = ta[-1]

                # P4 社交：好友求助
                if not action_desc and features.get("auto_help", True):
                    fa = self.friend.try_friend_help(rect, detections)
                    if fa:
                        result["actions_done"].extend(fa)
                        action_desc = fa[-1]

            # ---- 好友家园 ----
            elif scene == Scene.FRIEND_FARM:
                fa = self.friend._help_in_friend_farm(rect)
                if fa:
                    result["actions_done"].extend(fa)
                    action_desc = fa[-1]

            elif scene == Scene.SEED_SELECT:
                crop_name = self._resolve_crop_name()
                seed = self.popup.find_by_name(detections, f"seed_{crop_name}")
                if seed:
                    self.popup.click(seed.x, seed.y, f"播种{crop_name}", ActionType.PLANT)
                    self._record_stat(ActionType.PLANT)
                    action_desc = f"播种{crop_name}"

            elif scene == Scene.UNKNOWN:
                self.popup.click_blank(rect)
                action_desc = "点击空白处"

            # ---- 结果处理 ----
            if action_desc:
                result["actions_done"].append(action_desc)
                idle_rounds = 0
            else:
                idle_rounds += 1
                if idle_rounds == 1:
                    self.popup.click_blank(rect)
                elif idle_rounds >= max_idle:
                    break

            time.sleep(0.3)

        # 设置下次检查间隔
        # 有播种操作 → 5分钟后检查维护（除虫/除草/浇水）
        # 无播种操作 → 30秒后再检查（可能有新状态）
        has_planted = any("播种" in a for a in result.get("actions_done", []))
        if has_planted:
            interval = self.config.schedule.farm_check_minutes * 60
            result["next_check_seconds"] = interval
            crop_name = self._resolve_crop_name()
            crop = get_crop_by_name(crop_name)
            if crop:
                grow_time = crop[3]
                logger.info(f"已播种{crop_name}，{format_grow_time(grow_time)}后成熟，每{self.config.schedule.farm_check_minutes}分钟检查维护")
        else:
            result["next_check_seconds"] = 30

        result["success"] = True
        self.screen_capture.cleanup_old_screenshots(0)
        return result

    def check_friends(self) -> dict:
        result = {"success": True, "actions_done": [], "next_check_seconds": 1800}
        logger.info("好友巡查功能开发中...")
        return result

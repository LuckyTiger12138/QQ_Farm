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
import os
import re
import sys
import time
import cv2
import numpy as np
from PIL import Image as PILImage
from loguru import logger

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from models.config import AppConfig, PlantMode, RunMode, TaskScheduleItemConfig
from models.farm_state import ActionType
from models.game_data import get_best_crop_for_level, get_crop_by_name, format_grow_time
from core.window_manager import WindowManager
from core.screen_capture import ScreenCapture
from core.cv_detector import CVDetector, DetectResult
from core.action_executor import ActionExecutor
from core.task_scheduler import TaskScheduler, BotState
from core.scene_detector import Scene, identify_scene
from core.silent_hours import is_silent_time, get_silent_remaining_seconds
from core.task_executor import (
    TaskExecutor as AsyncTaskExecutor,
    TaskItem, TaskResult, TaskContext, TaskSnapshot,
    build_task_item,
)
from tasks.land_scan import LandScanTask
from core.strategies import (
    PopupStrategy, HarvestStrategy, MaintainStrategy,
    PlantStrategy, ExpandStrategy, FriendStrategy, TaskStrategy,
    GiftStrategy, TargetedStealStrategy,
)
from core.ui.navigator import Navigator
from core.cross_instance_bus import CrossInstanceBus, StealAlert


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


# ── 快速检测用的模板名称集合 ──────────────────────────────────────
# 只包含场景判断和农场操作所需的模板（跳过 seed/shop 等 70+ 模板）
SCENE_TEMPLATES = [
    # 弹窗指标
    "btn_close", "btn_info", "btn_info_close", "btn_buy_confirm", "btn_buy_max",
    "btn_shop_close", "btn_shop", "btn_claim", "btn_rw_close",
    "btn_share", "btn_confirm", "btn_cancel",
    # 场景指标
    "btn_home", "btn_zhongzi", "btn_warehouse",
    "btn_plant", "btn_remove", "btn_fertilize",
    # 农场操作按钮
    "btn_harvest", "btn_weed", "btn_bug", "btn_water",
    "btn_expand", "btn_friend_help", "btn_task",
    "btn_steal", "btn_visit_first", "btn_batch_sell", "btn_sell",
    "friend_check", "btn_friend_apply", "btn_friend_agreed",
    "ui_goto_friend",
    "icon_steal_in_friend_detail",
    "icon_water_in_friend_detail",
    "icon_weed_in_friend_detail",
    "icon_bug_in_friend_detail",
    # 状态图标
    "icon_mature", "icon_bug", "icon_water",
    # 地块状态图标（地块巡查用）
    "icon_land_stand", "icon_land_red", "icon_land_black",
    "icon_land_gold", "icon_land_gold_2",
    "btn_land_right", "btn_land_left",
    "btn_expand_brand", "btn_land_pop_empty",
    "btn_crop_removal", "btn_crop_maturity_time_suffix",
    # UI 元素（异地登录等）
    "ui_remote_login", "ui_next_time", "icon_levelup",
    "ui_shangcheng", "btn_shangcehng_fanhui",
    # 页面导航标识（商城/邮件/菜单）
    "mall_check", "mail_check", "menu_check",
    "main_goto_mall", "main_goto_menu", "menu_goto_mail",
]

LAND_TEMPLATES = [
    f"land_empty{i}" for i in ["", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
] + ["land_金1", "land_金21"]

MAINTAIN_TEMPLATES = ["btn_weed", "btn_bug", "btn_water"]


class BotEngine(QObject):
    log_message = pyqtSignal(str)
    screenshot_updated = pyqtSignal(object)
    state_changed = pyqtSignal(str)
    stats_updated = pyqtSignal(dict)
    detection_result = pyqtSignal(object)
    _request_farm_check = pyqtSignal()
    config_updated = pyqtSignal(object)  # 配置更新信号，通知 GUI 刷新

    def __init__(self, config: AppConfig, instance_id: str = 'default',
                 cross_bus: CrossInstanceBus | None = None):
        super().__init__()
        self.config = config
        self.instance_id = instance_id  # 实例 ID，用于日志和截图分离
        self._cross_bus = cross_bus  # 跨实例消息总线
        
        # 老板键状态
        self._game_hidden = False  # 游戏窗口是否已隐藏

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
        self.gift = GiftStrategy(self.cv_detector)          # P5 礼品领取
        self.targeted_steal = TargetedStealStrategy(self.cv_detector)  # 定点偷菜（大小号通讯）
        self._strategies = [self.popup, self.harvest, self.maintain,
                            self.plant, self.expand, self.task, self.friend, self.gift,
                            self.targeted_steal]

        # [4] 操作执行层
        self.action_executor: ActionExecutor | None = None

        # 调度
        self.scheduler = TaskScheduler()
        self._worker: BotWorker | None = None
        self._is_busy = False
        self._planted = False  # 标记是否已播种完成，等待收获
        self._fertilized = False  # 标记是否已施肥
        self._planted_idle_rounds = 0  # 已播种但未检测到收获按钮的连续轮数

        # 新版异步执行器
        self._async_executor: AsyncTaskExecutor | None = None
        self._task_snapshots: TaskSnapshot | None = None
        self._bot_stop_requested = False  # 通用停止标志（任务级别）

        # OCR 工具（延迟初始化）
        self._head_info_ocr = None
        self._land_scan_task: LandScanTask | None = None


        self.scheduler.farm_check_triggered.connect(self._on_farm_check)
        self.scheduler.friend_check_triggered.connect(self._on_friend_check)
        self._request_farm_check.connect(self._on_farm_check)  # 内部信号连接
        self.scheduler.state_changed.connect(self.state_changed.emit)
        self.scheduler.stats_updated.connect(self.stats_updated.emit)
        self.scheduler.window_lost.connect(self._on_window_lost)
        self.scheduler.set_window_check_fn(self._is_window_alive)

    def _ensure_head_info_ocr(self):
        """延迟初始化 HeadInfoOCR。"""
        if self._head_info_ocr is not None:
            return self._head_info_ocr
        try:
            from utils.head_info_ocr import HeadInfoOCR
            from utils.ocr_provider import get_ocr_tool
            self._head_info_ocr = HeadInfoOCR(ocr_tool=get_ocr_tool())
        except Exception as e:
            logger.debug(f"HeadInfoOCR 初始化失败: {e}")
            self._head_info_ocr = None
        return self._head_info_ocr

    def _ensure_land_scan_task(self) -> LandScanTask | None:
        """延迟初始化 LandScanTask。"""
        if self._land_scan_task is not None:
            return self._land_scan_task
        try:
            from utils.ocr_provider import get_ocr_tool
            self._land_scan_task = LandScanTask(ocr_tool=get_ocr_tool())
        except Exception as e:
            logger.debug(f"LandScanTask 初始化失败: {e}")
            self._land_scan_task = None
        return self._land_scan_task

    def _ensure_main_scene(self, rect: tuple):
        """确保游戏回到主界面（关闭弹窗）。"""
        cv_img, detections = self._fast_capture_and_detect(rect)
        if cv_img is None:
            return
        popup = self.popup
        if popup:
            popup.handle_popup(detections)

    # 策略用快速截屏检测所需的额外模板
    _STRATEGY_EXTRA_TEMPLATES = [
        "btn_expand_confirm", "btn_fertilize_popup",
        "bth_feiliao2_yj", "bth_feiliao_pt",
        "btn_batch_sell", "btn_sell", "btn_cangku",
        "btn_haoyou", "btn_task",
        # 礼品领取相关
        "btn_qqsvip", "btn_mall_free", "btn_mall_free_done",
        "btn_oneclick_open", "mall_goto_main", "btn_shangcehng_fanhui",
    ]

    def _init_strategies(self):
        """初始化所有策略的依赖"""
        for s in self._strategies:
            s.action_executor = self.action_executor
            s.set_capture_fn(self._fast_strategy_capture)
            s._stop_requested = False
        # 页面导航器
        def _nav_click(x: int, y: int, desc: str = "") -> None:
            """Navigator 用的点击函数，复用 Strategy 的 click 逻辑"""
            if self.action_executor and not self._bot_stop_requested:
                from models.farm_state import Action
                action = Action(type=ActionType.NAVIGATE,
                                click_position={"x": x, "y": y},
                                priority=0, description=desc)
                self.action_executor.execute_action(action)

        self._navigator = Navigator(
            capture_fn=self._fast_strategy_capture,
            click_fn=_nav_click,
            stopped_fn=lambda: self._bot_stop_requested,
        )
        self.gift.navigator = self._navigator
        self.plant.auto_buy_seed = self.config.features.auto_buy_seed
        self.plant.auto_fertilize = self.config.features.auto_fertilize
        # 好友策略配置
        self.friend.set_blacklist(self.config.features.friend.blacklist)
        self.friend._instance_id = self.instance_id

    def _fast_strategy_capture(self, rect: tuple, save: bool = False,
                                prefix: str = "strategy",
                                categories: list[str] | None = None
                                ) -> tuple[np.ndarray | None, list[DetectResult], None]:
        """策略专用快速截屏+检测：只扫描核心模板，3 个尺度"""
        cv_image = self._capture_only(rect)
        if cv_image is None:
            return None, [], None

        all_names = list(set(
            SCENE_TEMPLATES + LAND_TEMPLATES + self._STRATEGY_EXTRA_TEMPLATES
        ))
        detections = self.cv_detector.detect_targeted(
            cv_image, all_names, scales=[1.0, 0.9, 1.1]
        )
        return cv_image, detections, None

    def update_config(self, config: AppConfig):
        logger.info(
            f"⚙️ BotEngine[{self.instance_id}].update_config: config_id={id(config)} | "
            f"auto_harvest={config.features.auto_harvest} | cross_instance.enabled={config.cross_instance.enabled}"
        )
        self.config = config
        self.plant.auto_buy_seed = config.features.auto_buy_seed
        self.config_updated.emit(config)  # 通知 GUI 刷新

        # ✅ 同步任务执行器配置（间隔/启用状态等）
        if self._async_executor:
            self._sync_executor_tasks()

        # ✅ 重新加载模板元数据（确保修改的阈值配置生效）
        if hasattr(self.cv_detector, 'load_templates'):
            self.cv_detector.load_templates()
            logger.info("模板元数据（含阈值）已重新加载，新配置生效")

    def _resolve_crop_name(self) -> str:
        """根据策略决定种植作物"""
        planting = self.config.planting
        if planting.strategy == PlantMode.BEST_EXP_RATE:
            best = get_best_crop_for_level(planting.player_level)
            if best:
                logger.info(f"策略选择: {best[0]} (经验效率 {best[4]/best[3]:.4f}/秒)")
                return best[0]
        return planting.preferred_crop

    def _setup_instance_dirs(self):
        """初始化实例独立的目录（日志和截图）"""
        if self.instance_id == 'default':
            return  # 默认实例使用现有目录

        # 创建实例独立的截图目录
        screenshots_dir = f"screenshots/{self.instance_id}"
        os.makedirs(screenshots_dir, exist_ok=True)

        # 更新 screen_capture 的保存目录
        if hasattr(self.screen_capture, '_save_dir'):
            self.screen_capture._save_dir = screenshots_dir

        logger.info(f"实例 {self.instance_id} 目录已初始化: screenshots/{self.instance_id}/")

    def toggle_game_window(self) -> dict:
        """老板键：切换游戏窗口显示/隐藏（异步执行，不阻塞 GUI）
        
        隐藏方式：将窗口移出屏幕（坐标设为负值）
        - 屏幕上看不见窗口
        - Bot 可以继续截图（窗口技术上仍然可见）
        - 任务栏仍然显示图标（Windows 限制）
        
        Returns:
            dict: {success: bool, message: str, hidden: bool}
        """
        import threading
        
        result = {"success": False, "message": "", "hidden": self._game_hidden}
        
        def _do_toggle():
            """在后台线程执行窗口切换"""
            try:
                if self._game_hidden:
                    # 恢复窗口
                    success = self.window_manager.show_game_window()
                    self._game_hidden = False
                    if success:
                        msg = "✓ 游戏窗口已恢复（老板键）"
                        logger.info(msg)
                    else:
                        msg = "✗ 游戏窗口恢复失败"
                        logger.warning(msg)
                else:
                    # 隐藏窗口（传入配置的关键词，支持自动查找）
                    keyword = self.config.window_title_keyword or "QQ 农场"
                    success = self.window_manager.hide_game_window(keyword, auto_find=True)
                    self._game_hidden = True
                    if success:
                        msg = "🔒 游戏窗口已完美隐藏（老板键）- 任务栏图标已隐藏 + Bot 继续工作"
                        logger.info(msg)
                    else:
                        msg = "✗ 游戏窗口隐藏失败（请先打开游戏）"
                        logger.warning(msg)
                
                result["success"] = success
                result["message"] = msg
                result["hidden"] = self._game_hidden
                
                # 发送日志消息到 GUI（必须在主线程）
                self.log_message.emit(msg)
            except Exception as e:
                logger.error(f"老板键操作异常: {e}")
                result["message"] = f"✗ 操作异常: {e}"
                self.log_message.emit(result["message"])
        
        # 启动后台线程执行，不阻塞 GUI 主线程
        thread = threading.Thread(target=_do_toggle, daemon=True)
        thread.start()
        
        return result

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
        self._bot_stop_requested = False

        # 初始化实例独立的目录（日志和截图）
        self._setup_instance_dirs()

        self.cv_detector.load_templates()
        tpl_count = sum(len(v) for v in self.cv_detector._templates.values())
        if tpl_count == 0:
            self.log_message.emit("未找到模板图片，请先运行模板采集工具")
            return False

        window = self.window_manager.find_window(
            self.config.window_title_keyword, 
            auto_launch=True, 
            shortcut_path=self.config.planting.game_shortcut_path,
            select_rule=self.config.window_select_rule
        )
        if not window:
            self.log_message.emit("启动游戏失败，请检查快捷方式路径是否正确" if self.config.planting.game_shortcut_path else "未找到 QQ 农场窗口，请先打开微信小程序中的 QQ 农场")
            return False

        w, h = self.config.planting.window_width, self.config.planting.window_height
        if w > 0 and h > 0:
            # 等待游戏自适应完成
            time.sleep(2)
            window_position = self.config.safety.window_position
            self.window_manager.resize_window(w, h, window_position)
            time.sleep(0.5)
            # 使用缓存的窗口信息，不重新搜索
            window = self.window_manager._cached_window
            if window:
                self.log_message.emit(f"窗口已调整为 {window.width}x{window.height}")

        rect = (window.left, window.top, window.width, window.height)
        run_mode = self.config.safety.run_mode
        hwnd = window.hwnd if run_mode == RunMode.BACKGROUND else None
        self.action_executor = ActionExecutor(
            window_rect=rect,
            hwnd=hwnd,
            run_mode=run_mode,
            delay_min=self.config.safety.random_delay_min,
            delay_max=self.config.safety.random_delay_max,
            click_offset=self.config.safety.click_offset_range,
        )
        self._init_strategies()

        mode_text = "后台" if run_mode == RunMode.BACKGROUND else "前台"
        self.log_message.emit(f"Bot已启动 - 窗口: {window.title} | 模板: {tpl_count}个 | 模式: {mode_text}")

        # 启动异步任务执行器（替代旧 TaskScheduler）
        self._init_executor()

        # 标记调度器为运行状态（GUI 依赖此状态）
        self.scheduler.mark_running()
        # 窗口存活监控：仅开启掉线重登时才需要定时检查
        if self.config.safety.auto_remote_login:
            self.scheduler.start_window_check()

        # 启动时检查更新（异步，不阻塞）
        self._check_update_async()

        return True

    def stop(self):
        """停止 Bot - 立即停止所有操作"""
        logger.info("停止请求：设置停止标志")

        # 0. 设置通用停止标志（任务级别）
        self._bot_stop_requested = True

        # 1. 设置所有策略的停止标志（必须在停 executor 之前，让运行中的任务能响应停止）
        for s in self._strategies:
            s._stop_requested = True

        # 2. 停止异步执行器
        self._stop_executor()

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
        # 注意：_bot_stop_requested 不在此处重置，因为 executor 线程可能还在运行
        # 它会在 start() 时重置

        self.log_message.emit("Bot 已停止")

    def pause(self):
        for s in self._strategies:
            s._stop_requested = True
        if self._async_executor:
            self._async_executor.pause()
        self.scheduler.pause()

    def resume(self):
        for s in self._strategies:
            s._stop_requested = False
        if self._async_executor:
            self._async_executor.resume()
        self.scheduler.resume()

    def run_once(self):
        """手动触发农场巡查"""
        if self._async_executor:
            self._async_executor.task_call("main")
        else:
            self._on_farm_check()

    def run_friend_once(self):
        """手动触发好友巡查"""
        if self._async_executor:
            self._async_executor.task_call("friend")
        else:
            self._on_friend_check()

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
            run_mode = self.config.safety.run_mode
            wnd = self.window_manager._cached_window
            hwnd = wnd.hwnd if (run_mode == RunMode.BACKGROUND and wnd) else None
            self.action_executor = ActionExecutor(
                window_rect=rect,
                hwnd=hwnd,
                run_mode=run_mode,
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
        self._worker.finished.connect(self._on_farm_finished)
        self._worker.error.connect(self._on_task_error)
        self._worker.start()

    def _on_friend_check(self):
        # 检查是否开启了好友巡查功能
        friend_cfg = self.config.features.friend
        if not (friend_cfg.enable_steal or friend_cfg.enable_weed or friend_cfg.enable_water or friend_cfg.enable_bug):
            return  # 功能未开启，直接返回，不打日志

        if self._is_busy:
            logger.debug("上一轮操作尚未完成，跳过好友巡查")
            return
        if not self.action_executor:
            logger.warning("好友巡查: Bot 尚未启动，跳过")
            return
        # 标记好友巡查已开始，更新下次检查时间
        self.scheduler._next_friend_check = time.time() + self.scheduler._friend_timer.interval() / 1000
        self._is_busy = True
        self._worker = BotWorker(self, "friend")
        self._worker.finished.connect(self._on_friend_finished)
        self._worker.error.connect(self._on_task_error)
        self._worker.start()

    # ============================================================
    # 地块巡查
    # ============================================================

    def _check_land_triggers(self) -> dict:
        """检查地块数据中的倒计时和播种需求，返回触发建议。

        Returns:
            {"need_harvest": bool, "need_plant": bool, "alert_plots": list[dict]}
        """
        triggers = {"need_harvest": False, "need_plant": False, "alert_plots": []}
        plots = self.config.land.plots
        if not isinstance(plots, list) or not plots:
            return triggers

        ci_cfg = self.config.cross_instance
        threshold = ci_cfg.alert_threshold_seconds if ci_cfg.enabled else 300

        for plot in plots:
            if not isinstance(plot, dict):
                continue

            # 检查倒计时是否即将结束
            cd = str(plot.get("maturity_countdown", "") or "").strip()
            if cd and ":" in cd:
                parts = cd.split(":")
                try:
                    total = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    if 0 < total <= threshold:
                        triggers["need_harvest"] = True
                    # 跨实例通知：收集即将成熟的地块
                    if 0 < total <= threshold and ci_cfg.enabled and ci_cfg.send_alerts:
                        triggers["alert_plots"].append({
                            "plot_id": plot.get("plot_id", ""),
                            "maturity_seconds": total,
                        })
                except (ValueError, IndexError):
                    pass

            # 检查是否有需要播种的地块
            if plot.get("need_planting"):
                triggers["need_plant"] = True

        if triggers["need_harvest"] or triggers["need_plant"]:
            reasons = []
            if triggers["need_harvest"]:
                reasons.append("倒计时即将结束")
            if triggers["need_plant"]:
                reasons.append("有空地待播种")
            logger.info(f"地块触发: {', '.join(reasons)}")

        return triggers

    def _run_task_land_scan(self, ctx: TaskContext) -> TaskResult:
        """地块巡查：逐块点击+OCR采集，更新 config.land"""
        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        land_scan = self._ensure_land_scan_task()
        if land_scan is None:
            return TaskResult(success=False, error="LandScanTask 未初始化（OCR 不可用）")

        try:
            success = land_scan.run(self)
        except Exception as e:
            logger.error(f"地块巡查异常: {e}")
            return TaskResult(success=False, error=str(e))

        # 巡查完成后检查地块触发条件
        if success:
            triggers = self._check_land_triggers()
            # 跨实例通知：广播即将成熟的地块
            alert_plots = triggers.get("alert_plots", [])
            if alert_plots:
                self._broadcast_steal_alerts(alert_plots)
            if triggers["need_harvest"] or triggers["need_plant"]:
                if self._async_executor and self._async_executor.is_task_enabled("main"):
                    logger.info("地块巡查后触发农场任务")
                    self._async_executor.task_call("main", force_call=False)
                else:
                    logger.debug("地块巡查: 检测到待处理地块，但 main 任务未启用，跳过触发")

        return TaskResult(success=bool(success))

    def _broadcast_steal_alerts(self, alert_plots: list[dict]) -> None:
        """向配置的伙伴实例广播即将成熟的偷菜通知"""
        if not self._cross_bus:
            return
        ci_cfg = self.config.cross_instance
        if not ci_cfg.enabled or not ci_cfg.send_alerts:
            return
        if not alert_plots:
            return

        plot_ids = [p["plot_id"] for p in alert_plots if p.get("plot_id")]
        earliest = min(p.get("maturity_seconds", 300) for p in alert_plots)

        instance_name = getattr(self, '_instance_display_name', None) or self.instance_id

        logger.info(
            f"[大小号通讯📤] [{instance_name}] 检测到 {len(plot_ids)} 个地块即将成熟，"
            f"向 {len(ci_cfg.partners)} 个配对实例广播通知"
        )

        for partner in ci_cfg.partners:
            if not partner.enabled or not partner.instance_id or not partner.friend_name:
                continue
            alert = StealAlert(
                source_instance_id=self.instance_id,
                source_name=instance_name,
                friend_name=partner.friend_name,
                target_instance_id=partner.instance_id,
                plot_ids=plot_ids,
                earliest_maturity_seconds=earliest,
            )
            logger.info(
                f"[大小号通讯📤] 通知 [{partner.instance_id}]: "
                f"好友[{partner.friend_name}] | 地块: {','.join(plot_ids)} | "
                f"最近成熟: {earliest}s"
            )
            self._cross_bus.post_alert(alert)

    def _on_farm_finished(self, result: dict):
        """农场任务完成回调（BotWorker fallback 专用）"""
        if self.scheduler.state == BotState.IDLE:
            return
        self._is_busy = False
        self._record_actions(result)

    def _on_friend_finished(self, result: dict):
        """好友巡查完成回调（BotWorker fallback 专用）"""
        if self.scheduler.state == BotState.IDLE:
            logger.debug("Bot 已停止，忽略好友巡查完成回调")
            return
        self._is_busy = False
        self._record_actions(result)

    def _record_actions(self, result: dict):
        """记录操作统计信息"""
        actions = result.get("actions_done", [])
        if actions:
            self.log_message.emit(f"本轮完成: {', '.join(actions)}")
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

    def _on_task_error(self, error_msg: str):
        self._is_busy = False
        self.log_message.emit(f"操作异常: {error_msg}")

    def _check_update_async(self):
        """异步检查 GitHub Release 更新"""
        import threading
        def _do_check():
            try:
                from utils.update_checker import check_github_latest_release
                from utils.version import __version__
                result = check_github_latest_release('BMP937/qq-farm-bot', __version__)
                if result.ok and result.has_update:
                    self.log_message.emit(
                        f"发现新版本 {result.latest_version}！"
                        f"下载: {result.download_url}")
                    logger.info(f"更新检查: {result.message} | {result.download_url}")
                else:
                    logger.debug(f"更新检查: {result.message}")
            except Exception as e:
                logger.debug(f"更新检查失败: {e}")
        thread = threading.Thread(target=_do_check, daemon=True)
        thread.start()

    def _is_window_alive(self) -> bool:
        """检查游戏窗口是否存在（供调度器窗口监控调用）"""
        window = self.window_manager.find_window(
            self.config.window_title_keyword,
            auto_launch=False,
            shortcut_path="",
            select_rule=self.config.window_select_rule
        )
        return window is not None

    def _on_window_lost(self):
        """窗口监控检测到游戏窗口关闭，尝试自动重启"""
        logger.warning("窗口监控：检测到游戏窗口关闭，尝试自动重启...")
        self.log_message.emit("⚠ 检测到游戏窗口关闭，正在尝试自动重启...")
        window = self.window_manager.find_window(
            self.config.window_title_keyword,
            auto_launch=True,
            shortcut_path=self.config.planting.game_shortcut_path,
            select_rule=self.config.window_select_rule
        )
        if not window:
            logger.error("窗口监控：自动重启游戏失败")
            self.log_message.emit("❌ 自动重启游戏失败，请手动打开 QQ 农场")
            return
        # 重启成功，调整窗口并更新
        w, h = self.config.planting.window_width, self.config.planting.window_height
        if w > 0 and h > 0:
            time.sleep(1)
            window_position = self.config.safety.window_position
            self.window_manager.resize_window(w, h, window_position)
            time.sleep(0.5)
            window = self.window_manager._cached_window
        if window:
            rect = (window.left, window.top, window.width, window.height)
            if self.action_executor:
                self.action_executor.update_window_rect(rect)
                self.action_executor.update_window_handle(
                    window.hwnd if self.config.safety.run_mode == RunMode.BACKGROUND else None
                )
            self.log_message.emit(f"✅ 游戏已自动重启，窗口: {window.title}")
            logger.info(f"窗口监控：游戏已自动重启，窗口: {window.title}")

    # ============================================================
    # 截屏 + 检测
    # ============================================================

    def _capture_only(self, rect: tuple) -> np.ndarray | None:
        """仅截屏返回 cv_image，不做任何检测"""
        if not self.cv_detector._loaded:
            self.cv_detector.load_templates()

        hwnd = self.window_manager.get_window_handle() if self.config.safety.run_mode == RunMode.BACKGROUND else None
        image = self.screen_capture.capture(rect, hwnd=hwnd)
        if image is None:
            return None
        self.screenshot_updated.emit(image)
        return self.cv_detector.pil_to_cv2(image)

    def _fast_capture_and_detect(self, rect: tuple,
                                  extra_names: list[str] | None = None
                                  ) -> tuple[np.ndarray | None, list[DetectResult]]:
        """快速截图+检测：只扫描核心模板，使用 3 个尺度（vs 13 个），速度提升 5-10x"""
        cv_image = self._capture_only(rect)
        if cv_image is None:
            return None, []

        names = list(SCENE_TEMPLATES)
        if extra_names:
            names.extend(extra_names)

        detections = self.cv_detector.detect_targeted(
            cv_image, names, scales=[1.0, 0.9, 1.1]
        )
        detections = [d for d in detections
                      if d.name != "btn_shop_close"
                      and not (d.name == "btn_expand" and d.confidence < 0.85)]

        self._emit_annotated(cv_image, detections)
        return cv_image, detections

    def _prepare_window(self) -> tuple | None:
        window = self.window_manager.find_window(
            self.config.window_title_keyword,
            auto_launch=True,
            shortcut_path=self.config.planting.game_shortcut_path,
            select_rule=self.config.window_select_rule,
        )
        if not window:
            return None
        # 后台模式不激活窗口（不抢焦点）
        if self.config.safety.run_mode != RunMode.BACKGROUND:
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

        hwnd = self.window_manager.get_window_handle() if self.config.safety.run_mode == RunMode.BACKGROUND else None
        if save:
            image, _ = self.screen_capture.capture_and_save(rect, prefix, hwnd=hwnd)
        else:
            image = self.screen_capture.capture(rect, hwnd=hwnd)
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
                # 所有类别：逐个模板使用自定义阈值
                for tpl in self.cv_detector._templates[cat]:
                    thresh = self.cv_detector.get_template_threshold(tpl["name"])
                    detections += self.cv_detector.detect_single_template(
                        cv_image, tpl["name"], threshold=thresh
                    )
            detections = [d for d in detections
                          if d.name != "btn_shop_close"
                          and not (d.name == "btn_expand" and d.confidence < 0.85)]
            detections = self.cv_detector._nms_by_category(detections, iou_threshold=0.3)

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
        self._plant_done = False  # 重置播种标记，每次调度只播种一次
        
        # 检查静默时段
        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            logger.info(f"静默时段内，跳过农场巡查（剩余 {remaining} 秒）")
            result["success"] = True
            result["message"] = f"静默时段，跳过执行（剩余 {remaining} 秒）"
            result["next_check_seconds"] = min(remaining, 300)  # 最多 5 分钟后重试
            return result
        
        features = self.config.features.model_dump()

        # 判断是否有农场操作需求（排除好友功能）
        farm_features = {
            "auto_harvest", "auto_plant", "auto_weed", "auto_water",
            "auto_bug", "auto_fertilize", "auto_upgrade",
            "auto_task",
        }
        has_farm_work = any(features.get(f, False) for f in farm_features)

        # 地块触发条件检查（提前，影响轻量检查判断）
        # 仅在用户已开启对应功能时，通过地块触发激活执行
        _early_triggers = self._check_land_triggers()
        if _early_triggers["need_harvest"] and features.get("auto_harvest"):
            has_farm_work = True
        if _early_triggers["need_plant"] and features.get("auto_plant"):
            has_farm_work = True

        rect = self._prepare_window()
        if not rect:
            result["message"] = "窗口未找到"
            return result

        # 没有农场操作需求时，只做最轻量的检查
        if not has_farm_work:
            if not self.popup.stopped:
                self._clear_screen(rect)
            cv_image, detections = self._fast_capture_and_detect(rect)
            if cv_image is not None:
                scene = identify_scene(detections, self.cv_detector, cv_image)
                det_summary = ", ".join(f"{d.name}({d.confidence:.0%})" for d in detections[:3])
                logger.debug(f"[轻量检查] 场景={scene.value} | {det_summary}")
                # 处理弹窗，确保不会卡住
                if scene == Scene.POPUP:
                    self.popup.handle_popup(detections)
                elif scene == Scene.INFO_PAGE:
                    info_close = self.popup.find_any(detections, ["btn_close", "btn_info_close", "btn_rw_close"])
                    if info_close:
                        self.popup.click(info_close.x, info_close.y, "关闭个人信息页面")
            result["success"] = True
            result["next_check_seconds"] = self.config.schedule.farm_check_seconds
            return result

        # 清屏：点击天空区域关闭残留弹窗/菜单
        if not self.popup.stopped:
            self._clear_screen(rect)

        # 地块触发时增加主循环轮数，确保操作有机会执行
        has_land_trigger = (
            (_early_triggers["need_harvest"] and features.get("auto_harvest"))
            or (_early_triggers["need_plant"] and features.get("auto_plant"))
        )
        if has_land_trigger:
            reasons = []
            if _early_triggers["need_harvest"] and features.get("auto_harvest"):
                reasons.append("倒计时即将结束")
            if _early_triggers["need_plant"] and features.get("auto_plant"):
                reasons.append("有空地待播种")
            logger.info(f"地块触发：{', '.join(reasons)}")

        idle_rounds = 0
        max_idle = 1 if not has_land_trigger else 3  # 触发时增加轮数，确保操作执行
        
        # ✅ 添加状态变化检测：避免重复相同检测
        prev_scene = None
        prev_detection_keys = set()
        consecutive_same_state = 0  # 连续相同状态轮数
        max_consecutive_same = 2  # 最多允许连续 2 轮相同状态

        # 策略调度列表（按优先级）
        _FARM_OVERVIEW = Scene.FARM_OVERVIEW
        _f = self.config.features  # 缩短引用
        farm_tasks = [
            ("Popup",   lambda: True,                       lambda s: s in (Scene.POPUP, Scene.INFO_PAGE, Scene.SHOP_PAGE), lambda ctx: self._task_popup(ctx)),
            ("Harvest", lambda: _f.auto_harvest,             lambda s: s == _FARM_OVERVIEW,                                 lambda ctx: self._task_harvest(ctx)),
            ("Maintain",lambda: _f.auto_weed or _f.auto_water or _f.auto_bug, lambda s: s == _FARM_OVERVIEW, lambda ctx: self._task_maintain(ctx)),
            ("Plant",   lambda: _f.auto_plant and not self._plant_done, lambda s: s == _FARM_OVERVIEW,                       lambda ctx: self._task_plant(ctx)),
            ("Expand",  lambda: _f.auto_upgrade,             lambda s: s == _FARM_OVERVIEW,                                 lambda ctx: self._task_expand(ctx)),
            ("Upgrade", lambda: _f.auto_upgrade,             lambda s: s == _FARM_OVERVIEW,                                 lambda ctx: self._task_upgrade(ctx)),
            ("Task",    lambda: _f.auto_task,                 lambda s: s == _FARM_OVERVIEW,                                 lambda ctx: self._task_task(ctx)),
        ]

        def execute_farm_tasks(ctx: dict) -> bool:
            """按优先级执行匹配的任务，返回是否有任务实际执行了操作"""
            scene = ctx.get("scene")
            for name, enabled_fn, check_fn, run_fn in farm_tasks:
                try:
                    if enabled_fn() and check_fn(scene):
                        if run_fn(ctx):
                            return True
                except Exception as e:
                    logger.debug(f'[check_farm] 任务 {name} 异常: {e}')
            return False

        # 主循环
        for round_num in range(1, 51):
            if self.popup.stopped:
                logger.info("收到停止/暂停信号，中断当前操作")
                break

            # 窗口管理逻辑保持不变...
            window = self.window_manager.refresh_window_info(
                self.config.window_title_keyword,
                auto_launch=False,
                shortcut_path=""
            )
            if not window:
                logger.warning("游戏窗口已关闭，尝试自动重启...")
                self.log_message.emit("检测到游戏窗口关闭，正在尝试自动重启...")
                window = self.window_manager.find_window(
                    self.config.window_title_keyword,
                    auto_launch=True,
                    shortcut_path=self.config.planting.game_shortcut_path,
                    select_rule=self.config.window_select_rule
                )
                if not window:
                    logger.error("自动重启游戏失败")
                    self.log_message.emit("自动重启游戏失败，请手动打开 QQ 农场")
                    result["message"] = "窗口已关闭且重启失败"
                    break
                # 重启成功，更新窗口信息和 action_executor
                self.window_manager.resize_window(
                    self.config.planting.window_width,
                    self.config.planting.window_height,
                    self.config.safety.window_position
                )
                time.sleep(0.5)
                window = self.window_manager._cached_window
                if window:
                    rect = (window.left, window.top, window.width, window.height)
                    self.action_executor.update_window_rect(rect)
                    self.action_executor.update_window_handle(
                        window.hwnd if self.config.safety.run_mode == RunMode.BACKGROUND else None
                    )
                    self.log_message.emit(f"游戏已自动重启，窗口: {window.title}")

            cv_image, detections = self._fast_capture_and_detect(
                rect, extra_names=LAND_TEMPLATES
            )
            if cv_image is None:
                result["message"] = "截屏失败"
                break

            # 识别场景
            scene = identify_scene(detections, self.cv_detector, cv_image)
            det_summary = ", ".join(f"{d.name}({d.confidence:.0%})" for d in detections[:6])
            logger.debug(f"[轮{round_num}] 场景={scene.value} | {det_summary}")
            
            # ✅ 状态变化检测：避免重复相同检测
            # 构建当前状态的指纹（场景 + 关键按钮集合）
            key_detections = frozenset(d.name for d in detections if d.confidence > 0.7)
            current_state = (scene, key_detections)
            
            if prev_scene == scene and prev_detection_keys == key_detections:
                consecutive_same_state += 1
                if consecutive_same_state >= max_consecutive_same:
                    # 连续相同状态，跳过本轮调度
                    logger.debug(f"[轮{round_num}] 连续 {consecutive_same_state} 轮状态相同，跳过调度")
                    idle_rounds += 1
                    if idle_rounds >= max_idle:
                        logger.info(f"连续 {max_idle} 轮无操作且状态未变化，提前退出")
                        break
                    # 延长 sleep 时间
                    time.sleep(1.0)
                    continue
            else:
                # 状态发生变化，重置计数
                consecutive_same_state = 0
                prev_scene = scene
                prev_detection_keys = key_detections

            # 构建上下文
            context = {
                "detections": detections,
                "scene": scene,
                "rect": rect,
                "features": features,
                "engine": self
            }

            # 特殊处理：异地登录 (优先级最高，打断循环)
            if scene == Scene.REMOTE_LOGIN:
                if self.config.safety.auto_remote_login:
                    self._handle_remote_login(context)
                else:
                    logger.warning("检测到异地登录，但重登功能已关闭，跳过")
                    self.log_message.emit("⚠ 检测到异地登录，重登已关闭，请手动处理")
                continue

            # 执行任务调度
            if execute_farm_tasks(context):
                idle_rounds = 0
            else:
                idle_rounds += 1
                # ✅ 优化：移除 idle_rounds==1 时的无效 click_blank
                # 原因：在 farm_overview 场景下，如果没有弹窗，click_blank 纯属多余
                # 只有在检测到弹窗场景时才应该 click_blank
                if idle_rounds >= max_idle:
                    break

            # ✅ 优化：根据空闲轮数动态调整 sleep 时间
            if idle_rounds == 0:
                time.sleep(0.3)  # 有操作时快速响应
            elif idle_rounds == 1:
                time.sleep(0.5)  # 首次空闲，稍等
            else:
                time.sleep(1.0)  # 连续空闲，延长间隔，避免重复检测

        # 设置下次检查间隔：始终使用用户配置的间隔
        interval = self.config.schedule.farm_check_seconds
        result["next_check_seconds"] = interval
        has_planted = any("播种" in a for a in result.get("actions_done", []))
        if has_planted:
            crop_name = self._resolve_crop_name()
            crop = get_crop_by_name(crop_name)
            if crop:
                grow_time = crop[3]
                logger.info(f"已播种{crop_name}，{format_grow_time(grow_time)}后成熟，每{self.config.schedule.farm_check_seconds}秒检查维护")

        result["success"] = True
        self.screen_capture.cleanup_old_screenshots(0)
        return result

    def _handle_remote_login(self, context: dict):
        """处理异地登录（高优先级打断逻辑）"""
        logger.warning("检测到异地登录，关闭游戏并等待 3 分钟后重启...")
        self.log_message.emit("⚠ 检测到异地登录，正在关闭游戏，等待 3 分钟后重启...")
        self.scheduler.set_remote_login_cooldown(180)
        try:
            import ctypes
            if self.window_manager._cached_window:
                hwnd = self.window_manager._cached_window.hwnd
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
                time.sleep(1)
        except Exception as e:
            logger.error(f"关闭游戏失败: {e}")
        self.window_manager._cached_window = None
        
        for i in range(180, 0, -10):
            if self.popup.stopped:
                logger.info("收到停止信号，取消异地登录重启")
                break
            if i % 60 == 0:
                self.log_message.emit(f"等待重启中... 剩余 {i // 60} 分钟")
            time.sleep(10)
            
        self.log_message.emit("等待结束，正在重启游戏...")
        window = self.window_manager.find_window(
            self.config.window_title_keyword,
            auto_launch=True,
            shortcut_path=self.config.planting.game_shortcut_path,
            select_rule=self.config.window_select_rule
        )
        if not window:
            logger.error("异地登录重启游戏失败")
            self.log_message.emit("❌ 异地登录重启失败，请手动处理")
            return
            
        self.window_manager.resize_window(
            self.config.planting.window_width,
            self.config.planting.window_height,
            self.config.safety.window_position
        )
        time.sleep(0.5)
        window = self.window_manager._cached_window
        if window:
            rect = (window.left, window.top, window.width, window.height)
            self.action_executor.update_window_rect(rect)
            self.action_executor.update_window_handle(
                window.hwnd if self.config.safety.run_mode == RunMode.BACKGROUND else None
            )
            self.log_message.emit(f"✅ 游戏已重启，窗口: {window.title}")
            self.scheduler._remote_login_cooldown_until = 0.0

    # --- 任务执行器回调方法 ---

    def _task_popup(self, context: dict) -> bool:
        """处理弹窗 - 使用快速检测优化"""
        scene = context.get("scene")
        rect = context.get("rect")
        
        if scene == Scene.POPUP:
            return self.popup.handle_popup_direct(rect) is not None
        elif scene == Scene.INFO_PAGE:
            # 只检测关闭相关的按钮
            cv_img, dets = self.popup.quick_detect(rect, ["btn_close", "btn_info_close", "btn_rw_close"],
                                                     scales=[1.0, 0.9, 1.1])
            if cv_img is None:
                return False
            info_close = self.popup.find_any(dets, ["btn_close", "btn_info_close", "btn_rw_close"])
            if info_close:
                self.popup.click(info_close.x, info_close.y, "关闭个人信息页面")
                return True
        elif scene in (Scene.SHOP_PAGE, Scene.BUY_CONFIRM, Scene.PLOT_MENU, Scene.LEVEL_UP):
            return self.popup.handle_popup_direct(rect) is not None
        elif scene == Scene.MALL_PAGE:
            # 只检测商城返回按钮
            cv_img, dets = self.popup.quick_detect(rect, ["btn_shangcehng_fanhui"], scales=[1.0, 0.9, 1.1])
            if cv_img is None:
                return False
            mall_back = self.popup.find_by_name(dets, "btn_shangcehng_fanhui")
            if mall_back:
                self.popup.click(mall_back.x, mall_back.y, "关闭商城")
                return True
            self.popup.click_blank(rect)
            return True
        return False

    def _task_harvest(self, context: dict) -> bool:
        """收获任务 - 使用快速检测优化"""
        rect = context.get("rect")
        desc = self.harvest.try_harvest_direct(rect)
        if desc:
            self._planted = False
            self._fertilized = False
            self._planted_idle_rounds = 0
            return True
        elif self._planted:
            self._planted_idle_rounds += 1
            if self._planted_idle_rounds >= 6:
                logger.info("已播种但连续 6 轮未检测到收获，重置状态")
                self._planted = False
                self._fertilized = False
                self._planted_idle_rounds = 0
        return False

    def _task_maintain(self, context: dict) -> bool:
        """维护任务（除草/除虫/浇水）- 统一循环，共享确认计时器"""
        rect = context.get("rect")
        features = context.get("features", {})
        return self.maintain.try_maintain_direct(rect, features) is not None

    def _task_plant(self, context: dict) -> bool:
        """播种任务（每次 check_farm 只执行一次）"""
        self._plant_done = True  # 标记已执行，本轮不再重复播种
        rect = context.get("rect")
        crop_name = self._resolve_crop_name()
        pa = self.plant.plant_all(rect, crop_name, auto_fertilize=self.config.features.auto_fertilize)
        if pa:
            self._planted = True
            self._fertilized = True
            return True
        return False

    def _task_expand(self, context: dict) -> bool:
        """扩建任务"""
        rect = context.get("rect")
        detections = context.get("detections", [])
        return self.expand.try_expand(rect, detections) is not None

    def _task_upgrade(self, context: dict) -> bool:
        """自动升级任务"""
        rect = context.get("rect")
        detections = context.get("detections", [])
        return self.expand.try_upgrade(rect, detections) is not None

    def _task_task(self, context: dict) -> bool:
        """任务/出售任务"""
        rect = context.get("rect")
        detections = context.get("detections", [])
        ta = self.task.try_task(rect, detections)
        return ta is not None and len(ta) > 0

    def _task_gift(self, context: dict) -> bool:
        """礼品领取任务"""
        rect = context.get("rect")
        detections = context.get("detections", [])
        ga = self.gift.try_gift(rect, detections,
                                auto_svip_gift=self.config.features.auto_svip_gift,
                                auto_mall_gift=self.config.features.auto_mall_gift,
                                auto_mail=self.config.features.auto_mail)
        if ga:
            self.log_message.emit(f"礼品领取: {', '.join(ga)}")
            return True
        return False

    def check_friends(self) -> dict:
        result = {"success": True, "actions_done": [], "next_check_seconds": 1800}
        
        # 检查静默时段
        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            logger.info(f"静默时段内，跳过好友巡查（剩余 {remaining} 秒）")
            result["success"] = True
            result["message"] = f"静默时段，跳过执行（剩余 {remaining} 秒）"
            result["next_check_seconds"] = min(remaining, 300)
            return result
        
        features = self.config.features
        friend_cfg = features.friend

        # 新配置：4个独立开关全关则跳过
        if not friend_cfg.enable_steal and not friend_cfg.enable_weed \
           and not friend_cfg.enable_water and not friend_cfg.enable_bug:
            logger.info("好友巡查: 未启用任何操作，跳过")
            return result

        rect = self._prepare_window()
        if not rect:
            result["message"] = "窗口未找到"
            return result

        # 调用 FriendStrategy 完整流程（传入独立开关和偷菜上限）
        # 设置好友黑名单
        self.friend.set_blacklist(friend_cfg.blacklist)
        self.friend._instance_id = self.instance_id

        actions = self.friend.run_friend_round(
            rect,
            enable_steal=friend_cfg.enable_steal,
            enable_weed=friend_cfg.enable_weed,
            enable_water=friend_cfg.enable_water,
            enable_bug=friend_cfg.enable_bug,
            max_steal=friend_cfg.max_steal_per_round,
        )
        result["actions_done"] = actions

        if actions:
            self.log_message.emit(f"好友巡查完成: {', '.join(actions)}")

        return result

    # ============================================================
    # 定点偷菜（大小号通讯）
    # ============================================================

    def _run_task_steal(self, ctx: TaskContext) -> TaskResult:
        """定点偷菜任务：由跨实例通知触发

        通过 TaskItem.features["friend_name"] 获取目标好友昵称。
        任务名格式: steal_{source_instance_id}
        """
        # 从执行器内部任务字典获取 friend_name
        friend_name = ""
        source_name = ""
        if self._async_executor:
            with self._async_executor._lock:
                task_item = self._async_executor._tasks.get(ctx.task_name)
                if task_item:
                    friend_name = task_item.features.get("friend_name", "")
                    source_name = task_item.features.get("source_name", "")

        if not friend_name:
            return TaskResult(success=False, error="未指定好友昵称")

        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        if self._bot_stop_requested:
            return TaskResult(success=False, error="已停止")

        ci_cfg = self.config.cross_instance
        if not ci_cfg.enabled or not ci_cfg.accept_steal:
            logger.warning(
                f"[大小号通讯🎯] 配置检查失败: engine={self.instance_id} | "
                f"config_id={id(self.config)} | enabled={ci_cfg.enabled} | accept_steal={ci_cfg.accept_steal}"
            )
            return TaskResult(success=False, error="未启用接收偷菜")

        logger.info(
            f"[大小号通讯🎯] 开始定点偷菜: [{source_name}] → 好友[{friend_name}]"
        )
        self.log_message.emit(f"[大小号通讯🎯] 定点偷菜: [{source_name}] → 好友[{friend_name}]")

        rect = self._prepare_window()
        if not rect:
            return TaskResult(success=False, error="窗口未找到")

        # 确保策略依赖已初始化
        if not self.targeted_steal.action_executor:
            self.targeted_steal.action_executor = self.action_executor
        self.targeted_steal.set_capture_fn(self._fast_strategy_capture)
        self.targeted_steal._stop_requested = False

        result = self.targeted_steal.steal_from_friend(friend_name, rect)

        if result["success"]:
            logger.info(f"[大小号通讯🎯] 偷菜成功: [{source_name}] → 好友[{friend_name}]")
            self.log_message.emit(f"[大小号通讯🎯] ✓ 偷菜成功: [{source_name}] → 好友[{friend_name}]")
            return TaskResult(success=True)
        else:
            logger.warning(f"[大小号通讯🎯] 偷菜失败: [{source_name}] → 好友[{friend_name}] | {result['message']}")
            self.log_message.emit(f"[大小号通讯🎯] ✗ 偷菜失败: [{source_name}] → 好友[{friend_name}] | {result['message']}")
            return TaskResult(success=False, error=result["message"])

    # ============================================================
    # 异步任务执行器管理
    # ============================================================

    def _init_executor(self):
        """初始化并启动异步任务执行器"""
        self._stop_executor()
        tasks = self._build_executor_tasks()
        runners = self._collect_task_runners()

        if not tasks or not runners:
            logger.warning("无任务或 runner，跳过执行器初始化")
            return

        self._async_executor = AsyncTaskExecutor(
            tasks=tasks,
            runners=runners,
            executor_cfg=self.config.executor,
            on_snapshot=self._on_executor_snapshot,
            on_task_done=self._on_executor_task_done,
            on_task_error=self._on_executor_task_error,
            cross_bus=self._cross_bus,
            instance_id=self.instance_id,
        )
        self._async_executor.start()
        logger.info(f"异步执行器已启动: {len(tasks)} 个任务, {len(runners)} 个 runner")

    def _stop_executor(self):
        """停止异步任务执行器"""
        if self._async_executor:
            self._async_executor.stop()
            self._async_executor = None

    def _collect_task_runners(self) -> dict:
        """反射发现 _run_task_* 方法"""
        runners = {}
        for attr_name in dir(self):
            if attr_name.startswith("_run_task_") and callable(getattr(self, attr_name)):
                task_name = attr_name[len("_run_task_"):]
                runners[task_name] = getattr(self, attr_name)
        logger.debug(f"发现 {len(runners)} 个 task runner: {list(runners.keys())}")
        return runners

    def _build_executor_tasks(self) -> list[TaskItem]:
        """从 config.tasks 构建 TaskItem 列表"""
        items = []
        for name, cfg in self.config.tasks.items():
            items.append(build_task_item(name, cfg))
        return items

    def _sync_executor_tasks(self):
        """热更新执行器任务配置"""
        if self._async_executor:
            self._async_executor.sync_tasks(self.config.tasks)

    @staticmethod
    def _task_display_name(task_name: str) -> str:
        """获取任务中文显示名（移植自 copilot）"""
        from gui.widgets.task_panel import DEFAULT_TASK_TITLES
        return DEFAULT_TASK_TITLES.get(task_name, task_name)

    def _on_executor_snapshot(self, snapshot: TaskSnapshot):
        """执行器快照回调（移植自 copilot：立即推送 GUI 统计面板）"""
        # 兼容旧 flat_snapshot 调用
        self._task_snapshots = snapshot

        # 立即更新 scheduler 的运行态指标（Qt 信号跨线程安全）
        display_name = self._task_display_name(snapshot.running_task) if snapshot.running_task else "--"
        next_name = "--"
        next_run_text = "--"
        if snapshot.pending_tasks:
            next_name = self._task_display_name(snapshot.pending_tasks[0].name)
            next_run_text = snapshot.pending_tasks[0].next_run.strftime("%H:%M:%S")
        elif snapshot.waiting_tasks:
            next_name = self._task_display_name(snapshot.waiting_tasks[0].name)
            next_run_text = snapshot.waiting_tasks[0].next_run.strftime("%m-%d %H:%M:%S")

        self.scheduler.update_runtime_metrics(
            current_task=display_name,
            next_task=next_name,
            next_run=next_run_text,
            running_tasks=1 if snapshot.running_task else 0,
            pending_tasks=len(snapshot.pending_tasks),
            waiting_tasks=len(snapshot.waiting_tasks),
        )

    def _on_executor_task_done(self, task_name: str, result: TaskResult):
        """任务完成回调（移植自 copilot：持久化 next_run）"""
        display_name = self._task_display_name(task_name)
        status_text = "成功" if result.success else "失败"

        # 持久化 next_run 到配置文件
        self._persist_task_next_run(task_name)

        # 格式化下次执行时间
        next_run_text = "--"
        if self._async_executor:
            snap = self._async_executor.snapshot()
            for t in snap.pending_tasks:
                if t.name == task_name:
                    next_run_text = t.next_run.strftime("%H:%M:%S")
                    break
            if next_run_text == "--":
                for t in snap.waiting_tasks:
                    if t.name == task_name:
                        next_run_text = t.next_run.strftime("%m-%d %H:%M:%S")
                        break

        msg = f"[{display_name}] 任务完成: {status_text} | 下次执行: {next_run_text}"
        if not result.success and result.error:
            msg = f"{msg} | 错误: {result.error}"
        logger.info(msg)

    def _on_executor_task_error(self, task_name: str, error: str):
        """任务失败回调"""
        display_name = self._task_display_name(task_name)
        logger.warning(f"[{display_name}] 任务失败: {error}")

    def _persist_task_next_run(self, task_name: str):
        """将任务下次执行时间回写到配置文件（移植自 copilot）"""
        if not self._async_executor:
            return
        snap = self._async_executor.snapshot()
        target_item = None
        for t in snap.pending_tasks:
            if t.name == task_name:
                target_item = t
                break
        if target_item is None:
            for t in snap.waiting_tasks:
                if t.name == task_name:
                    target_item = t
                    break
        if target_item is None:
            return

        cfg = self.config.tasks.get(task_name)
        if cfg is None:
            return
        next_run_text = target_item.next_run.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        if str(getattr(cfg, "next_run", "") or "") == next_run_text:
            return
        cfg.next_run = next_run_text
        try:
            self.config.save()
        except Exception as exc:
            logger.debug(f"持久化 next_run 失败({task_name}): {exc}")

    # ============================================================
    # 异步 Task Runner 方法
    # ============================================================

    def _run_task_main(self, ctx: TaskContext) -> TaskResult:
        """主农场任务：弹窗→收获→维护→播种→扩建→升级→礼品"""
        # 静默时段检查
        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(
                success=True,
                next_run_seconds=min(remaining, 300),
            )

        # 使用 BotWorker 在 QThread 中执行（兼容现有信号机制）
        result = self.check_farm()
        return TaskResult(
            success=result.get("success", False),
        )

    def _sync_head_profile_from_ocr(self, rect: tuple | None = None):
        """OCR 识别头部信息并回写 config.land.profile（移植自 copilot）。"""
        head_ocr = self._ensure_head_info_ocr()
        if head_ocr is None:
            return

        if rect is None:
            rect = self._prepare_window()
            if not rect:
                return

        cv_img, _ = self._fast_capture_and_detect(rect)
        if cv_img is None:
            return

        # 只识别窗口上三分之一区域，减少干扰提升性能
        h, w = cv_img.shape[:2]
        roi = (0, 0, w, h // 3)

        try:
            level, score, raw_text, extra_info = head_ocr.detect_head_info(cv_img, region=roi)
        except Exception as e:
            logger.debug(f"个人信息 OCR 失败: {e}")
            return

        if extra_info:
            logger.debug(
                f"个人信息 OCR | roi={roi} tokens={extra_info.get('tokens', [])} "
                f"money={extra_info.get('money_candidates', [])}"
            )

        if not extra_info:
            return

        # 直接使用 OCR 识别结果
        old_level = int(self.config.planting.player_level)
        accepted_level = int(level) if isinstance(level, int) and level > 0 else old_level

        # 回写 planting.player_level
        level_changed = accepted_level != old_level
        if level_changed:
            self.config.planting.player_level = accepted_level

        # 回写 land.profile（保留旧值：OCR 为空时不覆盖）
        profile = self.config.land.profile
        old_gold = str(getattr(profile, 'gold', '') or '').strip()
        old_coupon = str(getattr(profile, 'coupon', '') or '').strip()
        old_exp = str(getattr(profile, 'exp', '') or '').strip()

        gold_candidate = str(extra_info.get('gold', '') or '').strip()
        coupon_candidate = str(extra_info.get('coupon', '') or '').strip()
        exp_candidate = str(extra_info.get('exp', '') or '').strip()

        # 校验 OCR 结果格式，过滤脏数据（如终端文字混入）
        gold_re = re.compile(r'^\d+(?:\.\d+)?(?:万|亿)?$')
        exp_re = re.compile(r'^\d+(?:\.\d+)?(?:万|亿)?/\d+(?:\.\d+)?(?:万|亿)?$')
        coupon_re = re.compile(r'^\d+$')
        if gold_candidate and not gold_re.match(gold_candidate):
            logger.debug(f"金币格式异常，丢弃: '{gold_candidate}'")
            gold_candidate = ''
        if exp_candidate and not exp_re.match(exp_candidate):
            logger.debug(f"经验格式异常，丢弃: '{exp_candidate}'")
            exp_candidate = ''
        if coupon_candidate and not coupon_re.match(coupon_candidate):
            logger.debug(f"点券格式异常，丢弃: '{coupon_candidate}'")
            coupon_candidate = ''

        new_level = accepted_level
        new_gold = gold_candidate or old_gold
        new_coupon = coupon_candidate or old_coupon
        new_exp = exp_candidate or old_exp

        profile_changed = (
            int(profile.level) != new_level
            or old_gold != new_gold
            or old_coupon != new_coupon
            or old_exp != new_exp
        )

        if profile_changed:
            profile.level = new_level
            profile.gold = new_gold
            profile.coupon = new_coupon
            profile.exp = new_exp

        if level_changed or profile_changed:
            self.config.save()
            if level_changed:
                logger.info(
                    f"等级已更新 | Lv{old_level} -> Lv{accepted_level} | "
                    f"gold={new_gold or '-'} coupon={new_coupon or '-'} exp={new_exp or '-'}"
                )
            else:
                logger.info(
                    f"个人信息已更新 | Lv{accepted_level} | "
                    f"gold={new_gold or '-'} coupon={new_coupon or '-'} exp={new_exp or '-'}"
                )
            # 广播配置更新，触发 GUI 刷新
            self.config_updated.emit(self.config)

    def _run_task_profile(self, ctx: TaskContext) -> TaskResult:
        """个人信息 OCR 任务：主界面识别 + 打开详情页获取精确经验。"""
        if not self.config.tasks.get("profile", TaskScheduleItemConfig()).enabled:
            return TaskResult(success=True)
        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        # 确保 action_executor 已设置
        if not self.action_executor:
            logger.warning("_run_task_profile: action_executor 为 None，跳过")
            return TaskResult(success=False, error="action_executor 未初始化")
        for s in self._strategies:
            if not s.action_executor:
                s.action_executor = self.action_executor

        rect = self._prepare_window()
        if not rect:
            return TaskResult(success=False, error="窗口未找到")

        try:
            # 第一步：主界面 OCR（等级/金币/点券/模糊经验）
            self._sync_head_profile_from_ocr(rect)

            # 第二步：打开个人信息页获取精确经验
            self._sync_detail_exp(rect)

            return TaskResult(success=True)
        except Exception as e:
            logger.error(f"个人信息 OCR 任务异常: {e}")
            return TaskResult(success=False, error=str(e))

    def _sync_detail_exp(self, rect: tuple):
        """点击主界面经验文字打开个人信息页，OCR 获取精确经验值。"""
        if self._bot_stop_requested:
            return
        # 确保 action_executor 已设置（可能从 GUI 直接调用）
        if not self.action_executor:
            logger.debug("_sync_detail_exp: action_executor 为 None，尝试初始化")
            if not self.start():
                logger.warning("_sync_detail_exp: start() 失败")
                return
        head_ocr = self._ensure_head_info_ocr()
        if head_ocr is None:
            return

        # 清屏：关闭残留弹窗
        self._clear_screen(rect)

        # 截屏，上 1/3 区域 OCR 定位经验文字
        cv_img, _ = self._fast_capture_and_detect(rect)
        if cv_img is None:
            return
        h, w = cv_img.shape[:2]
        roi = (0, 0, w, h // 3)

        ocr = head_ocr._ensure_ocr()
        if ocr is None:
            return
        items = ocr.detect(cv_img, region=roi, scale=1.5, alpha=1.15, beta=0.0)
        if not items:
            logger.debug("主界面 OCR 无结果，跳过精确经验识别")
            return

        exp_pattern = re.compile(r'\d+(?:\.\d+)?(?:万|亿)?/\d+(?:\.\d+)?(?:万|亿)?')
        concat_exp_pattern = re.compile(r'\d+(?:\.\d+)?(?:万|亿)\d+(?:\.\d+)?(?:万|亿)')
        exp_item = None
        for item in items:
            text = str(item.text or "").replace(" ", "")
            if exp_pattern.search(text):
                exp_item = item
                break
            if concat_exp_pattern.search(text) and exp_item is None:
                exp_item = item

        if exp_item is None:
            logger.debug("未找到经验文字，跳过精确经验识别")
            return

        # 点击经验文字位置（窗口相对坐标）
        xs = [p[0] for p in exp_item.box]
        ys = [p[1] for p in exp_item.box]
        exp_cx = int(sum(xs) / len(xs))
        exp_cy = int(sum(ys) / len(ys))
        logger.debug(f"点击经验文字: coords=({exp_cx},{exp_cy}) text='{exp_item.text}'")
        if self.action_executor:
            from models.farm_state import Action, ActionType
            action = Action(type=ActionType.NAVIGATE,
                            click_position={"x": exp_cx, "y": exp_cy},
                            priority=0, description="点击经验文字打开个人信息页")
            result = self.action_executor.execute_action(action)
            logger.debug(f"点击结果: {result.success}")
        else:
            logger.warning("action_executor 为 None，无法点击")
        time.sleep(0.6)

        if self._bot_stop_requested:
            return

        # 检测个人信息页关闭按钮或标识，确认页面已打开（只检测需要的模板）
        cv_img = self._capture_only(rect)
        if cv_img is None:
            return
        info_templates = ["btn_info_close", "btn_info", "btn_close"]
        dets = self.cv_detector.detect_targeted(cv_img, names=info_templates, scales=[1.0, 0.9, 1.1])

        det_names = [d.name for d in dets]
        info_close = any(d.name == "btn_info_close" for d in dets)
        info_icon = any(d.name == "btn_info" for d in dets)
        generic_close = any(d.name == "btn_close" for d in dets)
        logger.debug(f"个人信息页检测: info_close={info_close} "
                     f"info_icon={info_icon} "
                     f"generic_close={generic_close} "
                     f"detected={det_names}")
        if not info_close and not info_icon and not generic_close:
            logger.debug("个人信息页未打开（未检测到关闭按钮或标识），跳过精确经验识别")
            return

        # OCR 精确经验（上 1/3 区域）
        h2, w2 = cv_img.shape[:2]
        roi = (0, 0, w2, h2 // 3)
        try:
            exp_text = head_ocr.detect_detail_exp(cv_img, region=roi)
        except Exception as e:
            logger.debug(f"精确经验 OCR 失败: {e}")
            exp_text = ""

        if exp_text:
            old_exp = str(self.config.land.profile.exp or "").strip()
            if exp_text != old_exp:
                self.config.land.profile.exp = exp_text
                self.config.save()
                logger.info(f"精确经验已更新 | {old_exp or '-'} → {exp_text}")
                self.config_updated.emit(self.config)

        # 关闭个人信息页（复用已有检测结果，直接用 action_executor 避免 popup 引用问题）
        close_btn = self.popup.find_any(dets, ["btn_info_close", "btn_close"])
        if close_btn:
            if self.action_executor:
                from models.farm_state import Action, ActionType
                action = Action(type=ActionType.NAVIGATE,
                                click_position={"x": close_btn.x, "y": close_btn.y},
                                priority=0, description="关闭个人信息页")
                self.action_executor.execute_action(action)
                logger.info("✓ 关闭个人信息页")
        else:
            logger.debug("未找到关闭按钮，点击空白处关闭个人信息页")
            if self.action_executor:
                w, h = rect[2], rect[3]
                from models.farm_state import Action, ActionType
                action = Action(type=ActionType.NAVIGATE,
                                click_position={"x": w // 2, "y": int(h * 0.05)},
                                priority=0, description="点击空白处关闭")
                self.action_executor.execute_action(action)

    def _close_info_page(self, rect: tuple):
        """关闭个人信息页（兜底检测多种关闭按钮）。"""
        cv_img, dets = self.popup.quick_detect(rect, ["btn_info_close", "btn_close"])
        close_btn = self.popup.find_any(dets, ["btn_info_close", "btn_close"])
        if close_btn:
            self.popup.click(close_btn.x, close_btn.y, "关闭个人信息页")
        else:
            logger.debug("未找到关闭按钮，尝试点击空白处关闭个人信息页")
            self.popup.click_blank(rect)

    def _run_task_friend(self, ctx: TaskContext) -> TaskResult:
        """好友巡查"""
        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        result = self.check_friends()
        return TaskResult(
            success=result.get("success", False),
        )

    def _run_task_gift(self, ctx: TaskContext) -> TaskResult:
        """礼品领取（daily 触发）"""
        if not any([self.config.features.auto_svip_gift,
                    self.config.features.auto_mall_gift,
                    self.config.features.auto_mail]):
            return TaskResult(success=True)

        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        rect = self._prepare_window()
        if not rect:
            return TaskResult(success=False, error="窗口未找到")

        cv_image, detections = self._fast_capture_and_detect(rect)
        if cv_image is None:
            return TaskResult(success=False, error="截屏失败")

        ga = self.gift.try_gift(rect, detections,
                                auto_svip_gift=self.config.features.auto_svip_gift,
                                auto_mall_gift=self.config.features.auto_mall_gift,
                                auto_mail=self.config.features.auto_mail)
        if ga:
            self.log_message.emit(f"礼品领取: {', '.join(ga)}")
            return TaskResult(success=True)
        return TaskResult(success=True)

    def _run_task_fertilize(self, ctx: TaskContext) -> TaskResult:
        """定时施肥：对所有已播种地块施肥"""
        # 由任务调度 fertilize.enabled 控制是否执行

        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        rect = self._prepare_window()
        if not rect:
            return TaskResult(success=False, error="窗口未找到")

        self.log_message.emit("定时施肥：开始对所有地块施肥...")
        fa = self.plant.fertilize_all(rect, lands=None, is_test=True)
        if fa:
            self.log_message.emit(f"定时施肥完成: {', '.join(fa)}")
            return TaskResult(success=True)
        return TaskResult(success=True)

    def _run_task_sell(self, ctx: TaskContext) -> TaskResult:
        """仓库出售（移植自 copilot：支持直接仓库导航，不依赖任务条）"""
        # 由任务调度 sell.enabled 控制是否执行，无需二次检查

        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        rect = self._prepare_window()
        if not rect:
            return TaskResult(success=False, error="窗口未找到")

        # 优先尝试直接仓库导航（copilot 模式）
        cv_image, detections = self._fast_capture_and_detect(rect)
        if cv_image is None:
            return TaskResult(success=False, error="截屏失败")

        names = {d.name for d in detections}

        # 场景判断：有仓库按钮可直接进入仓库
        if "btn_warehouse" in names:
            result = self.task.try_sell_direct(rect)
            if result:
                self.log_message.emit(f"自动出售完成: {', '.join(result)}")
                return TaskResult(success=True)

        # 回退到任务条路径
        if "btn_task" in names:
            result = self.task.try_task(rect, detections)
            if result:
                self.log_message.emit(f"自动出售完成: {', '.join(result)}")
            return TaskResult(success=True)

        return TaskResult(success=True)

    def _run_task_task(self, ctx: TaskContext) -> TaskResult:
        """领取任务奖励"""
        if not self.config.features.auto_task:
            return TaskResult(success=True)

        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        rect = self._prepare_window()
        if not rect:
            return TaskResult(success=False, error="窗口未找到")

        cv_image, detections = self._fast_capture_and_detect(rect)
        if cv_image is None:
            return TaskResult(success=False, error="截屏失败")

        ta = self.task.try_task(rect, detections)
        return TaskResult(success=ta is not None and len(ta) > 0)

    def _run_task_share(self, ctx: TaskContext) -> TaskResult:
        """分享任务（daily 触发）"""
        if is_silent_time(self.config.silent_hours):
            remaining = get_silent_remaining_seconds(self.config.silent_hours)
            return TaskResult(success=True, next_run_seconds=min(remaining, 300))

        rect = self._prepare_window()
        if not rect:
            return TaskResult(success=False, error="窗口未找到")

        # 分享按钮检测和点击
        cv_image, detections = self._fast_capture_and_detect(rect)
        if cv_image is None:
            return TaskResult(success=False, error="截屏失败")

        return TaskResult(success=True)

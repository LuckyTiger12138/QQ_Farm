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
import sys
import time
import cv2
import numpy as np
from PIL import Image as PILImage
from loguru import logger

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from models.config import AppConfig, PlantMode, RunMode
from models.farm_state import ActionType
from models.game_data import get_best_crop_for_level, get_crop_by_name, format_grow_time
from core.window_manager import WindowManager
from core.screen_capture import ScreenCapture
from core.cv_detector import CVDetector, DetectResult
from core.action_executor import ActionExecutor
from core.task_scheduler import TaskScheduler, BotState
from core.scene_detector import Scene, identify_scene
from core.silent_hours import is_silent_time, get_silent_remaining_seconds
from core.task_executor import TaskExecutor, TaskItem
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
    "btn_steal", "btn_visit_first",
    "friend_check", "btn_friend_apply", "btn_friend_agreed",
    "ui_goto_friend",
    "icon_steal_in_friend_detail",
    "icon_water_in_friend_detail",
    "icon_weed_in_friend_detail",
    "icon_bug_in_friend_detail",
    # 状态图标
    "icon_mature", "icon_bug", "icon_water",
    # UI 元素（异地登录等）
    "ui_remote_login", "ui_next_time", "icon_levelup",
    "ui_shangcheng", "btn_shangcehng_fanhui",
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

    def __init__(self, config: AppConfig, instance_id: str = 'default'):
        super().__init__()
        self.config = config
        self.instance_id = instance_id  # 实例 ID，用于日志和截图分离
        
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
        self._planted_idle_rounds = 0  # 已播种但未检测到收获按钮的连续轮数


        self.scheduler.farm_check_triggered.connect(self._on_farm_check)
        self.scheduler.friend_check_triggered.connect(self._on_friend_check)
        self._request_farm_check.connect(self._on_farm_check)  # 内部信号连接
        self.scheduler.state_changed.connect(self.state_changed.emit)
        self.scheduler.stats_updated.connect(self.stats_updated.emit)
        self.scheduler.window_lost.connect(self._on_window_lost)
        self.scheduler.set_window_check_fn(self._is_window_alive)

    # 策略用快速截屏检测所需的额外模板
    _STRATEGY_EXTRA_TEMPLATES = [
        "btn_expand_confirm", "btn_fertilize_popup",
        "bth_feiliao2_yj", "bth_feiliao_pt",
        "btn_batch_sell", "btn_sell", "btn_cangku",
        "btn_haoyou", "btn_task",
    ]

    def _init_strategies(self):
        """初始化所有策略的依赖"""
        for s in self._strategies:
            s.action_executor = self.action_executor
            s.set_capture_fn(self._fast_strategy_capture)
            s._stop_requested = False
        self.task.sell_config = self.config.sell
        self.plant.auto_buy_seed = self.config.features.auto_buy_seed
        self.plant.auto_fertilize = self.config.features.auto_fertilize

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
        self.config = config
        self.task.sell_config = config.sell
        self.plant.auto_buy_seed = config.features.auto_buy_seed
        self.plant.auto_fertilize = self.config.features.auto_fertilize
        self.config_updated.emit(config)  # 通知 GUI 刷新

        # ✅ 更新调度器定时器间隔（修改配置后自动刷新）
        farm_ms = config.schedule.farm_check_seconds * 1000
        friend_ms = config.schedule.friend_check_seconds * 1000
        self.scheduler.set_farm_interval(farm_ms)
        self.scheduler.set_friend_interval(friend_ms)
        logger.debug(f"调度器间隔已更新: 农场={config.schedule.farm_check_seconds}s, 好友={config.schedule.friend_check_seconds}s")

        # ✅ 重新加载模板元数据（确保修改的阈值配置生效）
        # 用户通过 GUI 修改阈值后，配置已更新到 self.config，
        # 但 cv_detector 内部缓存的阈值字典需要刷新。
        if hasattr(self.cv_detector, 'load_templates'):
            self.cv_detector.load_templates()
            logger.info("模板元数据（含阈值）已重新加载，新配置生效")

        # 注意：此处不再自动唤醒定时器，防止 Web 端轮询配置导致频繁触发。
        # 静默时段检查应在调度器触发任务时进行。

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

        # 使用秒级间隔（配置值 * 1000 转毫秒）
        farm_ms = self.config.schedule.farm_check_seconds * 1000
        friend_ms = self.config.schedule.friend_check_seconds * 1000
        self.scheduler.start(farm_ms, friend_ms)
        mode_text = "后台" if run_mode == RunMode.BACKGROUND else "前台"
        self.log_message.emit(f"Bot已启动 - 窗口: {window.title} | 模板: {tpl_count}个 | 模式: {mode_text}")
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

    def run_friend_once(self):
        """手动触发好友巡查"""
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

    def _on_farm_finished(self, result: dict):
        """农场任务完成回调"""
        if self.scheduler.state == BotState.IDLE:
            return
        self._is_busy = False
        self._record_actions(result)

        next_sec = result.get("next_check_seconds", 0)
        if next_sec > 0:
            self.scheduler.set_farm_interval(next_sec * 1000)  # 转为毫秒

        # 农场任务完成后，如果好友巡查时间已到（或从未执行过），立即触发
        friend_cfg = self.config.features.friend
        has_friend_feature = (friend_cfg.enable_steal or friend_cfg.enable_weed
                              or friend_cfg.enable_water or friend_cfg.enable_bug)
        if self.action_executor and has_friend_feature:
            now = time.time()
            next_friend = self.scheduler._next_friend_check
            # next_friend == 0 表示从未执行过好友巡查，应立即触发
            if next_friend == 0 or now >= next_friend:
                logger.info("农场任务完成，触发好友巡查")
                self._on_friend_check()

    def _on_friend_finished(self, result: dict):
        """好友巡查完成回调"""
        if self.scheduler.state == BotState.IDLE:
            logger.debug("Bot 已停止，忽略好友巡查完成回调")
            return
        self._is_busy = False
        self._record_actions(result)
        logger.info("好友巡查完成，立即触发农场检查")
        self._request_farm_check.emit()

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
        window = self.window_manager._cached_window
        if not window:
            window = self.window_manager.refresh_window_info(
                self.config.window_title_keyword,
                auto_launch=True,
                shortcut_path=self.config.planting.game_shortcut_path,
            )
        else:
            window = self.window_manager.find_window(
                self.config.window_title_keyword,
                auto_launch=True,
                shortcut_path=self.config.planting.game_shortcut_path,
                select_rule=self.config.window_select_rule
            )
        if not window:
            window = self.window_manager.refresh_window_info(
                self.config.window_title_keyword,
                auto_launch=True,
                shortcut_path=self.config.planting.game_shortcut_path,
            )
        else:
            # 刷新缓存窗口的最新位置
            self.window_manager.find_window(
                self.config.window_title_keyword,
                select_rule=self.config.window_select_rule
            )
            window = self.window_manager._cached_window

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
            "auto_bug", "auto_fertilize", "auto_sell", "auto_upgrade",
            "auto_task", "auto_bad",
        }
        has_farm_work = any(features.get(f, False) for f in farm_features)

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

        idle_rounds = 0
        max_idle = 1  # ✅ 优化：降为 1，一轮无操作即退出（避免无效等待）
        
        # ✅ 添加状态变化检测：避免重复相同检测
        prev_scene = None
        prev_detection_keys = set()
        consecutive_same_state = 0  # 连续相同状态轮数
        max_consecutive_same = 2  # 最多允许连续 2 轮相同状态

        # 初始化任务执行器
        executor = TaskExecutor()

        # 注册任务（按优先级）
        # 注意：这里只注册一次，因为任务的 enabled_fn 等逻辑在内部会动态判断
        
        # P-1: 弹窗处理
        executor.add_task(TaskItem(
            name="Popup",
            priority=-1,
            enabled_fn=lambda: True,
            check_fn=lambda ctx: ctx.get("scene") in (Scene.POPUP, Scene.INFO_PAGE, Scene.SHOP_PAGE),
            run_fn=lambda ctx: self._task_popup(ctx)
        ))

        # P0: 收获
        executor.add_task(TaskItem(
            name="Harvest",
            priority=0,
            enabled_fn=lambda: self.config.features.auto_harvest,
            check_fn=lambda ctx: ctx.get("scene") == Scene.FARM_OVERVIEW,
            run_fn=lambda ctx: self._task_harvest(ctx)
        ))

        # P1: 维护
        executor.add_task(TaskItem(
            name="Maintain",
            priority=1,
            enabled_fn=lambda: any([self.config.features.auto_weed, self.config.features.auto_water, self.config.features.auto_bug]),
            check_fn=lambda ctx: ctx.get("scene") == Scene.FARM_OVERVIEW,
            run_fn=lambda ctx: self._task_maintain(ctx)
        ))

        # P2: 播种
        executor.add_task(TaskItem(
            name="Plant",
            priority=2,
            enabled_fn=lambda: self.config.features.auto_plant,
            check_fn=lambda ctx: ctx.get("scene") == Scene.FARM_OVERVIEW,
            run_fn=lambda ctx: self._task_plant(ctx)
        ))

        # P3: 扩建
        executor.add_task(TaskItem(
            name="Expand",
            priority=3,
            enabled_fn=lambda: self.config.features.auto_upgrade,
            check_fn=lambda ctx: ctx.get("scene") == Scene.FARM_OVERVIEW,
            run_fn=lambda ctx: self._task_expand(ctx)
        ))

        # P3.5: 任务/出售
        executor.add_task(TaskItem(
            name="Task",
            priority=4,
            enabled_fn=lambda: self.config.features.auto_task,
            check_fn=lambda ctx: ctx.get("scene") == Scene.FARM_OVERVIEW,
            run_fn=lambda ctx: self._task_task(ctx)
        ))

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
                self._handle_remote_login(context)
                continue

            # 执行任务调度
            if executor.execute(context):
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
        """处理弹窗"""
        scene = context.get("scene")
        detections = context.get("detections", [])
        rect = context.get("rect")
        
        if scene == Scene.POPUP:
            return self.popup.handle_popup(detections) is not None
        elif scene == Scene.INFO_PAGE:
            info_close = self.popup.find_any(detections, ["btn_close", "btn_info_close", "btn_rw_close"])
            if info_close:
                self.popup.click(info_close.x, info_close.y, "关闭个人信息页面")
                return True
        elif scene in (Scene.SHOP_PAGE, Scene.BUY_CONFIRM, Scene.PLOT_MENU, Scene.LEVEL_UP):
            return self.popup.handle_popup(detections) is not None
        elif scene == Scene.MALL_PAGE:
            mall_back = self.popup.find_by_name(detections, "btn_shangcehng_fanhui")
            if mall_back:
                self.popup.click(mall_back.x, mall_back.y, "关闭商城")
                return True
            self.popup.click_blank(rect)
            return True
        return False

    def _task_harvest(self, context: dict) -> bool:
        """收获任务"""
        detections = context.get("detections", [])
        desc = self.harvest.try_harvest(detections)
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
        """维护任务（除草/除虫/浇水）"""
        detections = context.get("detections", [])
        features = context.get("features", {})
        return self.maintain.try_maintain(detections, features) is not None

    def _task_plant(self, context: dict) -> bool:
        """播种任务"""
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

    def _task_task(self, context: dict) -> bool:
        """任务/出售任务"""
        rect = context.get("rect")
        detections = context.get("detections", [])
        ta = self.task.try_task(rect, detections)
        return ta is not None and len(ta) > 0

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

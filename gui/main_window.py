"""主窗口 — 浅色毛玻璃侧边栏导航布局

布局: 标题栏 + (侧边栏 | 内容区 QStackedWidget)
支持多实例切换：右侧实例栏 + 主内容区
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QStackedWidget,
    QMessageBox, QInputDialog,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QImage
from PIL import Image

from loguru import logger

from models.config import AppConfig
from core.bot_engine import BotEngine
from core.instance_manager import InstanceManager, InstanceSession
from core.cross_instance_bus import CrossInstanceBus
from gui.styles import Colors, GLASS_STYLESHEET, glass_button_style
from gui.widgets.sidebar import Sidebar
from gui.widgets.log_panel import LogPanel
from gui.widgets.status_panel import StatusPanel
from gui.widgets.settings_panel import SettingsPanel
from gui.widgets.template_panel import TemplatePanel
from gui.widgets.instance_sidebar import InstanceSidebar
from gui.widgets.land_detail_panel import LandDetailPanel
from gui.widgets.task_panel import TaskPanel
from gui.widgets.feature_panel import FeaturePanel
from gui.widgets.global_settings_panel import GlobalSettingsPanel
from utils.logger import get_log_signal


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, instance_manager: InstanceManager | None = None):
        super().__init__()
        self.config = config
        self.instance_manager = instance_manager
        self._first_show = True
        
        # 跨实例通讯消息总线（所有引擎共享）
        self._cross_bus = CrossInstanceBus()
        
        # 多实例支持：instance_id -> BotEngine
        self._engines: dict[str, BotEngine] = {}
        self._current_instance_id: str = 'default'
        
        # 如果有实例管理器，为当前活动实例创建引擎
        instance_id = 'default'
        if instance_manager:
            active = instance_manager.get_active()
            if active:
                instance_id = active.instance_id
        
        self.engine = BotEngine(config, instance_id=instance_id, cross_bus=self._cross_bus)
        self._current_instance_id = instance_id
        if instance_manager:
            self._engines[instance_id] = self.engine
        
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        self.setWindowTitle("QQ Farm Vision Bot - 多实例版 | F11老板键")
        self.setMinimumSize(1100, 680)
        self.resize(1200, 740)

        self.setStyleSheet(GLASS_STYLESHEET)

        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"""
            QWidget {{
                background-color: {Colors.WINDOW_BG};
            }}
        """)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 主体：左侧导航 + 内容区 + 右侧实例栏 ──
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # 左侧导航栏
        self._sidebar = Sidebar()
        self._sidebar.navigation_changed.connect(self._on_navigation)
        body.addWidget(self._sidebar)

        # ── 内容区 ──
        content = QWidget()
        content.setStyleSheet("background: transparent; border: none;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 12, 16, 12)
        content_layout.setSpacing(8)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent; border: none;")

        # 页面 0: 状态页
        status_page = self._build_status_page()
        self._stack.addWidget(status_page)

        # 页面 1: 地块详情页
        self._land_panel = LandDetailPanel(self.config)
        self._land_panel.refresh_requested.connect(self._on_land_refresh_requested)
        self._land_panel.config_changed.connect(self._on_config_changed)
        self._stack.addWidget(self._land_panel)

        # 页面 2: 任务调度页
        self._task_panel = TaskPanel(self.config)
        self._task_panel.config_changed.connect(self._on_config_changed)
        self._stack.addWidget(self._task_panel)

        # 页面 3: 功能配置页
        self._feature_panel = FeaturePanel(self.config)
        self._feature_panel.config_changed.connect(self._on_config_changed)
        self._stack.addWidget(self._feature_panel)

        # 页面 4: 设置页
        self._settings_panel = SettingsPanel(self.config)
        self._stack.addWidget(self._settings_panel)

        # 页面 5: 模板管理页
        self._template_panel = TemplatePanel(self.engine.cv_detector)
        # 设置回调：实时读取当前活跃实例的窗口关键字和选择规则
        self._template_panel._get_window_keyword = self._get_active_window_keyword
        self._template_panel._get_window_select_rule = self._get_active_window_select_rule
        self._stack.addWidget(self._template_panel)

        # 页面 6: 全局设置页
        self._global_panel = GlobalSettingsPanel()
        self._stack.addWidget(self._global_panel)

        # 页面 7: 日志页
        self._log_panel = LogPanel()
        self._stack.addWidget(self._log_panel)

        # 状态面板定时刷新（每秒）
        self._status_refresh_timer = QTimer(self)
        self._status_refresh_timer.setInterval(1000)
        self._status_refresh_timer.timeout.connect(self._refresh_status)

        content_layout.addWidget(self._stack)
        body.addWidget(content, 1)

        # 右侧实例栏（如果启用了实例管理器）
        if self.instance_manager:
            self._instance_sidebar = InstanceSidebar()
            self._instance_sidebar.instance_selected.connect(self._on_instance_selected)
            self._instance_sidebar.instance_start_requested.connect(self._on_instance_start)
            self._instance_sidebar.instance_stop_requested.connect(self._on_instance_stop)
            self._instance_sidebar.start_all_requested.connect(self._on_start_all)
            self._instance_sidebar.stop_all_requested.connect(self._on_stop_all)
            self._instance_sidebar.create_requested.connect(self._on_create_instance)
            self._instance_sidebar.delete_requested.connect(self._on_delete_instance)
            self._instance_sidebar.clone_requested.connect(self._on_clone_instance)
            self._instance_sidebar.rename_requested.connect(self._on_rename_instance)
            body.addWidget(self._instance_sidebar)
            self._refresh_instance_sidebar()

        root.addLayout(body, 1)

        # ── 底部开源横幅 ──
        banner = QLabel(
            "本软件免费开源  |  如果你花钱购买的，请立即退款！  "
            "GitHub: github.com/luckytiger12138/qq-farm  "
            "Gitee: gitee.com/luckytiger12138/qq-farm"
        )
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner.setFixedHeight(28)
        banner.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0, 122, 255, 12);
                color: {Colors.PRIMARY};
                font-size: 12px;
                border-top: 1px solid rgba(0, 122, 255, 30);
                padding: 0 12px;
            }}
        """)
        root.addWidget(banner)

    def _build_status_page(self) -> QWidget:
        """构建状态页：截图预览 + 统计面板 + 控制按钮"""
        page = QWidget()
        page.setStyleSheet("background: transparent; border: none;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 上半部分：统计面板（左）+ 截图预览（右）
        top = QHBoxLayout()
        top.setSpacing(12)

        # 左侧：统计面板
        left_container = QVBoxLayout()
        left_container.setSpacing(8)

        self._status_panel = StatusPanel()
        left_container.addWidget(self._status_panel, 1)
        left_container.addStretch()

        top.addLayout(left_container, 1)

        # 右侧：截图预览卡片
        preview_card = QFrame()
        preview_card.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        preview_card.setFixedWidth(300)
        pv_layout = QVBoxLayout(preview_card)
        pv_layout.setContentsMargins(6, 6, 6, 6)
        self._screenshot_label = QLabel("启动后显示\n实时截图")
        self._screenshot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._screenshot_label.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0, 0, 0, 10);
                border: 1px dashed {Colors.BORDER};
                border-radius: 8px;
                color: {Colors.TEXT_DIM};
                font-size: 14px;
            }}
        """)
        pv_layout.addWidget(self._screenshot_label)
        top.addWidget(preview_card)

        layout.addLayout(top, 1)

        # 底部：控制按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_start = self._make_btn("开始", Colors.SUCCESS, "#15803d")
        self._btn_pause = self._make_btn("暂停", Colors.WARNING, "#b45309")
        self._btn_stop = self._make_btn("停止", Colors.DANGER, "#b91c1c")

        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)

        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_stop.clicked.connect(self._on_stop)

        for b in (self._btn_start, self._btn_pause, self._btn_stop):
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return page

    def _make_btn(self, text: str, color: str, hover: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(38)
        btn.setStyleSheet(glass_button_style(color, hover))
        return btn

    def _connect_signals(self):
        self.engine.log_message.connect(self._log_panel.append_log)
        # 截图信号使用 instance_id 过滤
        engine_id = self.engine.instance_id
        self.engine.screenshot_updated.connect(lambda img, iid=engine_id: self._on_screenshot_updated(iid, img))
        self.engine.detection_result.connect(lambda det, iid=engine_id: self._on_screenshot_updated(iid, det))
        self.engine.state_changed.connect(self._on_state_changed)
        self.engine.stats_updated.connect(self._status_panel.update_stats)
        self.engine.stats_updated.connect(self._on_stats_for_task_panel)
        get_log_signal().new_log.connect(self._log_panel.append_log)
        self._settings_panel.config_changed.connect(self._on_config_changed)
        self._settings_panel.web_server_toggled.connect(self._on_web_server_toggled)
        self.engine.config_updated.connect(lambda cfg: self._on_config_updated_filtered(self._current_instance_id, cfg))

    # ── 导航切换 ────────────────────────────────────────────

    def _on_navigation(self, key: str):
        page_map = {"status": 0, "land": 1, "task": 2, "feature": 3, "settings": 4, "template": 5, "global": 6, "logs": 7}
        idx = page_map.get(key, 0)
        self._stack.setCurrentIndex(idx)

    # ── 截图更新 ────────────────────────────────────────────

    def _update_screenshot(self, image: Image.Image):
        """更新截图预览"""
        try:
            image = image.convert("RGB")
            data = image.tobytes("raw", "RGB")
            qimg = QImage(data, image.width, image.height,
                          3 * image.width, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                self._screenshot_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._screenshot_label.setPixmap(scaled)
        except Exception:
            pass

    def _on_screenshot_updated(self, instance_id: str, image: Image.Image):
        """截图更新：只显示当前选中实例的截图"""
        if instance_id == self._current_instance_id:
            self._update_screenshot(image)

    # ── 控制按钮 ────────────────────────────────────────────

    def _on_start(self):
        if self.engine.start():
            self._btn_start.setEnabled(False)
            self._btn_pause.setEnabled(True)
            self._btn_stop.setEnabled(True)
            self._status_refresh_timer.start()

    def _on_pause(self):
        if self._btn_pause.text() == "暂停":
            self.engine.pause()
            self._btn_pause.setText("恢复")
            self._status_refresh_timer.stop()
        else:
            self.engine.resume()
            self._btn_pause.setText("暂停")
            self._status_refresh_timer.start()

    def _on_stop(self):
        self.engine.stop()
        self._btn_start.setEnabled(True)
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._btn_pause.setText("暂停")
        self._status_refresh_timer.stop()

    def _on_state_changed(self, state: str):
        """Bot 状态变化时更新按钮"""
        state_map = {"running": True, "paused": True, "idle": False, "error": False}
        running = state_map.get(state, False)
        paused = state == "paused"
        self._btn_start.setEnabled(not running)
        self._btn_pause.setEnabled(running)
        self._btn_stop.setEnabled(running)
        if paused:
            self._btn_pause.setText("恢复")
        else:
            self._btn_pause.setText("暂停")
        self._refresh_status()
        
        # 同时更新实例侧边栏状态
        if self.instance_manager and self._current_instance_id:
            self._on_instance_state_changed(self._current_instance_id, state)

    def _refresh_status(self):
        """定时刷新状态面板数据"""
        idx = self._stack.currentIndex()
        if idx == 0:
            # scheduler.get_stats() 已包含 runtime_metrics（由执行器快照回调实时更新）
            self._status_panel.update_stats(self.engine.scheduler.get_stats())
        # 始终刷新任务调度面板的 next_run（不限制当前页面）
        if self.engine and self.engine._async_executor:
            self._task_panel.refresh_snapshots(self.engine._task_snapshots)

    def _on_stats_for_task_panel(self, _stats):
        """stats_updated 信号触发时同步刷新任务调度面板"""
        if self.engine and self.engine._task_snapshots:
            self._task_panel.refresh_snapshots(self.engine._task_snapshots)

    def _on_config_changed(self, config: AppConfig):
        """设置面板配置变更时，只更新当前显示的实例"""
        self.config = config
        self.engine.update_config(config)

    def _on_config_updated(self, config: AppConfig):
        """引擎配置更新时同步 GUI（如地块巡查完成、Web 端修改配置）"""
        if config != self.config:
            return
        self._settings_panel.config = config
        self._settings_panel._loading += 1
        self._settings_panel._load_config()
        self._settings_panel._loading -= 1
        # 刷新地块详情面板
        self._land_panel.set_config(config)

    def _on_config_updated_filtered(self, instance_id: str, config: AppConfig):
        """带实例ID过滤的配置更新处理"""
        if instance_id != self._current_instance_id:
            return
        self._on_config_updated(config)

    def _on_land_refresh_requested(self):
        """地块详情页「立即刷新」按钮：触发 OCR 识别个人信息"""
        try:
            # 确保 action_executor 已设置
            if not self.engine.action_executor:
                from loguru import logger
                logger.debug("地块刷新: action_executor 为 None，尝试初始化")
                if not self.engine.start():
                    logger.warning("地块刷新: start() 失败")
                    return

            rect = self.engine._prepare_window()
            if rect:
                self.engine._sync_head_profile_from_ocr(rect)
                self.engine._sync_detail_exp(rect)
            else:
                self.engine._sync_head_profile_from_ocr()
        except Exception:
            pass

    def _on_web_server_toggled(self, start: bool):
        """Web 服务启动/停止"""
        from loguru import logger
        logger.info(f"MainWindow._on_web_server_toggled: start={start}")
        logger.info(f"self.web_server: {getattr(self, 'web_server', None) is not None}")

        if start:
            logger.info("调用 _start_web_server")
            import main as _main
            _main._start_web_server(self.config, self)
        else:
            logger.info("调用 web_server.stop()")
            # 直接使用 window 对象上存储的 web_server 实例，避免模块导入问题
            # 注意：不能使用 import main，因为当 main.py 作为 __main__ 运行时，
            # import main 会创建一个新的模块实例，无法访问 _global_web_server
            web = getattr(self, 'web_server', None)
            if web:
                logger.info(f"找到 web_server 实例: {id(web)}")
                web.stop()
                self.web_server = None
                logger.info("web_server.stop() 已调用")
            else:
                logger.warning("self.web_server 为 None，无法停止 Web 服务")

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(300, self._show_opensource_notice)

    def closeEvent(self, event):
        self.unregister_hotkeys()
        self.engine.stop()
        super().closeEvent(event)

    # ── 全局热键 ──────────────────────────────────────────

    def register_hotkeys(self):
        """注册 F9/F10/F11 全局热键"""
        try:
            import keyboard
            keyboard.on_press_key("f9", lambda _: self._on_hotkey_pause())
            keyboard.on_press_key("f10", lambda _: self._on_hotkey_stop())
            keyboard.on_press_key("f11", lambda _: self._on_hotkey_boss_key())  # 老板键
            logger.info("全局热键已注册: F9=暂停/恢复, F10=停止, F11=老板键（隐藏窗口）")
        except Exception as e:
            logger.warning(f"全局热键注册失败（可能需要管理员权限）: {e}")

    def unregister_hotkeys(self):
        """注销全局热键"""
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass

    def _on_hotkey_pause(self):
        """F9: 暂停/恢复"""
        if self._btn_start.isEnabled():
            # Bot 未运行，忽略
            return
        if self._btn_pause.text() == "暂停":
            self.engine.pause()
            self._btn_pause.setText("恢复")
            self.engine.log_message.emit("[热键] F9 已暂停")
        else:
            self.engine.resume()
            self._btn_pause.setText("暂停")
            self.engine.log_message.emit("[热键] F9 已恢复")

    def _on_hotkey_stop(self):
        """F10: 停止"""
        if self._btn_start.isEnabled():
            # Bot 未运行，忽略
            return
        self.engine.stop()
        self._btn_start.setEnabled(True)
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._btn_pause.setText("暂停")
        self.engine.log_message.emit("[热键] F10 已停止")

    def _on_hotkey_boss_key(self):
        """F11: 老板键（隐藏/显示游戏窗口）"""
        result = self.engine.toggle_game_window()
        # 更新窗口标题提示
        if result.get("hidden"):
            self.setWindowTitle("QQ Farm Vision Bot - 游戏已完美隐藏 | F11恢复")
        else:
            self.setWindowTitle("QQ Farm Vision Bot - 多实例版 | F11老板键")

    def _show_opensource_notice(self):
        """启动时提醒用户本软件免费开源"""
        msg = QMessageBox(self)
        msg.setWindowTitle("免费开源声明")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            "<h3 style='color:#007AFF;'>本软件完全免费开源！</h3>"
            "<p style='font-size:14px;'>如果你花钱购买的，请立即退款！</p>"
            "<p style='font-size:13px;'>"
            "官方仓库（免费下载）：<br>"
            "<b>GitHub</b>: github.com/luckytiger12138/qq-farm<br>"
            "<b>Gitee</b>: gitee.com/luckytiger12138/qq-farm"
            "</p>"
            "<hr style='border:1px solid #ddd;'>"
            "<p style='font-size:12px; color:#888;'>任何倒卖行为均违反开源协议，请勿上当受骗。</p>"
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    # ── 多实例切换 ──────────────────────────────────────────

    def _refresh_instance_sidebar(self):
        """刷新实例侧边栏显示"""
        if not self.instance_manager:
            return
        sessions = self.instance_manager.iter_sessions()
        instances = []
        for session in sessions:
            # 获取引擎状态
            eng = self._engines.get(session.instance_id)
            state = session.state
            if eng:
                from core.task_scheduler import BotState
                state_map = {
                    BotState.RUNNING: 'running',
                    BotState.PAUSED: 'paused',
                    BotState.IDLE: 'idle',
                    BotState.ERROR: 'error',
                }
                state = state_map.get(eng.scheduler.state, session.state)
            instances.append({
                'id': session.instance_id,
                'name': session.name,
                'state': state,
            })
        self._instance_sidebar.set_instances(instances)
        self._instance_sidebar.set_active_instance(self._current_instance_id)

    def _get_active_window_keyword(self) -> str:
        """实时获取当前活跃实例配置的窗口关键字"""
        try:
            if self.instance_manager:
                session = self.instance_manager.get_session(self._current_instance_id)
                if session and session.config:
                    kw = session.config.window_title_keyword or "QQ经典农场"
                    logger.debug(f"[MainWindow] 当前实例 '{self._current_instance_id}' 窗口关键字: '{kw}'")
                    return kw
        except Exception as e:
            logger.warning(f"[MainWindow] 获取窗口关键字失败: {e}")
        return self.config.window_title_keyword or "QQ经典农场"

    def _get_active_window_select_rule(self) -> str:
        """实时获取当前活跃实例配置的窗口选择规则"""
        try:
            if self.instance_manager:
                session = self.instance_manager.get_session(self._current_instance_id)
                if session and session.config:
                    return session.config.window_select_rule or "auto"
        except Exception as e:
            logger.warning(f"[MainWindow] 获取窗口选择规则失败: {e}")
        return self.config.window_select_rule or "auto"

    def _on_instance_selected(self, instance_id: str):
        """用户点击实例，切换到该实例（仅切换 UI，不停止其他实例）"""
        if not self.instance_manager:
            return
        try:
            self._switch_to_instance(instance_id)
        except Exception as e:
            logger.error(f"切换实例失败: {e}")
            QMessageBox.critical(self, "错误", f"切换实例失败: {e}")

    def _switch_to_instance(self, instance_id: str):
        """切换到指定实例（仅切换 UI 显示，不影响其他实例运行）"""
        if instance_id == self._current_instance_id:
            return

        session = self.instance_manager.get_session(instance_id)
        if session is None:
            raise KeyError(f'实例不存在: {instance_id}')

        # 注意：不再自动停止当前引擎，允许多个实例同时运行
        # 只是切换当前显示的 UI 上下文

        # 切换活动实例
        self.instance_manager.switch_active(instance_id)
        self._current_instance_id = instance_id

        # 获取或创建该实例的 BotEngine
        if instance_id not in self._engines:
            new_engine = BotEngine(session.config, instance_id=instance_id, cross_bus=self._cross_bus)
            self._engines[instance_id] = new_engine
            # 连接信号（截图信号使用 instance_id 过滤，只显示当前选中实例）
            new_engine.log_message.connect(self._log_panel.append_log)
            new_engine.screenshot_updated.connect(lambda img, iid=instance_id: self._on_screenshot_updated(iid, img))
            new_engine.detection_result.connect(lambda det, iid=instance_id: self._on_screenshot_updated(iid, det))
            new_engine.state_changed.connect(lambda state, iid=instance_id: self._on_instance_state_changed(iid, state))
            new_engine.stats_updated.connect(lambda stats, iid=instance_id: self._status_panel.update_stats(stats) if iid == self._current_instance_id else None)
            new_engine.stats_updated.connect(lambda stats, iid=instance_id: self._on_stats_for_task_panel(stats) if iid == self._current_instance_id else None)
            new_engine.config_updated.connect(lambda cfg, iid=instance_id: self._on_config_updated_filtered(iid, cfg))
            logger.info(f"已创建实例 {instance_id} 的 BotEngine，window_select_rule={session.config.window_select_rule}")
        else:
            new_engine = self._engines[instance_id]
            logger.info(f"使用已存在的实例 {instance_id} 的 BotEngine，window_select_rule={session.config.window_select_rule}")

        # 更新当前引擎引用（用于当前显示的 UI 上下文）
        self.engine = new_engine
        self.config = session.config

        # 根据当前实例状态更新按钮和截图显示
        from core.task_scheduler import BotState
        state = new_engine.scheduler.state
        
        # 清空截图显示，避免显示其他实例的旧截图
        if state != BotState.RUNNING:
            self._screenshot_label.setText("启动后显示\n实时截图")
            self._screenshot_label.setPixmap(QPixmap())

        # 关键：切换实例后，刷新实例侧边栏显示
        self._refresh_instance_sidebar()

        # 更新 UI 面板
        logger.info(f"🔄 切换到实例 {instance_id}: session.config_id={id(session.config)}")
        self._settings_panel.config = session.config
        logger.info(f"📋 settings_panel.config_id={id(self._settings_panel.config)}")
        self._settings_panel._loading += 1
        self._settings_panel._load_config()
        self._settings_panel._loading -= 1

        # 更新模板面板
        self._template_panel._detector = new_engine.cv_detector
        self._template_panel._window_keyword = session.config.window_title_keyword or "QQ经典农场"
        self._template_panel._load_templates()

        # 更新地块详情面板
        self._land_panel.set_config(session.config)

        # 更新任务调度面板
        self._task_panel.set_config(session.config)

        # 更新功能配置面板
        self._feature_panel.set_config(session.config)

        # 更新实例侧边栏高亮
        self._instance_sidebar.set_active_instance(instance_id)

        # 根据当前实例状态更新按钮
        if state == BotState.RUNNING:
            self._btn_start.setEnabled(False)
            self._btn_pause.setEnabled(True)
            self._btn_stop.setEnabled(True)
            self._btn_pause.setText("暂停")
        elif state == BotState.PAUSED:
            self._btn_start.setEnabled(False)
            self._btn_pause.setEnabled(True)
            self._btn_stop.setEnabled(True)
            self._btn_pause.setText("恢复")
        else:
            self._btn_start.setEnabled(True)
            self._btn_pause.setEnabled(False)
            self._btn_stop.setEnabled(False)
            self._btn_pause.setText("暂停")

        logger.info(f"已切换到实例: {session.name} ({instance_id})")

    def _get_or_create_engine(self, session: InstanceSession) -> BotEngine:
        """获取或创建实例的 BotEngine"""
        instance_id = session.instance_id
        if instance_id not in self._engines:
            engine = BotEngine(session.config, instance_id=instance_id, cross_bus=self._cross_bus)
            self._engines[instance_id] = engine
            # 连接信号（截图信号使用 instance_id 过滤，只显示当前选中实例）
            engine.log_message.connect(self._log_panel.append_log)
            engine.screenshot_updated.connect(lambda img, iid=instance_id: self._on_screenshot_updated(iid, img))
            engine.detection_result.connect(lambda det, iid=instance_id: self._on_screenshot_updated(iid, det))
            engine.state_changed.connect(lambda state, iid=instance_id: self._on_instance_state_changed(iid, state))
            engine.stats_updated.connect(lambda stats, iid=instance_id: self._status_panel.update_stats(stats) if iid == self._current_instance_id else None)
            engine.stats_updated.connect(lambda stats, iid=instance_id: self._on_stats_for_task_panel(stats) if iid == self._current_instance_id else None)
            engine.config_updated.connect(lambda cfg, iid=instance_id: self._on_config_updated_filtered(iid, cfg))
        return self._engines[instance_id]

    def _on_create_instance(self):
        """新增实例"""
        name, ok = QInputDialog.getText(self, "新增实例", "实例名称：")
        if not ok or not name.strip():
            return
        try:
            session = self.instance_manager.create_instance(name.strip())
            self._get_or_create_engine(session)
            self._refresh_instance_sidebar()
            self._switch_to_instance(session.instance_id)
            logger.info(f"已创建实例: {session.name} ({session.instance_id})")
        except Exception as e:
            logger.error(f"创建实例失败: {e}")
            QMessageBox.critical(self, "错误", f"创建实例失败: {e}")

    def _on_delete_instance(self, instance_id: str):
        """删除实例"""
        session = self.instance_manager.get_session(instance_id)
        if not session:
            return
        if self.instance_manager.iter_sessions().__len__() <= 1:
            QMessageBox.warning(self, "警告", "不能删除最后一个实例")
            return

        # 确认删除
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除实例 '{session.name}' 吗？\n\n注意：该实例的配置文件将被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # 如果删除的是当前实例，先切换到其他实例
            is_current = (instance_id == self._current_instance_id)
            if is_current:
                # 停止引擎
                if self._btn_start.isEnabled() is False:
                    self.engine.stop()
                # 找到另一个实例
                other = next(
                    (s for s in self.instance_manager.iter_sessions() if s.instance_id != instance_id),
                    None
                )
                if other:
                    self._switch_to_instance(other.instance_id)

            # 删除引擎缓存
            if instance_id in self._engines:
                del self._engines[instance_id]

            # 删除实例
            self.instance_manager.delete_instance(instance_id)
            self._refresh_instance_sidebar()
            logger.info(f"已删除实例: {session.name} ({instance_id})")
        except Exception as e:
            logger.error(f"删除实例失败: {e}")
            QMessageBox.critical(self, "错误", f"删除实例失败: {e}")

    def _on_clone_instance(self, source_instance_id: str):
        """克隆实例"""
        session = self.instance_manager.get_session(source_instance_id)
        if not session:
            return

        name, ok = QInputDialog.getText(
            self, "克隆实例",
            f"从 '{session.name}' 克隆新实例，名称：",
        )
        if not ok or not name.strip():
            return

        try:
            new_session = self.instance_manager.clone_instance(source_instance_id, name.strip())
            self._get_or_create_engine(new_session)
            self._refresh_instance_sidebar()
            self._switch_to_instance(new_session.instance_id)
            logger.info(f"已克隆实例: {new_session.name} ({new_session.instance_id})")
        except Exception as e:
            logger.error(f"克隆实例失败: {e}")
            QMessageBox.critical(self, "错误", f"克隆实例失败: {e}")

    def _on_rename_instance(self, instance_id: str):
        """重命名实例"""
        session = self.instance_manager.get_session(instance_id)
        if not session:
            return

        name, ok = QInputDialog.getText(
            self, "重命名实例",
            "新名称：",
            text=session.name,
        )
        if not ok or not name.strip():
            return

        try:
            self.instance_manager.rename_instance(instance_id, name.strip())
            self._refresh_instance_sidebar()
            logger.info(f"已重命名实例: {session.name} -> {name} ({instance_id})")
        except Exception as e:
            logger.error(f"重命名实例失败: {e}")
            QMessageBox.critical(self, "错误", f"重命名实例失败: {e}")

    # ── 多实例并发运行 ─────────────────────────────────────

    def _on_instance_start(self, instance_id: str):
        """启动指定实例"""
        session = self.instance_manager.get_session(instance_id)
        if not session:
            return
        engine = self._get_or_create_engine(session)
        # 启动前同步更新设置面板的配置引用，确保后续保存写入该实例的配置文件
        # 仅当启动的是当前显示的实例时才更新设置面板
        if instance_id == self._current_instance_id:
            self._settings_panel.config = session.config
            self._settings_panel._loading += 1
            self._settings_panel._load_config()
            self._settings_panel._loading -= 1
        if engine.start():
            self._instance_sidebar.update_instance_state(instance_id, 'running')
            logger.info(f"实例 {session.name} 已启动")

    def _on_instance_stop(self, instance_id: str):
        """停止指定实例"""
        if instance_id in self._engines:
            self._engines[instance_id].stop()
            self._instance_sidebar.update_instance_state(instance_id, 'idle')
            session = self.instance_manager.get_session(instance_id)
            if session:
                logger.info(f"实例 {session.name} 已停止")

    def _on_instance_state_changed(self, instance_id: str, state: str):
        """实例状态变化时更新侧边栏显示"""
        from core.task_scheduler import BotState
        state_map = {
            BotState.RUNNING: 'running',
            BotState.PAUSED: 'paused',
            BotState.IDLE: 'idle',
            BotState.ERROR: 'error',
        }
        sidebar_state = state_map.get(state, 'idle')
        self._instance_sidebar.update_instance_state(instance_id, sidebar_state)

        # 如果是当前选中的实例，更新按钮状态
        if instance_id == self._current_instance_id:
            if state == BotState.RUNNING:
                self._btn_start.setEnabled(False)
                self._btn_pause.setEnabled(True)
                self._btn_stop.setEnabled(True)
                self._btn_pause.setText("暂停")
            elif state == BotState.PAUSED:
                self._btn_start.setEnabled(False)
                self._btn_pause.setEnabled(True)
                self._btn_stop.setEnabled(True)
                self._btn_pause.setText("恢复")
            else:
                self._btn_start.setEnabled(True)
                self._btn_pause.setEnabled(False)
                self._btn_stop.setEnabled(False)
                self._btn_pause.setText("暂停")

    def _on_start_all(self):
        """启动所有实例"""
        sessions = self.instance_manager.iter_sessions()
        started = 0
        for session in sessions:
            engine = self._get_or_create_engine(session)
            # 只启动未运行的实例
            from core.task_scheduler import BotState
            if engine.scheduler.state != BotState.RUNNING:
                if engine.start():
                    self._instance_sidebar.update_instance_state(session.instance_id, 'running')
                    started += 1
        logger.info(f"已启动 {started} 个实例")
        self.engine.log_message.emit(f"✓ 已启动 {started} 个实例")

    def _on_stop_all(self):
        """停止所有实例"""
        stopped = 0
        for eid, engine in list(self._engines.items()):
            from core.task_scheduler import BotState
            if engine.scheduler.state in (BotState.RUNNING, BotState.PAUSED):
                engine.stop()
                self._instance_sidebar.update_instance_state(eid, 'idle')
                stopped += 1
        logger.info(f"已停止 {stopped} 个实例")
        if self.engine:
            self.engine.log_message.emit(f"✓ 已停止 {stopped} 个实例")

    def closeEvent(self, event):
        """关闭事件：停止所有引擎"""
        self.unregister_hotkeys()
        # 停止当前引擎
        self.engine.stop()
        # 停止所有其他引擎
        for eid, eng in self._engines.items():
            if eng != self.engine:
                eng.stop()
        super().closeEvent(event)

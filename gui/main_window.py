"""主窗口 — 浅色毛玻璃侧边栏导航布局

布局: 标题栏 + (侧边栏 | 内容区 QStackedWidget)
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QStackedWidget,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QImage
from PIL import Image

from loguru import logger

from models.config import AppConfig
from core.bot_engine import BotEngine
from gui.styles import Colors, GLASS_STYLESHEET, glass_button_style
from gui.widgets.sidebar import Sidebar
from gui.widgets.log_panel import LogPanel
from gui.widgets.status_panel import StatusPanel
from gui.widgets.settings_panel import SettingsPanel
from gui.widgets.template_panel import TemplatePanel
from utils.logger import get_log_signal


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.engine = BotEngine(config)
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        self.setWindowTitle("QQ Farm Vision Bot")
        self.setMinimumSize(960, 680)
        self.resize(1060, 740)

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

        # ── 主体：侧边栏 + 内容区 ──
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

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

        # 页面 1: 设置页
        self._settings_panel = SettingsPanel(self.config)
        self._stack.addWidget(self._settings_panel)

        # 页面 2: 模板管理页
        self._template_panel = TemplatePanel(self.engine.cv_detector)
        self._stack.addWidget(self._template_panel)

        # 页面 3: 日志页
        self._log_panel = LogPanel()
        self._stack.addWidget(self._log_panel)

        # 状态面板定时刷新（每秒）
        self._status_refresh_timer = QTimer(self)
        self._status_refresh_timer.setInterval(1000)
        self._status_refresh_timer.timeout.connect(self._refresh_status)

        content_layout.addWidget(self._stack)
        body.addWidget(content, 1)

        root.addLayout(body, 1)

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
        self._btn_test = self._make_btn("测试", Colors.PRIMARY, Colors.PRIMARY_HOVER)
        self._btn_friend = self._make_btn("巡查好友", "#7c3aed", "#6d28d9")

        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._btn_friend.setEnabled(False)

        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_test.clicked.connect(self._on_test)
        self._btn_friend.clicked.connect(self._on_friend)

        for b in (self._btn_start, self._btn_pause, self._btn_stop, self._btn_test, self._btn_friend):
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
        self.engine.screenshot_updated.connect(self._update_screenshot)
        self.engine.detection_result.connect(self._update_screenshot)
        self.engine.state_changed.connect(self._on_state_changed)
        self.engine.stats_updated.connect(self._status_panel.update_stats)
        get_log_signal().new_log.connect(self._log_panel.append_log)
        self._settings_panel.config_changed.connect(self._on_config_changed)

    # ── 导航切换 ────────────────────────────────────────────

    def _on_navigation(self, key: str):
        page_map = {"status": 0, "settings": 1, "template": 2, "logs": 3}
        idx = page_map.get(key, 0)
        self._stack.setCurrentIndex(idx)

    # ── 截图更新 ────────────────────────────────────────────

    def _update_screenshot(self, image: Image.Image):
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

    # ── 控制按钮 ────────────────────────────────────────────

    def _on_start(self):
        if self.engine.start():
            self._btn_start.setEnabled(False)
            self._btn_pause.setEnabled(True)
            self._btn_stop.setEnabled(True)
            self._btn_friend.setEnabled(True)
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
        self._btn_friend.setEnabled(False)
        self._btn_pause.setText("暂停")
        self._status_refresh_timer.stop()

    def _on_test(self):
        """测试施肥流程"""
        self.engine.test_fertilize()

    def _on_friend(self):
        """手动触发好友巡查"""
        self.engine.run_friend_once()

    def _on_state_changed(self, state: str):
        self._refresh_status()

    def _refresh_status(self):
        """定时刷新状态面板数据"""
        if self._stack.currentIndex() == 0:
            self._status_panel.update_stats(self.engine.scheduler.get_stats())

    def _on_config_changed(self, config: AppConfig):
        self.config = config
        self.engine.update_config(config)

    def showEvent(self, event):
        super().showEvent(event)

    def closeEvent(self, event):
        self.unregister_hotkeys()
        self.engine.stop()
        super().closeEvent(event)

    # ── 全局热键 ──────────────────────────────────────────

    def register_hotkeys(self):
        """注册 F9/F10 全局热键"""
        try:
            import keyboard
            keyboard.on_press_key("f9", lambda _: self._on_hotkey_pause())
            keyboard.on_press_key("f10", lambda _: self._on_hotkey_stop())
            logger.info("全局热键已注册: F9=暂停/恢复, F10=停止")
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

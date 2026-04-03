"""侧边栏导航 — Apple Settings 风格（图标 + 文字）"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon

from gui.styles import Colors


class NavItem(QPushButton):
    """侧边栏导航项：图标 + 文字"""

    def __init__(self, icon_path: str, label: str, key: str):
        super().__init__()
        self.key = key
        self._selected = False
        self._icon_path = icon_path
        self._label = label

        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        self._update_style()

        # 内部布局：图标 + 文字
        inner = QHBoxLayout(self)
        inner.setContentsMargins(12, 0, 12, 0)
        inner.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(QIcon(icon_path).pixmap(QSize(18, 18)))
        icon_lbl.setFixedSize(18, 18)
        inner.addWidget(icon_lbl)

        text_lbl = QLabel(label)
        text_lbl.setObjectName("navLabel")
        inner.addWidget(text_lbl)
        inner.addStretch()

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.SIDEBAR_ITEM_SELECTED_BG};
                    border: none;
                    border-radius: 6px;
                    padding: 0;
                    text-align: left;
                }}
                QPushButton:hover {{
                    background-color: rgba(0, 122, 255, 18);
                }}
                QLabel {{ color: {Colors.SIDEBAR_ITEM_SELECTED}; background: transparent; border: none; }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    border-radius: 6px;
                    padding: 0;
                    text-align: left;
                }}
                QPushButton:hover {{
                    background-color: {Colors.SIDEBAR_ITEM_HOVER};
                }}
                QLabel {{ color: {Colors.TEXT_SECONDARY}; background: transparent; border: none; }}
            """)


class Sidebar(QWidget):
    """侧边栏导航 — macOS Settings 风格"""
    navigation_changed = pyqtSignal(str)

    PAGE_DEF = [
        ("gui/icons/nav_status.svg", "状态总览", "status"),
        ("gui/icons/nav_settings.svg", "参数设置", "settings"),
        ("gui/icons/nav_sell.svg", "出售管理", "sell"),
        ("gui/icons/nav_template.svg", "模板管理", "template"),
        ("gui/icons/nav_logs.svg", "运行日志", "logs"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: list[NavItem] = []
        self.setFixedWidth(170)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {Colors.SIDEBAR_BG};
                border-right: 1px solid {Colors.BORDER};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 20, 8, 8)
        layout.setSpacing(2)

        for icon_path, label, key in self.PAGE_DEF:
            btn = NavItem(icon_path, label, key)
            btn.clicked.connect(lambda checked, k=key: self._on_nav(k))
            layout.addWidget(btn)
            self._buttons.append(btn)

        layout.addStretch()
        self._buttons[0].set_selected(True)

    def _on_nav(self, key: str):
        for btn in self._buttons:
            btn.set_selected(btn.key == key)
        self.navigation_changed.emit(key)

    def set_current(self, key: str):
        for btn in self._buttons:
            btn.set_selected(btn.key == key)

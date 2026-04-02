"""自定义标题栏 — 浅色毛玻璃风格"""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QIcon

from gui.styles import Colors


class TitleBar(QWidget):
    """自定义标题栏，支持拖拽移动和双击最大化"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_window = parent
        self._press_pos = QPoint()
        self._is_dragging = False
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {Colors.TITLEBAR_BG};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("QQ Farm Vision Bot")
        title.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT};
                font-size: 12px;
                font-weight: bold;
                background: transparent;
                border: none;
            }}
        """)
        layout.addWidget(title)
        layout.addStretch()

        btn_style = f"""
            QPushButton {{
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
                width: 46px; height: 36px;
                border-radius: 0px;
            }}
            QPushButton:hover {{
                background: rgba(0, 0, 0, 15);
            }}
        """
        close_hover = f"""
            QPushButton {{
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
                width: 46px; height: 36px;
                border-radius: 0px;
            }}
            QPushButton:hover {{
                background: {Colors.DANGER};
            }}
        """

        self._btn_min = QPushButton()
        self._btn_min.setIcon(QIcon("gui/icons/title_minimize.svg"))
        self._btn_min.setStyleSheet(btn_style)
        self._btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_min.clicked.connect(self._on_minimize)

        self._btn_max = QPushButton()
        self._btn_max.setIcon(QIcon("gui/icons/title_maximize.svg"))
        self._btn_max.setStyleSheet(btn_style)
        self._btn_max.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_max.clicked.connect(self._on_toggle_maximize)

        self._btn_close = QPushButton()
        self._btn_close.setIcon(QIcon("gui/icons/title_close.svg"))
        self._btn_close.setStyleSheet(close_hover)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.clicked.connect(self._on_close)

        layout.addWidget(self._btn_min)
        layout.addWidget(self._btn_max)
        layout.addWidget(self._btn_close)

    def _on_minimize(self):
        if self._parent_window:
            self._parent_window.showMinimized()

    def _on_toggle_maximize(self):
        if not self._parent_window:
            return
        if self._parent_window.isMaximized():
            self._parent_window.showNormal()
            self._btn_max.setIcon(QIcon("gui/icons/title_maximize.svg"))
        else:
            self._parent_window.showMaximized()
            self._btn_max.setIcon(QIcon("gui/icons/title_restore.svg"))

    def _on_close(self):
        if self._parent_window:
            self._parent_window.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint() - (
                self._parent_window.pos() if self._parent_window else QPoint(0, 0)
            )
            self._is_dragging = True

    def mouseMoveEvent(self, event):
        if self._is_dragging and self._parent_window:
            if self._parent_window.isMaximized():
                self._parent_window.showNormal()
                self._btn_max.setIcon(QIcon("gui/icons/title_maximize.svg"))
            new_pos = event.globalPosition().toPoint() - self._press_pos
            self._parent_window.move(new_pos)

    def mouseReleaseEvent(self, event):
        self._is_dragging = False

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_toggle_maximize()

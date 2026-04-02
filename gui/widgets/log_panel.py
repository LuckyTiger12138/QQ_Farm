"""日志面板 — 浅色终端风格"""
from PyQt6.QtWidgets import QTextEdit, QWidget, QHBoxLayout, QPushButton, QVBoxLayout, QApplication
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor

from gui.styles import Colors, ghost_button_style


class LogPanel(QWidget):
    MAX_LINES = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 工具栏 ──
        toolbar = QWidget()
        toolbar.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(0, 0, 0, 8);
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }}
        """)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 6, 12, 6)
        toolbar_layout.setSpacing(4)
        toolbar_layout.addStretch()

        self._btn_copy = QPushButton("复制")
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy.setStyleSheet(ghost_button_style())
        self._btn_copy.clicked.connect(self._copy_log)
        toolbar_layout.addWidget(self._btn_copy)

        self._btn_select_all = QPushButton("全选")
        self._btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_select_all.setStyleSheet(ghost_button_style())
        self._btn_select_all.clicked.connect(self._select_all)
        toolbar_layout.addWidget(self._btn_select_all)

        self._btn_clear = QPushButton("清空")
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid transparent;
                color: {Colors.DANGER}; padding: 4px 12px;
                font-size: 12px; border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: rgba(220, 38, 38, 15);
                border-color: rgba(220, 38, 38, 30);
            }}
        """)
        self._btn_clear.clicked.connect(self._clear_log)
        toolbar_layout.addWidget(self._btn_clear)

        layout.addWidget(toolbar)

        # ── 日志文本框 ──
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: #f8fafc;
                color: {Colors.TEXT};
                font-family: 'Cascadia Code', 'Consolas', monospace;
                font-size: 12px;
                border: none;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
                padding: 12px;
            }}
        """)
        layout.addWidget(self._log_text, 1)

    def _copy_log(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self._log_text.toPlainText())
        original_text = self._btn_copy.text()
        self._btn_copy.setText("已复制!")
        QTimer.singleShot(1500, lambda: self._btn_copy.setText(original_text))

    def _select_all(self):
        self._log_text.selectAll()

    def _clear_log(self):
        self._log_text.clear()
        self.append_log("日志已清空")

    def append_log(self, message: str):
        if "ERROR" in message or "✗" in message:
            color = Colors.DANGER
        elif "WARNING" in message:
            color = Colors.WARNING
        elif "✓" in message:
            color = Colors.SUCCESS
        elif "INFO" in message:
            color = Colors.PRIMARY
        else:
            color = Colors.TEXT_SECONDARY

        self._log_text.append(f'<span style="color:{color}">{message}</span>')

        if self._log_text.document().blockCount() > self.MAX_LINES:
            cursor = self._log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(QTextCursor.MoveOperation.Down,
                                QTextCursor.MoveMode.KeepAnchor, 50)
            cursor.removeSelectedText()

        self._log_text.verticalScrollBar().setValue(self._log_text.verticalScrollBar().maximum())

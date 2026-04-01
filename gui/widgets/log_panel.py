"""日志面板 - 深色终端风格"""
from PyQt6.QtWidgets import QTextEdit, QWidget, QHBoxLayout, QPushButton, QVBoxLayout, QApplication
from PyQt6.QtGui import QTextCursor
from PyQt6.QtCore import Qt


class LogPanel(QWidget):
    MAX_LINES = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 工具栏
        toolbar = QWidget()
        toolbar.setStyleSheet("""
            QWidget { background-color: #f1f5f9; border-top-left-radius: 8px; border-top-right-radius: 8px; }
            QPushButton {
                background-color: transparent; border: none; color: #64748b;
                padding: 4px 10px; font-size: 12px; border-radius: 4px; margin: 2px;
            }
            QPushButton:hover { background-color: #e2e8f0; color: #1e293b; }
        """)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)
        toolbar_layout.setSpacing(4)
        toolbar_layout.addStretch()

        self._btn_copy = QPushButton("复制")
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy.clicked.connect(self._copy_log)
        toolbar_layout.addWidget(self._btn_copy)

        self._btn_select_all = QPushButton("全选")
        self._btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_select_all.clicked.connect(self._select_all)
        toolbar_layout.addWidget(self._btn_select_all)

        self._btn_clear = QPushButton("清空")
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.setStyleSheet("""
            QPushButton { color: #dc2626; }
            QPushButton:hover { background-color: #fee2e2; color: #b91c1c; }
        """)
        self._btn_clear.clicked.connect(self._clear_log)
        toolbar_layout.addWidget(self._btn_clear)

        # 日志文本框
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet("""
            QTextEdit {
                background-color: #f8fafc; color: #1e293b;
                font-family: 'Cascadia Code', 'Consolas', monospace;
                font-size: 12px; border: none; padding: 8px;
                border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;
            }
        """)
        layout.addWidget(toolbar)
        layout.addWidget(self._log_text, 1)

    def _copy_log(self):
        """复制所有日志到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(self._log_text.toPlainText())

    def _select_all(self):
        """全选日志"""
        self._log_text.selectAll()

    def _clear_log(self):
        """清空日志"""
        self._log_text.clear()

    def append_log(self, message: str):
        if "ERROR" in message or "✗" in message:
            color = "#dc2626"
        elif "WARNING" in message:
            color = "#d97706"
        elif "✓" in message:
            color = "#16a34a"
        elif "INFO" in message:
            color = "#2563eb"
        else:
            color = "#64748b"

        self._log_text.append(f'<span style="color:{color}">{message}</span>')

        if self._log_text.document().blockCount() > self.MAX_LINES:
            cursor = self._log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(QTextCursor.MoveOperation.Down,
                                QTextCursor.MoveMode.KeepAnchor, 50)
            cursor.removeSelectedText()

        self._log_text.verticalScrollBar().setValue(self._log_text.verticalScrollBar().maximum())

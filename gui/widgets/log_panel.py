"""日志面板 — 现代终端风格"""
from PyQt6.QtWidgets import QTextEdit, QWidget, QHBoxLayout, QPushButton, QVBoxLayout, QApplication, QLabel, QFrame
from PyQt6.QtCore import Qt, QTimer
from dataclasses import dataclass

from gui.styles import Colors


@dataclass
class LogEntry:
    message: str
    color: str
    level: str


class LogBadge(QLabel):
    def __init__(self, text: str, bg: str, fg: str, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(f"""
            background-color: {bg};
            color: {fg};
            border-radius: 10px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
        """)


class FilterButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._active = False
        self._active_bg = ""
        self._active_fg = ""
        self._inactive_bg = "rgba(0, 0, 0, 6)"
        self._inactive_fg = Colors.TEXT_SECONDARY
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {self._inactive_bg};
                color: {self._inactive_fg};
                border: none;
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: rgba(0, 0, 0, 12);
            }}
        """)

    def set_active_style(self, bg: str, fg: str):
        self._active_bg = bg
        self._active_fg = fg
        self._update_style()

    def set_active(self, active: bool):
        self._active = active
        self._update_style()

    def _update_style(self):
        if self._active:
            bg = self._active_bg
            fg = self._active_fg
        else:
            bg = self._inactive_bg
            fg = self._inactive_fg
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: none;
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {fg.replace(')', ', 20)').replace('rgb', 'rgba') if fg.startswith('rgb') else bg};
            }}
        """)


class LogPanel(QWidget):
    MAX_LINES = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts = {"INFO": 0, "WARNING": 0, "ERROR": 0, "OTHER": 0}
        self._active_filters = {"INFO", "WARNING", "ERROR", "OTHER"}
        self._entries: list[LogEntry] = []
        self._init_ui()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 14px 14px 0 0;
            }}
        """)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 10)
        header_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)

        title = QLabel("运行日志")
        title.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 18px; font-weight: 700;
            background: transparent; border: none;
        """)
        title_row.addWidget(title)

        self._count_badge = LogBadge("0 条", "rgba(0, 122, 255, 12)", Colors.PRIMARY)
        title_row.addWidget(self._count_badge)
        title_row.addStretch()

        self._btn_copy = QPushButton("复制")
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(0, 0, 0, 6);
                color: {Colors.TEXT_SECONDARY};
                border: none;
                border-radius: 8px;
                padding: 5px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: rgba(0, 0, 0, 12);
                color: {Colors.TEXT};
            }}
        """)
        self._btn_copy.clicked.connect(self._copy_log)
        title_row.addWidget(self._btn_copy)

        self._btn_clear = QPushButton("清空")
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 59, 48, 10);
                color: {Colors.DANGER};
                border: none;
                border-radius: 8px;
                padding: 5px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 59, 48, 20);
            }}
        """)
        self._btn_clear.clicked.connect(self._clear_log)
        title_row.addWidget(self._btn_clear)

        header_layout.addLayout(title_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self._filter_all = FilterButton("全部")
        self._filter_all.set_active_style("rgba(0, 122, 255, 15)", Colors.PRIMARY)
        self._filter_all.set_active(True)
        self._filter_all.clicked.connect(lambda: self._toggle_filter("ALL"))

        self._filter_info = FilterButton("INFO")
        self._filter_info.set_active_style("rgba(0, 122, 255, 15)", Colors.PRIMARY)
        self._filter_info.set_active(True)
        self._filter_info.clicked.connect(lambda: self._toggle_filter("INFO"))

        self._filter_warn = FilterButton("WARN")
        self._filter_warn.set_active_style("rgba(255, 149, 0, 15)", Colors.WARNING)
        self._filter_warn.set_active(True)
        self._filter_warn.clicked.connect(lambda: self._toggle_filter("WARNING"))

        self._filter_error = FilterButton("ERROR")
        self._filter_error.set_active_style("rgba(255, 59, 48, 15)", Colors.DANGER)
        self._filter_error.set_active(True)
        self._filter_error.clicked.connect(lambda: self._toggle_filter("ERROR"))

        filter_row.addWidget(self._filter_all)
        filter_row.addWidget(self._filter_info)
        filter_row.addWidget(self._filter_warn)
        filter_row.addWidget(self._filter_error)
        filter_row.addStretch()

        header_layout.addLayout(filter_row)

        outer.addWidget(header)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: #1e1e2e;
                color: #cdd6f4;
                font-family: 'Cascadia Code', 'Consolas', 'JetBrains Mono', monospace;
                font-size: 12px;
                border: 1px solid {Colors.BORDER};
                border-top: none;
                border-radius: 0 0 14px 14px;
                padding: 12px 14px;
                line-height: 1.6;
            }}
            QScrollBar:vertical {{
                background: transparent; width: 8px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 20); border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255, 255, 255, 35);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)
        outer.addWidget(self._log_text, 1)

    def _toggle_filter(self, level: str):
        if level == "ALL":
            all_active = all(f in self._active_filters for f in ("INFO", "WARNING", "ERROR"))
            if all_active:
                self._active_filters.clear()
                self._filter_all.set_active(False)
                self._filter_info.set_active(False)
                self._filter_warn.set_active(False)
                self._filter_error.set_active(False)
            else:
                self._active_filters = {"INFO", "WARNING", "ERROR", "OTHER"}
                self._filter_all.set_active(True)
                self._filter_info.set_active(True)
                self._filter_warn.set_active(True)
                self._filter_error.set_active(True)
        else:
            if level in self._active_filters:
                self._active_filters.discard(level)
            else:
                self._active_filters.add(level)

            btn_map = {"INFO": self._filter_info, "WARNING": self._filter_warn, "ERROR": self._filter_error}
            if level in btn_map:
                btn_map[level].set_active(level in self._active_filters)

            all_active = all(f in self._active_filters for f in ("INFO", "WARNING", "ERROR"))
            self._filter_all.set_active(all_active)

        self._rebuild_display()

    def _rebuild_display(self):
        self._log_text.clear()
        was_at_bottom = self._log_text.verticalScrollBar().value() == self._log_text.verticalScrollBar().maximum()
        for entry in self._entries:
            if entry.level in self._active_filters:
                self._log_text.append(f'<span style="color:{entry.color}">{entry.message}</span>')
        if was_at_bottom:
            self._log_text.verticalScrollBar().setValue(self._log_text.verticalScrollBar().maximum())

    def _copy_log(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self._log_text.toPlainText())
        original_text = self._btn_copy.text()
        self._btn_copy.setText("已复制!")
        QTimer.singleShot(1500, lambda: self._btn_copy.setText(original_text))

    def _clear_log(self):
        self._log_text.clear()
        self._entries.clear()
        self._counts = {"INFO": 0, "WARNING": 0, "ERROR": 0, "OTHER": 0}
        self._update_badge()
        self.append_log("日志已清空")

    def _update_badge(self):
        total = sum(self._counts.values())
        self._count_badge.setText(f"{total} 条")

    def _classify(self, message: str) -> tuple[str, str]:
        if "ERROR" in message or "✗" in message:
            return "#f38ba8", "ERROR"
        elif "WARNING" in message or "⚠" in message:
            return "#fab387", "WARNING"
        elif "✓" in message or "成功" in message:
            return "#a6e3a1", "OTHER"
        elif "INFO" in message:
            return "#89b4fa", "INFO"
        else:
            return "#a6adc8", "OTHER"

    def append_log(self, message: str):
        color, level = self._classify(message)
        self._counts[level] = self._counts.get(level, 0) + 1
        self._update_badge()

        self._entries.append(LogEntry(message=message, color=color, level=level))

        if len(self._entries) > self.MAX_LINES:
            self._entries = self._entries[-self.MAX_LINES:]

        if level in self._active_filters:
            was_at_bottom = self._log_text.verticalScrollBar().value() == self._log_text.verticalScrollBar().maximum()
            self._log_text.append(f'<span style="color:{color}">{message}</span>')
            if was_at_bottom:
                self._log_text.verticalScrollBar().setValue(self._log_text.verticalScrollBar().maximum())

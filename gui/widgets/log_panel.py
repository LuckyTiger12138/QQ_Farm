"""Fluent 日志面板 — 深色主题自适应。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import isDarkTheme, qconfig


class LogPanel(QWidget):
    """运行日志窗口，带过滤/复制/清空。"""

    MAX_LINES = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_filters = {"INFO", "WARNING", "ERROR", "OTHER"}
        self._counts = {"INFO": 0, "WARNING": 0, "ERROR": 0, "OTHER": 0}
        self._entries: list[tuple[str, str, str]] = []  # (message, color, level)
        self._init_ui()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 顶部工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 4, 4, 4)
        toolbar.setSpacing(6)

        self._count_badge = QLabel("0 条")
        self._count_badge.setStyleSheet("font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 10px;")
        self._update_badge_style()
        toolbar.addWidget(self._count_badge)

        self._filter_btns: dict[str, QPushButton] = {}
        for key, label in [("ALL", "全部"), ("INFO", "INFO"), ("WARNING", "WARN"), ("ERROR", "ERROR")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._toggle_filter(k))
            self._filter_btns[key] = btn
            toolbar.addWidget(btn)

        toolbar.addStretch()

        self._btn_copy = QPushButton("复制")
        self._btn_copy.setFixedHeight(26)
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy.clicked.connect(self._copy_log)
        toolbar.addWidget(self._btn_copy)

        self._btn_clear = QPushButton("清空")
        self._btn_clear.setFixedHeight(26)
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.clicked.connect(self._clear_log)
        toolbar.addWidget(self._btn_clear)

        outer.addLayout(toolbar)

        # ── 日志文本区 ──
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setUndoRedoEnabled(False)
        self._log_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._log_text.document().setMaximumBlockCount(self.MAX_LINES)
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(8)
        self._log_text.setFont(font)
        self._log_text.setPlaceholderText("运行后显示日志...")
        outer.addWidget(self._log_text, 1)

        self._apply_style()
        qconfig.themeChangedFinished.connect(self._apply_style)

    def _apply_style(self, *_args):
        dark = isDarkTheme()
        text = "#e5e7eb" if dark else "#1e293b"
        border = "rgba(255,255,255,0.16)" if dark else "rgba(15,23,42,0.12)"
        bg = "rgba(18,18,20,0.92)" if dark else "#f8fafc"
        selection = "rgba(59,130,246,0.45)" if dark else "rgba(59,130,246,0.24)"
        sb = "rgba(148,163,184,0.46)" if dark else "rgba(100,116,139,0.36)"
        sb_hover = "rgba(148,163,184,0.68)" if dark else "rgba(100,116,139,0.58)"

        btn_bg = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.05)"
        btn_checked = "rgba(0,122,255,0.18)" if dark else "rgba(0,122,255,0.12)"
        btn_fg = text

        self._log_text.setStyleSheet(f"""
            QTextEdit {{
                color: {text}; background-color: {bg}; border: 1px solid {border};
                border-radius: 10px; padding: 8px 10px;
                selection-background-color: {selection};
            }}
            QScrollBar:vertical {{ width: 10px; background: transparent; margin: 4px 2px; }}
            QScrollBar::handle:vertical {{ min-height: 24px; border-radius: 4px; background: {sb}; }}
            QScrollBar::handle:vertical:hover {{ background: {sb_hover}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        for key, btn in self._filter_btns.items():
            checked = btn.isChecked()
            bg_c = btn_checked if checked else btn_bg
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg_c}; color: {btn_fg}; border: 1px solid {border};
                    border-radius: 12px; padding: 2px 10px; font-size: 11px; font-weight: 600;
                }}
                QPushButton:hover {{ background-color: {btn_checked}; }}
            """)

        self._update_badge_style()
        self._btn_copy.setStyleSheet(f"""
            QPushButton {{ background-color: {btn_bg}; color: {btn_fg}; border: 1px solid {border};
                border-radius: 8px; padding: 2px 12px; font-size: 12px; }}
            QPushButton:hover {{ background-color: {btn_checked}; }}
        """)
        self._btn_clear.setStyleSheet(f"""
            QPushButton {{ background-color: rgba(255,59,48,0.08); color: {"#f87171" if dark else "#dc2626"};
                border: 1px solid {border}; border-radius: 8px; padding: 2px 12px; font-size: 12px; }}
            QPushButton:hover {{ background-color: rgba(255,59,48,0.16); }}
        """)

    def _update_badge_style(self):
        dark = isDarkTheme()
        bg = "rgba(0,122,255,0.15)" if dark else "rgba(0,122,255,0.12)"
        fg = "#60a5fa" if dark else "#007AFF"
        self._count_badge.setStyleSheet(
            f"color: {fg}; background-color: {bg}; font-size: 12px; font-weight: 600; "
            f"padding: 2px 8px; border-radius: 10px;"
        )

    @staticmethod
    def _resolve_level_color(message: str) -> tuple[str, str]:
        raw = str(message or "")
        upper = raw.upper()
        if "ERROR" in upper or "CRITICAL" in upper or "✗" in raw:
            return ("#f87171", "ERROR") if isDarkTheme() else ("#dc2626", "ERROR")
        if "WARNING" in upper or "WARN" in upper:
            return ("#fbbf24", "WARNING") if isDarkTheme() else ("#d97706", "WARNING")
        if "SUCCESS" in upper or "✓" in raw:
            return ("#4ade80", "OTHER") if isDarkTheme() else ("#16a34a", "OTHER")
        if "INFO" in upper:
            return ("#60a5fa", "INFO") if isDarkTheme() else ("#2563eb", "INFO")
        return ("#cbd5e1", "OTHER") if isDarkTheme() else ("#475569", "OTHER")

    def _toggle_filter(self, key: str):
        if key == "ALL":
            all_on = all(f in self._active_filters for f in ("INFO", "WARNING", "ERROR"))
            if all_on:
                self._active_filters.clear()
                for b in self._filter_btns.values():
                    b.setChecked(False)
            else:
                self._active_filters = {"INFO", "WARNING", "ERROR", "OTHER"}
                for b in self._filter_btns.values():
                    b.setChecked(True)
        else:
            if key in self._active_filters:
                self._active_filters.discard(key)
            else:
                self._active_filters.add(key)
            all_on = all(f in self._active_filters for f in ("INFO", "WARNING", "ERROR"))
            self._filter_btns["ALL"].setChecked(all_on)

        self._apply_style()
        self._rebuild_display()

    def _rebuild_display(self):
        self._log_text.clear()
        for msg, color, level in self._entries:
            if level in self._active_filters:
                self._append_colored(msg, color)

    def _append_colored(self, text: str, color: str):
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(text, fmt)
        cursor.insertBlock()
        self._log_text.setTextCursor(cursor)
        self._log_text.verticalScrollBar().setValue(self._log_text.verticalScrollBar().maximum())

    def _copy_log(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self._log_text.toPlainText())
        orig = self._btn_copy.text()
        self._btn_copy.setText("已复制!")
        QTimer.singleShot(1500, lambda: self._btn_copy.setText(orig))

    def _clear_log(self):
        self._log_text.clear()
        self._entries.clear()
        self._counts = {"INFO": 0, "WARNING": 0, "ERROR": 0, "OTHER": 0}
        self._update_badge()
        self.append_log("日志已清空")

    def _update_badge(self):
        total = sum(self._counts.values())
        self._count_badge.setText(f"{total} 条")

    def append_log(self, message: str):
        text = str(message or "").rstrip()
        if not text:
            return
        color, level = self._resolve_level_color(text)
        self._counts[level] = self._counts.get(level, 0) + 1
        self._update_badge()

        self._entries.append((text, color, level))
        if len(self._entries) > self.MAX_LINES:
            self._entries = self._entries[-self.MAX_LINES:]

        if level in self._active_filters:
            self._append_colored(text, color)

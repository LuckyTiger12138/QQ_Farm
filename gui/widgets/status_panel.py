"""状态面板 — 现代卡片式统计仪表盘"""
from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel, QHBoxLayout, QVBoxLayout, QFrame
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QLinearGradient, QColor

from gui.styles import Colors


class GradientCard(QFrame):
    def __init__(self, gradient_start: str, gradient_end: str, parent=None):
        super().__init__(parent)
        self._gradient_start = gradient_start
        self._gradient_end = gradient_end
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._update_style()

    def _update_style(self):
        self.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {self._gradient_start}, stop:1 {self._gradient_end});
                border: none;
                border-radius: 16px;
                padding: 16px;
            }}
        """)


class StatBadge(QFrame):
    def __init__(self, icon: str, title: str, value: str, value_color: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        top_row.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 12px; background: transparent; border: none;")
        top_row.addWidget(title_lbl)
        top_row.addStretch()

        layout.addLayout(top_row)

        self._value_lbl = QLabel(value)
        self._value_lbl.setStyleSheet(f"""
            color: {value_color}; font-size: 22px; font-weight: 700;
            background: transparent; border: none;
        """)
        layout.addWidget(self._value_lbl)

    def set_value(self, text: str):
        self._value_lbl.setText(text)


class MiniStatCard(QFrame):
    def __init__(self, icon: str, title: str, value: str, accent: str, parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 14px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        icon_row = QHBoxLayout()
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet(f"font-size: 18px; background: transparent; border: none;")
        icon_row.addWidget(icon_lbl)
        icon_row.addStretch()

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._dot.setStyleSheet(f"""
            background-color: {accent};
            border-radius: 4px;
            border: none;
        """)
        icon_row.addWidget(self._dot)
        layout.addLayout(icon_row)

        self._value_lbl = QLabel(value)
        self._value_lbl.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 24px; font-weight: 700;
            background: transparent; border: none;
        """)
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._value_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)


class StatusPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels = {}
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(800)
        self._pulse_opacity = 1.0
        self._pulse_direction = -1
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._init_ui()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        # ── 顶部标题 ──
        header = QHBoxLayout()
        title = QLabel("状态总览")
        title.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 20px; font-weight: 700;
            background: transparent; border: none;
        """)
        header.addWidget(title)
        header.addStretch()
        outer.addLayout(header)

        # ── 3 张大卡片 ──
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        self._state_card = GradientCard("#007AFF", "#5856D6")
        state_layout = QVBoxLayout(self._state_card)
        state_layout.setContentsMargins(20, 18, 20, 18)
        state_layout.setSpacing(8)

        state_header = QHBoxLayout()
        state_icon = QLabel("⚡")
        state_icon.setStyleSheet("font-size: 22px; background: transparent; border: none;")
        state_header.addWidget(state_icon)
        state_header.addStretch()
        self._state_badge = QLabel("未启动")
        self._state_badge.setStyleSheet("""
            color: rgba(255,255,255,0.7); font-size: 12px;
            background: rgba(255,255,255,0.15); border: none;
            border-radius: 10px; padding: 3px 10px;
        """)
        state_header.addWidget(self._state_badge)
        state_layout.addLayout(state_header)

        self._state_value = QLabel("● 未启动")
        self._state_value.setStyleSheet("""
            color: #FFFFFF; font-size: 26px; font-weight: 700;
            background: transparent; border: none;
        """)
        state_layout.addWidget(self._state_value)

        top_row.addWidget(self._state_card, 1)

        self._time_card = StatBadge("⏱", "已运行", "--", Colors.TEXT)
        top_row.addWidget(self._time_card, 1)

        self._next_card = StatBadge("🕐", "下次检查", "--", Colors.TEXT)
        top_row.addWidget(self._next_card, 1)

        outer.addLayout(top_row)

        # ── 操作统计：6 个彩色卡片 ──
        grid_label = QLabel("操作统计")
        grid_label.setStyleSheet(f"""
            color: {Colors.TEXT_SECONDARY}; font-size: 13px; font-weight: 600;
            background: transparent; border: none;
        """)
        outer.addWidget(grid_label)

        grid = QGridLayout()
        grid.setSpacing(10)

        stats = [
            ("🌾", "收获", "0", Colors.SUCCESS),
            ("🌱", "播种", "0", "#007AFF"),
            ("💧", "浇水", "0", "#5AC8FA"),
            ("🌿", "除草", "0", "#34C759"),
            ("🐛", "除虫", "0", Colors.WARNING),
            ("💊", "施肥", "0", "#AF52DE"),
        ]
        self._mini_cards = {}
        for i, (icon, label_text, val, accent) in enumerate(stats):
            card = MiniStatCard(icon, label_text, val, accent)
            self._mini_cards[label_text] = card
            grid.addWidget(card, i // 3, i % 3)

        outer.addLayout(grid)
        outer.addStretch()

        self._labels["elapsed"] = self._time_card._value_lbl
        self._labels["next_farm"] = self._next_card._value_lbl

    def _pulse_tick(self):
        self._pulse_opacity += self._pulse_direction * 0.12
        if self._pulse_opacity <= 0.5:
            self._pulse_direction = 1
        elif self._pulse_opacity >= 1.0:
            self._pulse_direction = -1
        self._state_value.setStyleSheet(f"""
            color: rgba(255, 255, 255, {self._pulse_opacity:.2f});
            font-size: 26px; font-weight: 700;
            background: transparent; border: none;
        """)

    def update_stats(self, stats: dict):
        state = stats.get("state", "idle")
        state_map = {
            "idle":    ("未启动", "● 未启动", False),
            "running": ("运行中", "● 运行中", True),
            "paused":  ("已暂停", "● 已暂停", False),
            "error":   ("异常",   "● 异常", False),
        }
        badge_text, value_text, pulse = state_map.get(state, ("运行中", "● 运行中", True))

        self._state_badge.setText(badge_text)
        self._state_value.setText(value_text)

        if pulse:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._state_value.setStyleSheet("""
                color: #FFFFFF; font-size: 26px; font-weight: 700;
                background: transparent; border: none;
            """)

        self._labels["elapsed"].setText(stats.get("elapsed", "--"))
        self._labels["next_farm"].setText(stats.get("next_farm_check", "--"))

        count_map = {
            "收获": "harvest", "播种": "plant", "浇水": "water",
            "除草": "weed", "除虫": "bug", "施肥": "fertilize",
        }
        for cn_label, key in count_map.items():
            if cn_label in self._mini_cards:
                self._mini_cards[cn_label]._value_lbl.setText(str(stats.get(key, 0)))

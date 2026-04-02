"""状态面板 — 浅色毛玻璃统计卡片"""
from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel, QHBoxLayout, QVBoxLayout, QFrame
from PyQt6.QtCore import Qt, QTimer

from gui.styles import Colors


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
        outer.setSpacing(10)

        # ── 状态行：3 张大卡片 ──
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self._state_card = self._make_stat_card("状态", "● 未启动", "state")
        self._time_card = self._make_stat_card("已运行", "--", "elapsed")
        self._next_card = self._make_stat_card("下次检查", "--", "next_farm")

        top_row.addWidget(self._state_card)
        top_row.addWidget(self._time_card)
        top_row.addWidget(self._next_card)
        outer.addLayout(top_row)

        # ── 操作统计：6 个小卡片 ──
        grid_row = QHBoxLayout()
        grid_row.setSpacing(8)

        stats = [
            ("收获", "harvest"), ("播种", "plant"), ("浇水", "water"),
            ("除草", "weed"), ("除虫", "bug"), ("施肥", "fertilize"),
        ]
        for label_text, key in stats:
            mini = self._make_mini_stat(label_text, "0", key)
            grid_row.addWidget(mini)

        outer.addLayout(grid_row)
        outer.addStretch()

    def _make_stat_card(self, title: str, default: str, key: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                padding: 12px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
        layout.addWidget(lbl)

        val = QLabel(default)
        val.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 16px; font-weight: bold;
            background: transparent; border: none;
        """)
        layout.addWidget(val)

        self._labels[key] = val
        return card

    def _make_mini_stat(self, title: str, default: str, key: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 6px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 10px; background: transparent; border: none;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        val = QLabel(default)
        val.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 14px; font-weight: bold;
            background: transparent; border: none;
        """)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(val)

        self._labels[key] = val
        return card

    def _pulse_tick(self):
        self._pulse_opacity += self._pulse_direction * 0.15
        if self._pulse_opacity <= 0.4:
            self._pulse_direction = 1
        elif self._pulse_opacity >= 1.0:
            self._pulse_direction = -1
        self._labels["state"].setStyleSheet(
            f"color: rgba(22, 163, 74, {self._pulse_opacity:.1f});"
            f" font-size: 16px; font-weight: bold; background: transparent; border: none;"
        )

    def update_stats(self, stats: dict):
        state = stats.get("state", "idle")
        state_map = {
            "idle":    ("● 未启动", Colors.TEXT_DIM, False),
            "running": ("● 运行中", Colors.SUCCESS, True),
            "paused":  ("● 已暂停", Colors.WARNING, False),
            "error":   ("● 异常",   Colors.DANGER, False),
        }
        text, color, pulse = state_map.get(state, ("● 运行中", Colors.SUCCESS, True))

        self._labels["state"].setText(text)
        if pulse:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._labels["state"].setStyleSheet(
                f"color: {color}; font-size: 16px; font-weight: bold;"
                f" background: transparent; border: none;"
            )

        self._labels["elapsed"].setText(stats.get("elapsed", "--"))
        self._labels["next_farm"].setText(stats.get("next_farm_check", "--"))
        for key in ("harvest", "plant", "water", "weed", "bug", "fertilize"):
            self._labels[key].setText(str(stats.get(key, 0)))

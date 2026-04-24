"""好友偷取统计图表面板 — 纯 PyQt6 实现"""
from __future__ import annotations

from datetime import date, timedelta

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.styles import Colors
from gui.widgets.fluent_container import ElevatedCardWidget
from utils.steal_stats import load_stats


def _format_count(value: int) -> str:
    if value >= 100_000_000:
        text = f'{value / 100_000_000:.2f}'.rstrip('0').rstrip('.')
        return f'{text}亿'
    if value >= 10_000:
        text = f'{value / 10_000:.2f}'.rstrip('0').rstrip('.')
        return f'{text}万'
    return str(value)


def _format_date_label(date_text: str) -> str:
    return date_text[5:] if len(date_text) >= 10 else date_text


class _BarChart(QWidget):
    """自定义柱状图绘制组件"""

    def __init__(self, *, bar_color: str, parent=None):
        super().__init__(parent)
        self._bar_color = QColor(bar_color)
        self._data: list[tuple[str, int]] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(140)

    def set_data(self, data: list[tuple[str, int]]):
        self._data = data
        self.update()

    def paintEvent(self, event):
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pad_l, pad_r, pad_t, pad_b = 56, 16, 16, 52
        w = self.width() - pad_l - pad_r
        h = self.height() - pad_t - pad_b
        n = len(self._data)
        max_val = max(v for _, v in self._data) or 1

        fg = QColor(Colors.TEXT)
        grid_c = QColor(Colors.BORDER)
        bar_c = self._bar_color

        font = QFont()
        font.setPointSize(8)
        p.setFont(font)

        # 绘制网格线和Y轴标签
        for i in range(5):
            y = pad_t + h - i * h // 4
            p.setPen(QPen(grid_c, 1, Qt.PenStyle.DashLine))
            p.drawLine(pad_l, y, pad_l + w, y)
            p.setPen(fg)
            p.drawText(
                QRectF(0, y - 10, pad_l - 4, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _format_count(int(max_val * i / 4)),
            )

        # 绘制柱子
        bar_w = max(4, w // n - 4)
        for i, (d, v) in enumerate(self._data):
            bh = int(v / max_val * h)
            x = pad_l + i * w // n + (w // n - bar_w) // 2
            y = pad_t + h - bh
            p.setBrush(bar_c)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x, y, bar_w, bh), 3, 3)

        # 绘制X轴日期标签
        sample_step = max(1, n // 6)
        for i, (d, _) in enumerate(self._data):
            if i != 0 and i != n - 1 and i % sample_step != 0:
                continue
            x = pad_l + i * w // n + w // n // 2
            p.setPen(grid_c)
            p.drawLine(x, pad_t + h, x, pad_t + h + 4)
            p.setPen(fg)
            p.drawText(
                QRectF(x - 22, pad_t + h + 6, 44, 20),
                Qt.AlignmentFlag.AlignHCenter,
                _format_date_label(d),
            )
        p.end()

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta and self.parent():
            panel = self.parent()
            while panel and not isinstance(panel, StealChartPanel):
                panel = panel.parent()
            if panel:
                panel.adjust_window(1 if delta > 0 else -1)
                event.accept()
                return
        super().wheelEvent(event)


class StealChartPanel(QWidget):
    """偷取统计图表标签页"""

    _MIN_DAY_WINDOW = 1
    _MAX_DAY_WINDOW = 120
    _MIN_WEEK_WINDOW = 1
    _MAX_WEEK_WINDOW = 52

    def __init__(self, instance_id: str = 'default', parent=None):
        super().__init__(parent)
        self._instance_id = instance_id
        self._day_window = 15
        self._week_window = 8
        self._is_week = False
        self._init_ui()
        self._refresh()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # 标题行
        header = QHBoxLayout()
        title = QLabel("偷取统计")
        title.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 20px; font-weight: 700;
            background: transparent; border: none;
        """)
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        # 视图切换控件
        ctrl = QHBoxLayout()
        ctrl.setSpacing(4)

        self._btn_day = QPushButton("天视图")
        self._btn_week = QPushButton("周视图")
        self._btn_day.setCheckable(True)
        self._btn_week.setCheckable(True)
        self._btn_day.setChecked(True)

        for btn in [self._btn_day, self._btn_week]:
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._apply_toggle_style(btn, btn.isChecked())

        self._btn_day.toggled.connect(self._on_view_changed)
        self._btn_day.toggled.connect(lambda c: self._apply_toggle_style(self._btn_day, c))
        self._btn_day.toggled.connect(lambda c: self._apply_toggle_style(self._btn_week, not c))

        ctrl.addWidget(self._btn_day)
        ctrl.addWidget(self._btn_week)
        ctrl.addStretch()
        root.addLayout(ctrl)

        # 图表卡片
        card = ElevatedCardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        coin_title = QLabel('金币')
        coin_title.setStyleSheet(f'color: {Colors.TEXT}; font-weight: 700; font-size: 13px; background: transparent;')
        self._coin_chart = _BarChart(bar_color='#f59e0b')
        bean_title = QLabel('金豆')
        bean_title.setStyleSheet(f'color: {Colors.TEXT}; font-weight: 700; font-size: 13px; background: transparent;')
        self._bean_chart = _BarChart(bar_color='#22c55e')

        card_layout.addWidget(coin_title)
        card_layout.addWidget(self._coin_chart)
        card_layout.addWidget(bean_title)
        card_layout.addWidget(self._bean_chart)
        root.addWidget(card, 1)

    @staticmethod
    def _apply_toggle_style(btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.PRIMARY};
                    color: #FFFFFF;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-weight: 600;
                    font-size: 12px;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.CARD_BG};
                    color: {Colors.TEXT_SECONDARY};
                    border: 1px solid {Colors.BORDER};
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    border-color: {Colors.PRIMARY};
                    color: {Colors.PRIMARY};
                }}
            """)

    def _on_view_changed(self, checked: bool):
        self._is_week = not checked
        self._refresh()

    def set_instance_id(self, instance_id: str):
        self._instance_id = instance_id
        self._refresh()

    def adjust_window(self, delta: int):
        if delta == 0:
            return
        if self._is_week:
            self._week_window = min(
                self._MAX_WEEK_WINDOW,
                max(self._MIN_WEEK_WINDOW, self._week_window + delta),
            )
        else:
            self._day_window = min(
                self._MAX_DAY_WINDOW,
                max(self._MIN_DAY_WINDOW, self._day_window + delta),
            )
        self._refresh()

    def _refresh(self):
        if self._is_week:
            today = date.today()
            current_monday = today - timedelta(days=today.weekday())
            first_monday = current_monday - timedelta(weeks=self._week_window - 1)
            days = (today - first_monday).days + 1
            day_data = load_stats(self._instance_id, days)
            day_map = {d: (coin, bean) for d, coin, bean in day_data}
            mondays = [first_monday + timedelta(weeks=i) for i in range(self._week_window)]
            data: list[tuple[str, int, int]] = []
            for monday in mondays:
                week_coin_sum = 0
                week_bean_sum = 0
                for offset in range(7):
                    current_day = monday + timedelta(days=offset)
                    if current_day > today:
                        break
                    day_coin, day_bean = day_map.get(current_day.isoformat(), (0, 0))
                    week_coin_sum += day_coin
                    week_bean_sum += day_bean
                data.append((monday.isoformat(), week_coin_sum, week_bean_sum))
        else:
            data = load_stats(self._instance_id, self._day_window)
        self._coin_chart.set_data([(d, coin) for d, coin, _ in data])
        self._bean_chart.set_data([(d, bean) for d, _, bean in data])

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()

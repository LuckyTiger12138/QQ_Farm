"""地块详情面板 — 纯 PyQt6 实现，24 格地块可视化网格"""
from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QShowEvent
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.styles import Colors
from gui.widgets.fluent_container import ElevatedCardWidget
from models.config import AppConfig


@dataclass(frozen=True)
class LandStateMeta:
    value: str
    label: str
    bg_color: str
    border_color: str
    text_color: str = '#1f2937'


LAND_STATE_META: dict[str, LandStateMeta] = {
    'unbuilt': LandStateMeta('unbuilt', '未扩建', '#D9C3A5', '#B2834A', '#5C432A'),
    'normal': LandStateMeta('normal', '普通', '#C39A64', '#7A552D', '#F9F2E7'),
    'red': LandStateMeta('red', '红', '#DF5737', '#9D3E27', '#FFF7F3'),
    'black': LandStateMeta('black', '黑', '#5C432A', '#3B2B1C', '#F8F5EF'),
    'gold': LandStateMeta('gold', '金', '#F9CB32', '#B78918', '#3C2B05'),
}

LAND_STATE_ORDER: list[str] = ['unbuilt', 'normal', 'red', 'black', 'gold']
LAND_STATE_ALIASES: dict[str, str] = {
    '未扩建': 'unbuilt', '普通': 'normal', '红': 'red', '黑': 'black', '金': 'gold',
}
LAND_STATE_RANK: dict[str, int] = {
    'unbuilt': 0, 'normal': 1, 'red': 2, 'black': 3, 'gold': 4,
}
LAND_COUNTDOWN_PATTERN = re.compile(r'^(\d{2}):(\d{2}):(\d{2})$')


class LandCell(QFrame):
    """单个地块格子"""
    state_changed = pyqtSignal(str, str)

    def __init__(self, plot_id: str, parent=None):
        super().__init__(parent)
        self.plot_id = plot_id
        self._countdown_seconds = 0
        self._need_upgrade = False
        self._need_planting = False
        self._init_ui()
        self.set_data({'level': 'unbuilt', 'maturity_countdown': ''})
        self.set_editable(False)

    def _init_ui(self):
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(90)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self._upgrade_badge = QLabel('待升级')
        self._upgrade_badge.setStyleSheet(
            'background: rgba(220,38,38,0.9); border-radius: 4px; color: #fff; '
            'font-size: 9px; font-weight: 700; padding: 1px 3px; border: none;'
        )
        self._upgrade_badge.setVisible(False)
        header.addWidget(self._upgrade_badge, 0, Qt.AlignmentFlag.AlignLeft)
        header.addStretch()

        self._plot_label = QLabel(self.plot_id)
        self._plot_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._plot_label.setStyleSheet('color: #5C432A; font-size: 11px; font-weight: 700; background: transparent; border: none;')
        header.addWidget(self._plot_label, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(header)

        root.addStretch(1)

        self._state_view = QLabel('未扩建')
        self._state_view.setObjectName('stateView')
        self._state_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state_view.setFixedHeight(22)
        self._state_view.setStyleSheet(
            'background: rgba(255,255,255,0.9); border: 1px solid rgba(0,0,0,0.1); '
            'border-radius: 5px; color: #0f172a; font-size: 11px; font-weight: 600; padding: 1px 4px;'
        )
        root.addWidget(self._state_view)

        self._state_combo = QComboBox()
        self._state_combo.setFixedHeight(26)
        for state in LAND_STATE_ORDER:
            meta = LAND_STATE_META[state]
            self._state_combo.addItem(meta.label, userData=state)
        self._state_combo.currentIndexChanged.connect(self._on_state_changed)
        root.addWidget(self._state_combo)

        self._countdown_view = QLabel('--:--:--')
        self._countdown_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._countdown_view.setStyleSheet(
            'background: rgba(255,255,255,0.85); border: 1px solid rgba(0,0,0,0.08); '
            'border-radius: 5px; color: #0f172a; font-size: 10px; font-weight: 600; padding: 1px 3px;'
        )
        root.addWidget(self._countdown_view)

        # 初始化样式（所有子控件已创建）
        self._apply_state_style('unbuilt')

    def _on_state_changed(self, _index):
        state = str(self._state_combo.currentData() or 'unbuilt')
        self._apply_state_style(state)
        self._state_view.setText(LAND_STATE_META.get(state, LAND_STATE_META['unbuilt']).label)
        self.state_changed.emit(self.plot_id, state)

    def _apply_state_style(self, state: str):
        meta = LAND_STATE_META.get(state, LAND_STATE_META['unbuilt'])
        if state == 'unbuilt':
            self.setStyleSheet(
                'QFrame { background: transparent; border: 2px dashed #cbd5e1; border-radius: 8px; }'
            )
        else:
            self.setStyleSheet(
                f'QFrame {{ background-color: {meta.bg_color}; border: none; border-radius: 8px; }}'
            )
        self._plot_label.setStyleSheet(
            f'color: {meta.text_color}; font-size: 11px; font-weight: 700; background: transparent; border: none;'
        )

    @staticmethod
    def _normalize_state(raw) -> str:
        value = str(raw or '').strip().lower()
        if value in LAND_STATE_META:
            return value
        return LAND_STATE_ALIASES.get(str(raw or '').strip(), 'unbuilt')

    def set_data(self, data: dict):
        state = self._normalize_state(data.get('level', 'unbuilt'))
        countdown_text = str(data.get('maturity_countdown', '') or '').strip()
        self._need_upgrade = bool(data.get('need_upgrade', False))
        self._need_planting = bool(data.get('need_planting', False))

        # 解析倒计时
        match = LAND_COUNTDOWN_PATTERN.match(countdown_text)
        if match:
            h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
            self._countdown_seconds = h * 3600 + m * 60 + s
        else:
            self._countdown_seconds = 0

        idx = self._state_combo.findData(state)
        if idx < 0:
            idx = 0
        self._state_combo.blockSignals(True)
        self._state_combo.setCurrentIndex(idx)
        self._state_combo.blockSignals(False)
        self._state_view.setText(LAND_STATE_META.get(state, LAND_STATE_META['unbuilt']).label)
        self._countdown_view.setText(countdown_text or '--:--:--')
        self._upgrade_badge.setVisible(self._need_upgrade)
        self._apply_state_style(state)

    def get_data(self) -> dict:
        state = str(self._state_combo.currentData() or 'unbuilt')
        cd = ''
        if self._countdown_seconds > 0:
            h = min(99, self._countdown_seconds // 3600)
            r = self._countdown_seconds % 3600
            m, s = r // 60, r % 60
            cd = f'{h:02d}:{m:02d}:{s:02d}'
        return {
            'plot_id': self.plot_id,
            'level': state,
            'maturity_countdown': cd,
            'need_upgrade': self._need_upgrade,
            'need_planting': self._need_planting,
        }

    def tick_countdown(self) -> bool:
        if self._countdown_seconds <= 0:
            return False
        self._countdown_seconds = max(0, self._countdown_seconds - 1)
        h = min(99, self._countdown_seconds // 3600)
        r = self._countdown_seconds % 3600
        m, s = r // 60, r % 60
        self._countdown_view.setText(f'{h:02d}:{m:02d}:{s:02d}' if self._countdown_seconds > 0 else '--:--:--')
        return True

    def set_editable(self, editable: bool):
        self._state_combo.setVisible(editable)
        self._state_combo.setEnabled(editable)
        self._state_view.setVisible(not editable)


class LandDetailPanel(QWidget):
    """土地详情标签页"""
    config_changed = pyqtSignal(object)
    refresh_requested = pyqtSignal()  # 请求 MainWindow 触发 OCR 刷新

    COL_COUNT = 6
    ROW_COUNT = 4

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._cells: dict[str, LandCell] = {}
        self._profile_labels: dict[str, QLabel] = {}
        self._editing = False
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._init_ui()
        self._load_from_config()
        self._set_edit_mode(False)
        self._countdown_timer.start()

    @staticmethod
    def _plot_id_at(row: int, col: int) -> str:
        display_col = 6 - col
        return f'{display_col}-{row + 1}'

    @classmethod
    def _plot_ids_visual_order(cls) -> list[str]:
        return [cls._plot_id_at(r, c) for r in range(cls.ROW_COUNT) for c in range(cls.COL_COUNT)]

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # 标题
        header = QHBoxLayout()
        title = QLabel("地块详情")
        title.setStyleSheet(f"color: {Colors.TEXT}; font-size: 20px; font-weight: 700; background: transparent; border: none;")
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        # 个人信息卡片
        profile_card = ElevatedCardWidget(self)
        profile_layout = QVBoxLayout(profile_card)
        profile_layout.setContentsMargins(14, 12, 14, 12)
        profile_layout.setSpacing(8)

        profile_header = QHBoxLayout()
        profile_title = QLabel("个人信息")
        profile_title.setStyleSheet(f"color: {Colors.TEXT}; font-weight: 700; font-size: 14px; background: transparent;")
        profile_header.addWidget(profile_title)

        self._refresh_hint = QLabel("切换页面时自动刷新")
        self._refresh_hint.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px; background: transparent;")
        profile_header.addWidget(self._refresh_hint)
        profile_header.addStretch()

        self._refresh_btn = QPushButton("立即刷新")
        self._refresh_btn.setFixedSize(72, 28)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.PRIMARY}; border: none;
                border-radius: 6px; color: #fff; font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {Colors.PRIMARY_HOVER}; }}
        """)
        self._refresh_btn.clicked.connect(self._on_refresh)
        profile_header.addWidget(self._refresh_btn)

        self._clear_btn = QPushButton("清除数据")
        self._clear_btn.setFixedSize(72, 28)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #ef4444; border: none;
                border-radius: 6px; color: #fff; font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: #dc2626; }}
        """)
        self._clear_btn.clicked.connect(self._on_clear_data)
        profile_header.addWidget(self._clear_btn)
        profile_layout.addLayout(profile_header)

        profile_divider = QFrame()
        profile_divider.setFixedHeight(1)
        profile_divider.setStyleSheet(f"background-color: rgba(0, 122, 255, 0.1); border: none;")
        profile_layout.addWidget(profile_divider)

        profile_grid = QHBoxLayout()
        profile_grid.setSpacing(16)
        for field_key, field_title in [('level', '等级'), ('gold', '金币'), ('coupon', '点券'), ('exp', '经验')]:
            item = QHBoxLayout()
            lbl = QLabel(f"{field_title}:")
            lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
            item.addWidget(lbl)
            val = QLabel("--")
            val.setStyleSheet(f"color: {Colors.TEXT}; font-weight: 700; font-size: 13px; background: transparent;")
            item.addWidget(val)
            item.addStretch()
            self._profile_labels[field_key] = val
            profile_grid.addLayout(item)
        profile_layout.addLayout(profile_grid)
        root.addWidget(profile_card)

        # 地块网格卡片
        board_card = ElevatedCardWidget(self)
        board_layout = QVBoxLayout(board_card)
        board_layout.setContentsMargins(14, 12, 14, 12)
        board_layout.setSpacing(8)

        board_header = QHBoxLayout()
        board_title = QLabel("土地信息")
        board_title.setStyleSheet(f"color: {Colors.TEXT}; font-weight: 700; font-size: 14px; background: transparent;")
        board_header.addWidget(board_title)

        subtitle = QLabel("管理 24 格地块状态，保存后写入配置")
        subtitle.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px; background: transparent;")
        board_header.addWidget(subtitle)
        board_header.addStretch()

        self._edit_btn = QPushButton("编辑")
        self._edit_btn.setFixedSize(56, 28)
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.clicked.connect(self._on_toggle_edit)
        self._apply_edit_btn_style(False)
        board_header.addWidget(self._edit_btn)
        board_layout.addLayout(board_header)

        board_divider = QFrame()
        board_divider.setFixedHeight(1)
        board_divider.setStyleSheet(f"background-color: rgba(0, 122, 255, 0.1); border: none;")
        board_layout.addWidget(board_divider)

        grid = QGridLayout()
        grid.setSpacing(6)
        for row in range(self.ROW_COUNT):
            for col in range(self.COL_COUNT):
                plot_id = self._plot_id_at(row, col)
                cell = LandCell(plot_id, board_card)
                grid.addWidget(cell, row, col)
                self._cells[plot_id] = cell
        for col in range(self.COL_COUNT):
            grid.setColumnStretch(col, 1)
        board_layout.addLayout(grid)

        root.addWidget(board_card, 1)

    def _apply_edit_btn_style(self, editing: bool):
        if editing:
            self._edit_btn.setText("保存")
            self._edit_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.SUCCESS}; border: none;
                    border-radius: 6px; color: #fff; font-weight: 600; font-size: 12px;
                }}
                QPushButton:hover {{ background-color: #2db84e; }}
            """)
        else:
            self._edit_btn.setText("编辑")
            self._edit_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.PRIMARY}; border: none;
                    border-radius: 6px; color: #fff; font-weight: 600; font-size: 12px;
                }}
                QPushButton:hover {{ background-color: {Colors.PRIMARY_HOVER}; }}
            """)

    def _set_edit_mode(self, editable: bool):
        self._editing = editable
        self._apply_edit_btn_style(editable)
        for cell in self._cells.values():
            cell.set_editable(editable)

    def _on_toggle_edit(self):
        if self._editing:
            # 保存
            self.config.land.plots = self.get_land_data()
            self.config.save()
            self.config_changed.emit(self.config)
        self._set_edit_mode(not self._editing)

    def _load_from_config(self):
        profile = self.config.land.profile
        level_val = int(profile.level) if profile.level else int(self.config.planting.player_level)
        self._profile_labels['level'].setText(str(level_val) if level_val > 0 else '--')
        self._profile_labels['gold'].setText(str(profile.gold).strip() or '--')
        self._profile_labels['coupon'].setText(str(profile.coupon).strip() or '--')
        self._profile_labels['exp'].setText(str(profile.exp).strip() or '--')
        self.set_land_data(self.config.land.plots)

    def set_land_data(self, items: list[dict]):
        # 先重置所有 cell 到默认状态
        for cell in self._cells.values():
            cell.set_data({'level': 'unbuilt', 'maturity_countdown': ''})
        for item in items:
            if not isinstance(item, dict):
                continue
            plot_id = str(item.get('plot_id', '')).strip()
            cell = self._cells.get(plot_id)
            if cell:
                cell.set_data(item)

    def get_land_data(self) -> list[dict]:
        return [self._cells[pid].get_data() for pid in self._plot_ids_visual_order() if pid in self._cells]

    def set_config(self, config: AppConfig):
        self.config = config
        self._set_edit_mode(False)
        self._load_from_config()

    def _on_clear_data(self):
        """一键清除所有地块数据"""
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("确认清除")
        msg.setText("确定要清除所有已保存的地块数据吗？")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.setStyleSheet(f"""
            QMessageBox {{
                background-color: #ffffff;
            }}
            QMessageBox QLabel {{
                color: {Colors.TEXT};
                font-size: 13px;
                background: transparent;
            }}
            QPushButton {{
                min-width: 64px; min-height: 28px;
                border-radius: 6px; font-weight: 600; font-size: 12px;
            }}
        """)
        reply = msg.exec()
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.config.land.plots = []
        try:
            self.config.save()
        except Exception:
            pass
        self._load_from_config()
        self.config_changed.emit(self.config)

    def _on_refresh(self):
        """立即刷新：触发 OCR 识别后重新加载配置"""
        self._refresh_hint.setText("刷新中...")
        self._refresh_btn.setEnabled(False)

        # 发出信号让 MainWindow 调用 BotEngine OCR 刷新
        self.refresh_requested.emit()

        # 延迟后从磁盘重新加载配置（等待 OCR 写入完成）
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, self._do_reload_after_ocr)

    def _do_reload_after_ocr(self):
        """OCR 刷新后重新加载配置"""
        cfg_path = str(getattr(self.config, '_config_path', '') or '').strip()
        if cfg_path:
            try:
                self.config = AppConfig.load(cfg_path)
            except Exception:
                pass
        self._set_edit_mode(False)
        self._load_from_config()
        self._refresh_hint.setText("已刷新")
        self._refresh_btn.setEnabled(True)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self._refresh_hint.setText("切换页面时自动刷新"))

    def _on_countdown_tick(self):
        for cell in self._cells.values():
            cell.tick_countdown()

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        # 每次显示时刷新
        cfg_path = str(getattr(self.config, '_config_path', '') or '').strip()
        if cfg_path:
            try:
                self.config = AppConfig.load(cfg_path)
            except Exception:
                pass
        self._load_from_config()

"""设置面板 — 现代卡片式布局，实时生效"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QSpinBox, QCheckBox, QComboBox,
    QFrame, QFormLayout, QGridLayout, QPushButton,
    QFileDialog, QScrollArea,
)
from PyQt6.QtCore import pyqtSignal, Qt

from models.config import AppConfig, PlantMode
from models.game_data import CROPS, get_crop_names, format_grow_time, get_best_crop_for_level
from gui.styles import Colors


class SettingRow(QFrame):
    def __init__(self, icon: str, title: str, widget: QWidget, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: none;
                border-bottom: 1px solid {Colors.BORDER};
            }}
            QFrame:last-child {{
                border-bottom: none;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 18px; background: transparent; border: none;")
        layout.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 13px;
            background: transparent; border: none;
        """)
        layout.addWidget(title_lbl)
        layout.addStretch()

        widget.setStyleSheet(f"""
            background-color: {Colors.INPUT_BG};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 5px 10px;
            color: {Colors.TEXT};
            min-height: 22px;
        """)
        layout.addWidget(widget)


class SettingCard(QFrame):
    def __init__(self, icon: str, title: str, subtitle: str, content_widget: QWidget, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 14px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(16, 14, 16, 4)
        header.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        header.addWidget(icon_lbl)

        info = QVBoxLayout()
        info.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 14px; font-weight: 600;
            background: transparent; border: none;
        """)
        info.addWidget(title_lbl)

        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setStyleSheet(f"""
                color: {Colors.TEXT_DIM}; font-size: 11px;
                background: transparent; border: none;
            """)
            info.addWidget(sub_lbl)

        header.addLayout(info)
        header.addStretch()
        layout.addLayout(header)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(16, 4, 16, 14)
        content_layout.setSpacing(8)
        content_layout.addWidget(content_widget)
        layout.addLayout(content_layout)


class ToggleGrid(QFrame):
    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._checkboxes = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        for i, (icon, label_text) in enumerate(items):
            cb = QCheckBox(f" {icon} {label_text}")
            cb.setStyleSheet(f"""
                QCheckBox {{
                    color: {Colors.TEXT};
                    font-size: 12px;
                    spacing: 4px;
                }}
                QCheckBox::indicator {{
                    width: 16px; height: 16px;
                    border: 1.5px solid rgba(0, 0, 0, 30);
                    border-radius: 4px;
                    background: {Colors.INPUT_BG};
                }}
                QCheckBox::indicator:checked {{
                    background: {Colors.PRIMARY};
                    border-color: {Colors.PRIMARY};
                    image: url(gui/icons/check.svg);
                }}
            """)
            self._checkboxes[label_text] = cb
            grid.addWidget(cb, i // 4, i % 4)

    def get_checkbox(self, name: str) -> QCheckBox:
        return self._checkboxes[name]


class SettingsPanel(QWidget):
    config_changed = pyqtSignal(object)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._loading = True
        self._init_ui()
        self._load_config()
        self._connect_auto_save()
        self._loading = False

    def _init_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        container.setStyleSheet("background: transparent; border: none;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── 顶部标题 ──
        header = QHBoxLayout()
        title = QLabel("参数设置")
        title.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 20px; font-weight: 700;
            background: transparent; border: none;
        """)
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # ===== 种植设置卡片 =====
        plant_widget = QWidget()
        plant_layout = QVBoxLayout(plant_widget)
        plant_layout.setContentsMargins(0, 0, 0, 0)
        plant_layout.setSpacing(0)

        self._player_level = QSpinBox()
        self._player_level.setRange(1, 100)
        self._player_level.setFixedWidth(80)

        self._strategy_combo = QComboBox()
        self._strategy_combo.addItem("自动最优", PlantMode.BEST_EXP_RATE.value)
        self._strategy_combo.addItem("手动指定", PlantMode.PREFERRED.value)
        self._strategy_combo.setFixedWidth(120)

        self._auto_crop_label = QLabel()
        self._auto_crop_label.setStyleSheet(
            f"color: {Colors.SUCCESS}; font-weight: 600; font-size: 12px; background: transparent; border: none;"
        )

        self._crop_combo = QComboBox()
        self._crop_names = get_crop_names()

        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("等级"))
        level_row.addWidget(self._player_level)
        level_row.addStretch()
        level_row.addWidget(QLabel("策略"))
        level_row.addWidget(self._strategy_combo)

        plant_layout.addWidget(self._make_row("🌱", "种植策略", level_row))
        plant_layout.addWidget(self._make_row("✨", "推荐作物", self._auto_crop_label))
        plant_layout.addWidget(self._make_row("🌾", "指定作物", self._crop_combo))

        self._player_level.valueChanged.connect(self._on_level_changed)
        self._player_level.valueChanged.connect(self._update_auto_crop_label)
        self._strategy_combo.currentIndexChanged.connect(self._on_strategy_changed)

        plant_card = SettingCard("🌿", "种植", "等级、策略与作物选择", plant_widget)
        layout.addWidget(plant_card)

        # ===== 功能开关卡片 =====
        toggle_items = [
            ("🌾", "收获"), ("🌱", "播种"), ("💊", "施肥"), ("🛒", "买种"),
            ("💧", "浇水"), ("🌿", "除草"), ("🐛", "除虫"), ("💰", "出售"),
            ("🥷", "偷菜"), ("🤝", "帮忙"), ("🎯", "任务"), ("🔨", "扩建"),
        ]
        self._toggle_grid = ToggleGrid(toggle_items)

        feat_card = SettingCard("⚙️", "功能开关", "选择需要自动执行的操作", self._toggle_grid)
        layout.addWidget(feat_card)

        # ===== 其他设置卡片 =====
        misc_widget = QWidget()
        misc_layout = QVBoxLayout(misc_widget)
        misc_layout.setContentsMargins(0, 0, 0, 0)
        misc_layout.setSpacing(0)

        self._window_keyword = QLineEdit()
        self._window_keyword.setPlaceholderText("QQ农场")

        self._game_shortcut = QLineEdit()
        self._game_shortcut.setPlaceholderText("选择 QQ 农场小程序快捷方式...")
        self._btn_browse = QPushButton("浏览...")
        self._btn_browse.setFixedWidth(70)
        self._btn_browse.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.PRIMARY};
                border: none;
                border-radius: 8px;
                color: #FFFFFF;
                padding: 5px 14px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {Colors.PRIMARY_HOVER};
            }}
        """)

        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(self._game_shortcut)
        shortcut_row.addWidget(self._btn_browse)

        self._farm_interval = QSpinBox()
        self._farm_interval.setRange(1, 120)
        self._farm_interval.setSuffix(" 分")
        self._farm_interval.setFixedWidth(90)

        self._friend_interval = QSpinBox()
        self._friend_interval.setRange(5, 180)
        self._friend_interval.setSuffix(" 分")
        self._friend_interval.setFixedWidth(90)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("农场"))
        interval_row.addWidget(self._farm_interval)
        interval_row.addWidget(QLabel("好友"))
        interval_row.addWidget(self._friend_interval)
        interval_row.addStretch()

        misc_layout.addWidget(self._make_row("🔍", "窗口关键词", self._window_keyword))
        misc_layout.addWidget(self._make_row("📁", "游戏路径", shortcut_row))
        misc_layout.addWidget(self._make_row("⏰", "检查间隔", interval_row))

        misc_card = SettingCard("🔧", "其他", "窗口、路径与调度设置", misc_widget)
        layout.addWidget(misc_card)

        layout.addStretch()

        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._btn_browse.clicked.connect(self._on_browse_shortcut)

    def _make_row(self, icon: str, title: str, widget) -> QFrame:
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: none;
                border-bottom: 1px solid {Colors.BORDER};
            }}
        """)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 16px; background: transparent; border: none;")
        layout.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"""
            color: {Colors.TEXT_SECONDARY}; font-size: 12px;
            background: transparent; border: none;
            min-width: 80px;
        """)
        layout.addWidget(title_lbl)
        layout.addStretch()

        if isinstance(widget, QWidget):
            layout.addWidget(widget)
        elif isinstance(widget, QHBoxLayout):
            layout.addLayout(widget)

        return row

    def _on_browse_shortcut(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择游戏快捷方式", "",
            "快捷方式 (*.lnk);;所有文件 (*.*)"
        )
        if file_path:
            self._game_shortcut.setText(file_path)
            self._auto_save()

    def _connect_auto_save(self):
        self._player_level.valueChanged.connect(self._auto_save)
        self._strategy_combo.currentIndexChanged.connect(self._auto_save)
        self._crop_combo.currentIndexChanged.connect(self._auto_save)
        self._window_keyword.editingFinished.connect(self._auto_save)
        self._game_shortcut.editingFinished.connect(self._auto_save)
        self._farm_interval.valueChanged.connect(self._auto_save)
        self._friend_interval.valueChanged.connect(self._auto_save)
        for cb in self._toggle_grid._checkboxes.values():
            cb.toggled.connect(self._auto_save)

    def _auto_save(self):
        if self._loading:
            return
        c = self.config
        c.planting.player_level = self._player_level.value()
        c.planting.strategy = PlantMode(self._strategy_combo.currentData())
        idx = self._crop_combo.currentIndex()
        if 0 <= idx < len(self._crop_names):
            c.planting.preferred_crop = self._crop_names[idx]
        c.window_title_keyword = self._window_keyword.text().strip()
        c.planting.game_shortcut_path = self._game_shortcut.text().strip()
        c.schedule.farm_check_minutes = self._farm_interval.value()
        c.schedule.friend_check_minutes = self._friend_interval.value()
        c.features.auto_harvest = self._toggle_grid.get_checkbox("收获").isChecked()
        c.features.auto_plant = self._toggle_grid.get_checkbox("播种").isChecked()
        c.features.auto_fertilize = self._toggle_grid.get_checkbox("施肥").isChecked()
        c.features.auto_buy_seed = self._toggle_grid.get_checkbox("买种").isChecked()
        c.features.auto_water = self._toggle_grid.get_checkbox("浇水").isChecked()
        c.features.auto_weed = self._toggle_grid.get_checkbox("除草").isChecked()
        c.features.auto_bug = self._toggle_grid.get_checkbox("除虫").isChecked()
        c.features.auto_sell = self._toggle_grid.get_checkbox("出售").isChecked()
        c.features.auto_steal = self._toggle_grid.get_checkbox("偷菜").isChecked()
        c.features.auto_help = self._toggle_grid.get_checkbox("帮忙").isChecked()
        c.features.auto_task = self._toggle_grid.get_checkbox("任务").isChecked()
        c.features.auto_upgrade = self._toggle_grid.get_checkbox("扩建").isChecked()
        c.save()
        self.config_changed.emit(c)

    def _on_level_changed(self, level: int):
        self._loading = True
        current_crop = (self._crop_names[self._crop_combo.currentIndex()]
                        if self._crop_combo.currentIndex() >= 0 else "")
        self._crop_combo.clear()
        for name, _, req_level, grow_time, exp, _ in CROPS:
            time_str = format_grow_time(grow_time)
            if req_level <= level:
                self._crop_combo.addItem(f"{name} (Lv{req_level}, {time_str}, {exp}经验)")
            else:
                self._crop_combo.addItem(f"[锁] {name} (需Lv{req_level})")
        if current_crop in self._crop_names:
            self._crop_combo.setCurrentIndex(self._crop_names.index(current_crop))
        self._loading = False

    def _on_strategy_changed(self, index: int):
        is_manual = self._strategy_combo.itemData(index) == PlantMode.PREFERRED.value
        self._crop_combo.setEnabled(is_manual)
        self._auto_crop_label.setVisible(not is_manual)
        self._update_auto_crop_label()

    def _update_auto_crop_label(self):
        level = self._player_level.value()
        best = get_best_crop_for_level(level)
        if best:
            name, _, _, grow_time, exp, _ = best
            time_str = format_grow_time(grow_time)
            rate = exp / grow_time
            self._auto_crop_label.setText(f"{name} ({time_str}, {exp}exp, {rate:.4f}/s)")
        else:
            self._auto_crop_label.setText("无可用作物")

    def _load_config(self):
        c = self.config
        self._player_level.setValue(c.planting.player_level)
        strategy_idx = 0 if c.planting.strategy == PlantMode.BEST_EXP_RATE else 1
        self._strategy_combo.setCurrentIndex(strategy_idx)
        self._on_strategy_changed(strategy_idx)
        self._update_auto_crop_label()
        if c.planting.preferred_crop in self._crop_names:
            self._crop_combo.setCurrentIndex(
                self._crop_names.index(c.planting.preferred_crop))
        self._on_level_changed(c.planting.player_level)
        self._window_keyword.setText(c.window_title_keyword)
        self._game_shortcut.setText(c.planting.game_shortcut_path)
        self._farm_interval.setValue(c.schedule.farm_check_minutes)
        self._friend_interval.setValue(c.schedule.friend_check_minutes)
        self._toggle_grid.get_checkbox("收获").setChecked(c.features.auto_harvest)
        self._toggle_grid.get_checkbox("播种").setChecked(c.features.auto_plant)
        self._toggle_grid.get_checkbox("施肥").setChecked(c.features.auto_fertilize)
        self._toggle_grid.get_checkbox("买种").setChecked(c.features.auto_buy_seed)
        self._toggle_grid.get_checkbox("浇水").setChecked(c.features.auto_water)
        self._toggle_grid.get_checkbox("除草").setChecked(c.features.auto_weed)
        self._toggle_grid.get_checkbox("除虫").setChecked(c.features.auto_bug)
        self._toggle_grid.get_checkbox("出售").setChecked(c.features.auto_sell)
        self._toggle_grid.get_checkbox("偷菜").setChecked(c.features.auto_steal)
        self._toggle_grid.get_checkbox("帮忙").setChecked(c.features.auto_help)
        self._toggle_grid.get_checkbox("任务").setChecked(c.features.auto_task)
        self._toggle_grid.get_checkbox("扩建").setChecked(c.features.auto_upgrade)

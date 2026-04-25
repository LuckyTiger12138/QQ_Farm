"""Fluent 设置面板 — 卡片式布局，实时生效，深色主题自适应。"""

from __future__ import annotations

import ctypes
import os
import pathlib

import pygetwindow as gw
from PyQt6.QtCore import QTime, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    LineEdit,
    PushButton,
    ScrollArea,
    SpinBox,
)

from gui.styles import Colors
from gui.widgets.fluent_container import StableElevatedCardWidget, TransparentCardContainer
from models.config import AppConfig, PlantMode, RunMode, WindowPosition, CrossInstancePartnerConfig
from models.game_data import CROPS, format_grow_time, get_best_crop_for_level, get_crop_names


class SettingsPanel(QWidget):
    """实例设置编辑面板 — 卡片式分组布局。"""

    config_changed = pyqtSignal(object)
    web_server_toggled = pyqtSignal(bool)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._crop_names = get_crop_names()
        self._loading = 0
        self._init_ui()
        self._load_config()
        self._connect_signals()
        self._loading = False

    # ── UI 构建 ────────────────────────────────────────────────

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)

        content = TransparentCardContainer(self)
        scroll.setWidget(content)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        # ── 种植卡片 ──
        plant_card, plant_form = self._build_group_card(content, "种植", "settingsPlantCard")
        layout.addWidget(plant_card)

        level_row = QWidget(plant_card)
        level_layout = QHBoxLayout(level_row)
        level_layout.setContentsMargins(0, 0, 0, 0)
        level_layout.setSpacing(8)
        self.level = SpinBox(plant_card)
        self.level.setRange(1, 100)
        level_layout.addWidget(self.level)
        level_layout.addStretch()
        plant_form.addRow(self._field_label("等级", plant_card), level_row)

        self.strategy = ComboBox(plant_card)
        self.strategy.addItem("自动最新", userData=PlantMode.LATEST_LEVEL.value)
        self.strategy.addItem("自动最优", userData=PlantMode.BEST_EXP_RATE.value)
        self.strategy.addItem("手动指定", userData=PlantMode.PREFERRED.value)
        plant_form.addRow(self._field_label("播种策略", plant_card), self.strategy)

        self.auto_crop_label = CaptionLabel("", plant_card)
        self.auto_crop_label.setStyleSheet("color: #16a34a; font-weight: 600;")
        plant_form.addRow(self._field_label("推荐作物", plant_card), self.auto_crop_label)

        self.crop = ComboBox(plant_card)
        plant_form.addRow(self._field_label("指定作物", plant_card), self.crop)

        self.warehouse_first = CheckBox("仓库优先", plant_card)
        warehouse_tip = CaptionLabel("建议开启，关闭后可能会因种子模板识别出错导致重复购买。", plant_card)
        warehouse_tip.setWordWrap(True)
        warehouse_tip.setStyleSheet("color: #d97706;")
        plant_form.addRow(self._field_label("播种", plant_card), self.warehouse_first)
        plant_form.addRow(self._field_label("", plant_card), warehouse_tip)
        self.skip_event_crops = CheckBox("排除活动作物", plant_card)
        plant_form.addRow(self._field_label("其他设置", plant_card), self.skip_event_crops)

        # ── 窗口与环境卡片 ──
        env_card, env_form = self._build_group_card(content, "窗口与环境", "settingsEnvCard")
        layout.addWidget(env_card)

        self.window_keyword = LineEdit(env_card)
        self.window_keyword.setPlaceholderText("窗口标题关键字")
        env_form.addRow(self._field_label("窗口关键词", env_card), self.window_keyword)

        select_row = QWidget(env_card)
        select_layout = QHBoxLayout(select_row)
        select_layout.setContentsMargins(0, 0, 0, 0)
        select_layout.setSpacing(8)
        self.window_select = ComboBox(select_row)
        select_layout.addWidget(self.window_select, 1)
        self.refresh_btn = PushButton("刷新", select_row)
        self.refresh_btn.setFixedWidth(64)
        select_layout.addWidget(self.refresh_btn)
        env_form.addRow(self._field_label("选择窗口", env_card), select_row)

        select_tip = CaptionLabel("自动模式按平台匹配；手动模式指定窗口索引。", env_card)
        select_tip.setWordWrap(True)
        select_tip.setStyleSheet("color: #64748b;")
        env_form.addRow(self._field_label("", env_card), select_tip)

        self.run_mode = ComboBox(env_card)
        self.run_mode.addItem("后台模式（窗口可遮挡）", userData=RunMode.BACKGROUND.value)
        self.run_mode.addItem("前台模式（需窗口置顶）", userData=RunMode.FOREGROUND.value)
        env_form.addRow(self._field_label("运行方式", env_card), self.run_mode)

        self.window_position = ComboBox(env_card)
        self.window_position.addItem("左下角", userData=WindowPosition.BOTTOM_LEFT.value)
        self.window_position.addItem("左上角", userData=WindowPosition.TOP_LEFT.value)
        self.window_position.addItem("右下角", userData=WindowPosition.BOTTOM_RIGHT.value)
        self.window_position.addItem("右上角", userData=WindowPosition.TOP_RIGHT.value)
        self.window_position.addItem("居中", userData=WindowPosition.CENTER.value)
        env_form.addRow(self._field_label("窗口位置", env_card), self.window_position)

        self.game_shortcut = LineEdit(env_card)
        self.game_shortcut.setPlaceholderText("QQ 农场小程序快捷方式路径...")
        shortcut_row = QWidget(env_card)
        shortcut_layout = QHBoxLayout(shortcut_row)
        shortcut_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_layout.setSpacing(8)
        shortcut_layout.addWidget(self.game_shortcut, 1)
        self.browse_btn = PushButton("浏览...", shortcut_row)
        self.browse_btn.setFixedWidth(70)
        shortcut_layout.addWidget(self.browse_btn)
        env_form.addRow(self._field_label("游戏路径", env_card), shortcut_row)

        # ── Web 服务卡片 ──
        web_card, web_form = self._build_group_card(content, "Web 服务", "settingsWebCard")
        layout.addWidget(web_card)

        self.web_status = CaptionLabel("● 已停止", web_card)
        self.web_status.setStyleSheet("font-weight: 600;")
        self.web_toggle_btn = PushButton("启动", web_card)
        web_ctrl_row = QWidget(web_card)
        web_ctrl_layout = QHBoxLayout(web_ctrl_row)
        web_ctrl_layout.setContentsMargins(0, 0, 0, 0)
        web_ctrl_layout.setSpacing(8)
        web_ctrl_layout.addWidget(self.web_status)
        web_ctrl_layout.addStretch()
        web_ctrl_layout.addWidget(self.web_toggle_btn)
        web_form.addRow(self._field_label("状态", web_card), web_ctrl_row)

        self.web_host = LineEdit(web_card)
        self.web_host.setFixedWidth(150)
        self.web_port = SpinBox(web_card)
        self.web_port.setRange(1024, 65535)
        web_addr_row = QWidget(web_card)
        web_addr_layout = QHBoxLayout(web_addr_row)
        web_addr_layout.setContentsMargins(0, 0, 0, 0)
        web_addr_layout.setSpacing(8)
        web_addr_layout.addWidget(CaptionLabel("地址:", web_addr_row))
        web_addr_layout.addWidget(self.web_host)
        web_addr_layout.addWidget(CaptionLabel("端口:", web_addr_row))
        web_addr_layout.addWidget(self.web_port)
        web_addr_layout.addStretch()
        web_form.addRow(self._field_label("", web_card), web_addr_row)

        # ── 高级卡片 ──
        adv_card, adv_form = self._build_group_card(content, "高级", "settingsAdvCard")
        layout.addWidget(adv_card)

        delay_row = QWidget(adv_card)
        delay_layout = QHBoxLayout(delay_row)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        delay_layout.setSpacing(8)
        self.delay_min = DoubleSpinBox(delay_row)
        self.delay_min.setRange(0, 10)
        self.delay_min.setDecimals(2)
        self.delay_min.setSingleStep(0.05)
        self.delay_min.setSuffix(" 秒")
        self.delay_max = DoubleSpinBox(delay_row)
        self.delay_max.setRange(0, 10)
        self.delay_max.setDecimals(2)
        self.delay_max.setSingleStep(0.05)
        self.delay_max.setSuffix(" 秒")
        delay_layout.addWidget(CaptionLabel("最小", delay_row))
        delay_layout.addWidget(self.delay_min, 1)
        delay_layout.addWidget(CaptionLabel("最大", delay_row))
        delay_layout.addWidget(self.delay_max, 1)
        adv_form.addRow(self._field_label("随机延迟", adv_card), delay_row)

        self.offset = SpinBox(adv_card)
        self.offset.setRange(0, 50)
        adv_form.addRow(self._field_label("点击抖动", adv_card), self.offset)

        self.max_actions = SpinBox(adv_card)
        self.max_actions.setRange(1, 500)
        adv_form.addRow(self._field_label("单轮点击上限", adv_card), self.max_actions)

        self.capture_interval = DoubleSpinBox(adv_card)
        self.capture_interval.setRange(0.0, 5.0)
        self.capture_interval.setDecimals(2)
        self.capture_interval.setSingleStep(0.05)
        self.capture_interval.setSuffix(" 秒")
        adv_form.addRow(self._field_label("截图间隔", adv_card), self.capture_interval)

        self.planting_stable = DoubleSpinBox(adv_card)
        self.planting_stable.setRange(0.1, 5.0)
        self.planting_stable.setDecimals(1)
        self.planting_stable.setSingleStep(0.1)
        self.planting_stable.setSuffix(" 秒")
        adv_form.addRow(self._field_label("播种稳定时间", adv_card), self.planting_stable)

        self.planting_stable_timeout = DoubleSpinBox(adv_card)
        self.planting_stable_timeout.setRange(0.5, 30.0)
        self.planting_stable_timeout.setDecimals(1)
        self.planting_stable_timeout.setSingleStep(0.5)
        self.planting_stable_timeout.setSuffix(" 秒")
        adv_form.addRow(self._field_label("播种稳定超时", adv_card), self.planting_stable_timeout)

        self.debug = CheckBox("启用 Debug 日志", adv_card)
        adv_form.addRow(self._field_label("调试日志", adv_card), self.debug)

        # 静默时段
        silent_row = QWidget(adv_card)
        silent_layout = QHBoxLayout(silent_row)
        silent_layout.setContentsMargins(0, 0, 0, 0)
        silent_layout.setSpacing(8)
        self.silent_enabled = CheckBox("静默时段", silent_row)
        silent_layout.addWidget(self.silent_enabled)
        self.silent_start = QTimeEdit(silent_row)
        self.silent_start.setDisplayFormat("HH:mm")
        self.silent_start.setFixedWidth(80)
        silent_layout.addWidget(self.silent_start)
        silent_layout.addWidget(BodyLabel("~"))
        self.silent_end = QTimeEdit(silent_row)
        self.silent_end.setDisplayFormat("HH:mm")
        self.silent_end.setFixedWidth(80)
        silent_layout.addWidget(self.silent_end)
        silent_layout.addStretch()
        adv_form.addRow(self._field_label("", adv_card), silent_row)

        self.logs_path_label = CaptionLabel("", adv_card)
        self.logs_path_label.setWordWrap(True)
        self.logs_path_label.setStyleSheet("color: #64748b;")
        adv_form.addRow(self._field_label("日志路径", adv_card), self.logs_path_label)

        # ── 大小号通讯卡片 ──
        ci_card, ci_form = self._build_group_card(content, "大小号通讯", "settingsCrossInstanceCard")
        layout.addWidget(ci_card)

        self.ci_enabled = CheckBox("启用大小号通讯", ci_card)
        ci_tip = CaptionLabel("当本实例地块即将成熟时，通知配对实例去偷菜。", ci_card)
        ci_tip.setWordWrap(True)
        ci_tip.setStyleSheet("color: #64748b;")
        ci_form.addRow(self._field_label("总开关", ci_card), self.ci_enabled)
        ci_form.addRow(self._field_label("", ci_card), ci_tip)

        ci_opts_row = QWidget(ci_card)
        ci_opts_layout = QHBoxLayout(ci_opts_row)
        ci_opts_layout.setContentsMargins(0, 0, 0, 0)
        ci_opts_layout.setSpacing(12)
        self.ci_send = CheckBox("发送成熟通知", ci_opts_row)
        self.ci_accept = CheckBox("接收偷菜任务", ci_opts_row)
        ci_opts_layout.addWidget(self.ci_send)
        ci_opts_layout.addWidget(self.ci_accept)
        ci_opts_layout.addStretch()
        ci_form.addRow(self._field_label("选项", ci_card), ci_opts_row)

        self.ci_threshold = SpinBox(ci_card)
        self.ci_threshold.setRange(30, 3600)
        self.ci_threshold.setSingleStep(30)
        self.ci_threshold.setSuffix(" 秒")
        ci_form.addRow(self._field_label("通知阈值", ci_card), self.ci_threshold)

        self.ci_partners = LineEdit(ci_card)
        self.ci_partners.setPlaceholderText("格式: 实例ID:好友昵称, 实例ID:好友昵称")
        ci_partners_tip = CaptionLabel("多个配对用逗号分隔，如: qq2:小号A, wx:大号B", ci_card)
        ci_partners_tip.setWordWrap(True)
        ci_partners_tip.setStyleSheet("color: #64748b;")
        ci_form.addRow(self._field_label("配对列表", ci_card), self.ci_partners)
        ci_form.addRow(self._field_label("", ci_card), ci_partners_tip)

        # ── 声明卡片 ──
        decl_card, decl_form = self._build_group_card(content, "声明", "settingsDeclCard")
        layout.addWidget(decl_card)

        free_notice = CaptionLabel(
            "本软件免费开源。如果你花钱购买的，请立即退款！ "
            "GitHub: github.com/luckytiger12138/qq-farm | Gitee: gitee.com/luckytiger12138/qq-farm",
            decl_card,
        )
        free_notice.setWordWrap(True)
        free_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        free_notice.setStyleSheet("color: #dc2626; font-weight: 700;")
        decl_form.addRow(self._field_label("免责声明", decl_card), free_notice)

        layout.addStretch()

    # ── 通用卡片构建 ─────────────────────────────────────────

    @staticmethod
    def _apply_card_style(card: StableElevatedCardWidget, object_name: str):
        card.setObjectName(object_name)
        card.setStyleSheet(
            f"ElevatedCardWidget#{object_name} {{"
            " border-radius: 10px; border: 1px solid rgba(100,116,139,0.22); }"
            f"ElevatedCardWidget#{object_name}:hover {{"
            " background-color: rgba(37,99,235,0.06); border: 1px solid rgba(59,130,246,0.32); }"
        )

    @staticmethod
    def _style_form(form: QFormLayout):
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.setHorizontalSpacing(0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _field_label(text: str, parent: QWidget) -> CaptionLabel:
        text_value = str(text or "").strip()
        label = CaptionLabel(f"{text_value}:" if text_value else "", parent)
        if text_value:
            label.setFixedWidth(label.sizeHint().width() + label.fontMetrics().horizontalAdvance("字"))
            label.setStyleSheet("color: #475569; font-weight: 600;")
        return label

    def _build_group_card(self, parent: QWidget, title: str, object_name: str) -> tuple:
        card = StableElevatedCardWidget(parent)
        self._apply_card_style(card, object_name)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(9)
        title_label = BodyLabel(title)
        title_label.setStyleSheet("font-weight: 700; font-size: 14px; color: #1e293b;")
        card_layout.addWidget(title_label)
        divider = QFrame(card)
        divider.setObjectName("settingsCardDivider")
        divider.setFixedHeight(1)
        divider.setStyleSheet("QFrame#settingsCardDivider { background-color: rgba(37,99,235,0.10); border: none; }")
        card_layout.addWidget(divider)
        form = QFormLayout()
        self._style_form(form)
        card_layout.addLayout(form)
        return card, form

    @staticmethod
    def _set_combo_data(combo: ComboBox, value):
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    # ── 信号连接 ────────────────────────────────────────────

    def _connect_signals(self):
        # 种植
        self.level.valueChanged.connect(self._on_level_changed)
        self.level.valueChanged.connect(self._update_auto_crop_label)
        self.strategy.currentIndexChanged.connect(self._on_strategy_changed)
        self.crop.currentIndexChanged.connect(self._auto_save)
        self.warehouse_first.toggled.connect(self._auto_save)
        self.skip_event_crops.toggled.connect(self._auto_save)
        # 窗口
        self.window_keyword.editingFinished.connect(self._on_keyword_committed)
        self.refresh_btn.clicked.connect(self._refresh_windows)
        self.window_select.currentIndexChanged.connect(self._auto_save)
        self.run_mode.currentIndexChanged.connect(self._auto_save)
        self.window_position.currentIndexChanged.connect(self._auto_save)
        self.game_shortcut.editingFinished.connect(self._auto_save)
        self.browse_btn.clicked.connect(self._on_browse_shortcut)
        # 静默时段
        self.silent_enabled.toggled.connect(self._auto_save)
        self.silent_start.timeChanged.connect(self._auto_save)
        self.silent_end.timeChanged.connect(self._auto_save)
        # Web
        self.web_toggle_btn.clicked.connect(self._on_web_toggle)
        self.web_host.editingFinished.connect(self._auto_save)
        self.web_port.valueChanged.connect(self._auto_save)
        # 高级
        self.delay_min.valueChanged.connect(self._auto_save)
        self.delay_max.valueChanged.connect(self._auto_save)
        self.offset.valueChanged.connect(self._auto_save)
        self.max_actions.valueChanged.connect(self._auto_save)
        self.capture_interval.valueChanged.connect(self._auto_save)
        self.planting_stable.valueChanged.connect(self._auto_save)
        self.planting_stable_timeout.valueChanged.connect(self._auto_save)
        self.debug.toggled.connect(self._auto_save)
        # 大小号通讯
        self.ci_enabled.toggled.connect(self._auto_save)
        self.ci_send.toggled.connect(self._auto_save)
        self.ci_accept.toggled.connect(self._auto_save)
        self.ci_threshold.valueChanged.connect(self._auto_save)
        self.ci_partners.editingFinished.connect(self._auto_save)

    # ── 自动保存 ────────────────────────────────────────────

    def _auto_save(self):
        if self._loading > 0:
            return
        self._loading += 1
        try:
            c = self.config
            # 种植
            c.planting.player_level = int(self.level.value())
            c.planting.strategy = PlantMode(str(self.strategy.currentData() or PlantMode.PREFERRED.value))
            c.planting.preferred_crop = str(self.crop.currentData() or c.planting.preferred_crop)
            c.planting.warehouse_first = bool(self.warehouse_first.isChecked())
            c.planting.skip_event_crops = bool(self.skip_event_crops.isChecked())
            # 窗口
            c.window_title_keyword = str(self.window_keyword.text() or "").strip()
            c.window_select_rule = str(self.window_select.currentData() or "auto")
            c.safety.run_mode = RunMode(str(self.run_mode.currentData() or RunMode.BACKGROUND.value))
            c.safety.window_position = WindowPosition(
                str(self.window_position.currentData() or WindowPosition.BOTTOM_LEFT.value)
            )
            c.planting.game_shortcut_path = str(self.game_shortcut.text() or "").strip()
            # 静默时段
            c.silent_hours.enabled = bool(self.silent_enabled.isChecked())
            c.silent_hours.start_hour = self.silent_start.time().hour()
            c.silent_hours.start_minute = self.silent_start.time().minute()
            c.silent_hours.end_hour = self.silent_end.time().hour()
            c.silent_hours.end_minute = self.silent_end.time().minute()
            # Web
            c.web.host = str(self.web_host.text() or "0.0.0.0").strip()
            c.web.port = int(self.web_port.value())
            # 高级
            d_min, d_max = float(self.delay_min.value()), float(self.delay_max.value())
            c.safety.random_delay_min = min(d_min, d_max)
            c.safety.random_delay_max = max(d_min, d_max)
            c.safety.click_offset_range = int(self.offset.value())
            c.safety.max_actions_per_round = int(self.max_actions.value())
            c.safety.debug_log_enabled = bool(self.debug.isChecked())
            c.screenshot.capture_interval_seconds = float(self.capture_interval.value())
            c.planting.planting_stable_seconds = float(self.planting_stable.value())
            c.planting.planting_stable_timeout_seconds = float(self.planting_stable_timeout.value())
            # 大小号通讯
            c.cross_instance.enabled = bool(self.ci_enabled.isChecked())
            c.cross_instance.send_alerts = bool(self.ci_send.isChecked())
            c.cross_instance.accept_steal = bool(self.ci_accept.isChecked())
            c.cross_instance.alert_threshold_seconds = int(self.ci_threshold.value())
            c.cross_instance.partners = self._parse_partners(str(self.ci_partners.text() or ""))

            c.save()
            self.config_changed.emit(c)
        finally:
            self._loading -= 1

    # ── 种植策略联动 ────────────────────────────────────────

    def _on_level_changed(self, level: int):
        self._loading += 1
        current_crop = str(self.crop.currentData() or "")
        self.crop.clear()
        for name, _, req_level, grow_time, exp, _ in CROPS:
            time_str = format_grow_time(grow_time)
            if req_level <= level:
                self.crop.addItem(f"{name} (Lv{req_level}, {time_str}, {exp}exp)", userData=name)
            else:
                self.crop.addItem(f"[锁] {name} (需Lv{req_level})", userData=name)
        if current_crop in self._crop_names:
            idx = self._crop_names.index(current_crop)
            if idx < self.crop.count():
                self.crop.setCurrentIndex(idx)
        self._loading -= 1

    def _on_strategy_changed(self, *_):
        is_manual = str(self.strategy.currentData() or "") == PlantMode.PREFERRED.value
        self.crop.setEnabled(is_manual)
        self.auto_crop_label.setVisible(not is_manual)
        self._update_auto_crop_label()

    def _update_auto_crop_label(self):
        level = int(self.level.value())
        strategy_value = str(self.strategy.currentData() or "")
        if strategy_value == PlantMode.LATEST_LEVEL.value:
            from models.game_data import get_latest_crop_for_level
            crop = get_latest_crop_for_level(level)
            if crop:
                name, _, _, grow_time, exp, _ = crop
                self.auto_crop_label.setText(f"{name} ({format_grow_time(grow_time)}, {exp}exp, 最新)")
            else:
                self.auto_crop_label.setText("无可用作物")
        elif strategy_value == PlantMode.BEST_EXP_RATE.value:
            best = get_best_crop_for_level(level)
            if best:
                name, _, _, grow_time, exp, _ = best
                rate = exp / grow_time
                self.auto_crop_label.setText(f"{name} ({format_grow_time(grow_time)}, {exp}exp, {rate:.4f}/s)")
            else:
                self.auto_crop_label.setText("无可用作物")

    # ── Web 服务 ────────────────────────────────────────────

    def _on_web_toggle(self):
        is_running = self.web_toggle_btn.text() == "停止"
        new_state = not is_running
        self._update_web_ui(new_state)
        self.web_server_toggled.emit(new_state)

    def _update_web_ui(self, running: bool):
        if running:
            self.web_status.setText("● 运行中")
            self.web_status.setStyleSheet("color: #16a34a; font-weight: 600;")
            self.web_toggle_btn.setText("停止")
            self.web_host.setEnabled(False)
            self.web_port.setEnabled(False)
        else:
            self.web_status.setText("● 已停止")
            self.web_status.setStyleSheet("color: #64748b; font-weight: 600;")
            self.web_toggle_btn.setText("启动")
            self.web_host.setEnabled(True)
            self.web_port.setEnabled(True)

    # ── 窗口选择 ────────────────────────────────────────────

    def _on_keyword_committed(self):
        self._refresh_windows()
        self._auto_save()

    def _on_browse_shortcut(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择游戏快捷方式", "", "快捷方式 (*.lnk);;所有文件 (*.*)"
        )
        if file_path:
            self.game_shortcut.setText(file_path)
            self._auto_save()

    def _refresh_windows(self):
        keyword = str(self.window_keyword.text() or "").strip() or "QQ农场"
        windows = self._list_windows(keyword)
        current = str(self.window_select.currentData() or self.config.window_select_rule or "auto")
        self.window_select.blockSignals(True)
        self.window_select.clear()
        self.window_select.addItem("自动（按平台优先）", userData="auto")
        for idx, info in enumerate(windows):
            self.window_select.addItem(self._format_window_option(idx, info), userData=f"index:{idx}")
        self._set_combo_data(self.window_select, current)
        self.window_select.blockSignals(False)

    @staticmethod
    def _format_window_option(index: int, info: dict) -> str:
        title = str(info.get("title", "")).replace("\n", " ").strip()
        if len(title) > 20:
            title = f"{title[:20]}..."
        process_name = str(info.get("process_name", "") or "").strip().lower()
        if process_name == "qq.exe" or process_name.startswith("qq"):
            platform = "QQ"
        elif process_name.startswith("wechat") or "weixin" in process_name:
            platform = "微信"
        else:
            platform = "未知"
        hwnd_hex = f"0x{int(info.get('hwnd', 0)):X}"
        return (
            f"#{index + 1} [{platform}] {title} | "
            f"{int(info.get('width', 0))}x{int(info.get('height', 0))} | "
            f"({int(info.get('left', 0))},{int(info.get('top', 0))}) {hwnd_hex}"
        )

    @staticmethod
    def _list_windows(title_keyword: str) -> list[dict]:
        try:
            all_windows = gw.getAllWindows()
            matched: list[dict] = []
            seen_hwnd: set[int] = set()
            for win in all_windows:
                title = str(getattr(win, "title", "") or "")
                if not title.strip():
                    continue
                keyword = title_keyword.lower()
                title_lower = title.lower()
                if keyword not in title_lower:
                    if "农场" not in title_lower or "助手" in title_lower:
                        continue
                hwnd = int(getattr(win, "_hWnd", 0) or 0)
                if hwnd <= 0 or hwnd in seen_hwnd:
                    continue
                width = int(getattr(win, "width", 0) or 0)
                height = int(getattr(win, "height", 0) or 0)
                left = int(getattr(win, "left", 0) or 0)
                top = int(getattr(win, "top", 0) or 0)
                if width < 300 or height < 300 or left < -5000 or top < -5000:
                    continue
                process_name = ""
                try:
                    pid = ctypes.c_ulong()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value:
                        try:
                            import psutil
                            proc = psutil.Process(pid.value)
                            process_name = proc.name()
                        except ImportError:
                            if "qq" in title_lower:
                                process_name = "QQ.exe"
                            elif "wechat" in title_lower or "微信" in title:
                                process_name = "WeChat.exe"
                except Exception:
                    pass
                if "megumiss" in title_lower or "devtools" in title_lower:
                    continue
                proc_lower = (process_name or "").lower()
                if proc_lower in ("chrome.exe", "msedge.exe", "firefox.exe", "code.exe"):
                    continue
                matched.append({
                    "hwnd": hwnd, "title": title, "left": left, "top": top,
                    "width": width, "height": height, "process_name": process_name,
                })
                seen_hwnd.add(hwnd)
            matched.sort(key=lambda item: (int(item["left"]), int(item["top"]), int(item["hwnd"])))
            return matched
        except Exception as e:
            from loguru import logger
            logger.error(f"列出窗口失败: {e}")
            return []

    # ── 加载配置 ────────────────────────────────────────────

    def _load_config(self):
        c = self.config
        # 种植
        self.level.setValue(int(c.planting.player_level))
        self._set_combo_data(self.strategy, c.planting.strategy.value)
        self._on_strategy_changed()
        self._on_level_changed(c.planting.player_level)
        self._update_auto_crop_label()
        if c.planting.preferred_crop in self._crop_names:
            self._set_combo_data(self.crop, c.planting.preferred_crop)
        self.warehouse_first.setChecked(bool(c.planting.warehouse_first))
        self.skip_event_crops.setChecked(bool(c.planting.skip_event_crops))
        # 窗口
        self.window_keyword.setText(str(c.window_title_keyword or ""))
        self._refresh_windows()
        self._set_combo_data(self.window_select, c.window_select_rule or "auto")
        self._set_combo_data(self.run_mode, c.safety.run_mode.value)
        self._set_combo_data(self.window_position, c.safety.window_position.value)
        self.game_shortcut.setText(str(c.planting.game_shortcut_path or ""))
        # 静默时段
        self.silent_enabled.setChecked(c.silent_hours.enabled)
        self.silent_start.setTime(QTime(c.silent_hours.start_hour, c.silent_hours.start_minute))
        self.silent_end.setTime(QTime(c.silent_hours.end_hour, c.silent_hours.end_minute))
        # Web
        self._update_web_ui(c.web.enabled)
        self.web_host.setText(str(c.web.host or "0.0.0.0"))
        self.web_port.setValue(int(c.web.port))
        # 高级
        self.delay_min.setValue(float(c.safety.random_delay_min))
        self.delay_max.setValue(float(c.safety.random_delay_max))
        self.offset.setValue(int(c.safety.click_offset_range))
        self.max_actions.setValue(int(c.safety.max_actions_per_round))
        self.capture_interval.setValue(float(c.screenshot.capture_interval_seconds))
        self.planting_stable.setValue(float(c.planting.planting_stable_seconds))
        self.planting_stable_timeout.setValue(float(c.planting.planting_stable_timeout_seconds))
        self.debug.setChecked(bool(c.safety.debug_log_enabled))
        # 大小号通讯
        self.ci_enabled.setChecked(bool(c.cross_instance.enabled))
        self.ci_send.setChecked(bool(c.cross_instance.send_alerts))
        self.ci_accept.setChecked(bool(c.cross_instance.accept_steal))
        self.ci_threshold.setValue(int(c.cross_instance.alert_threshold_seconds))
        self.ci_partners.setText(self._format_partners(c.cross_instance.partners))
        # 日志路径
        cfg_path = str(getattr(c, "_config_path", "") or "").strip()
        if cfg_path:
            try:
                p = pathlib.Path(cfg_path).resolve()
                if p.name.lower() == "config.json" and p.parent.name == "configs":
                    self.logs_path_label.setText(str((p.parent.parent / "logs").resolve()))
                else:
                    self.logs_path_label.setText(str(pathlib.Path("logs").resolve()))
            except Exception:
                self.logs_path_label.setText(str(pathlib.Path("logs").resolve()))

    # ── 大小号通讯配对解析 ────────────────────────────────────

    @staticmethod
    def _parse_partners(text: str) -> list:
        """解析配对字符串 'id1:name1, id2:name2' → CrossInstancePartnerConfig 列表"""
        partners = []
        text = str(text or "").strip()
        if not text:
            return partners
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                iid, fname = part.split(":", 1)
                iid = iid.strip()
                fname = fname.strip()
                if iid and fname:
                    partners.append(CrossInstancePartnerConfig(
                        instance_id=iid, friend_name=fname, enabled=True,
                    ))
        return partners

    @staticmethod
    def _format_partners(partners: list) -> str:
        """将 CrossInstancePartnerConfig 列表格式化为 'id1:name1, id2:name2'"""
        if not partners:
            return ""
        parts = []
        for p in partners:
            iid = getattr(p, "instance_id", "") or ""
            fname = getattr(p, "friend_name", "") or ""
            if iid and fname:
                parts.append(f"{iid}:{fname}")
        return ", ".join(parts)

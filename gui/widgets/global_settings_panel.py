"""Fluent 全局设置面板 — 主题/云母效果/版本检查。"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon,
    OptionsConfigItem,
    OptionsSettingCard,
    OptionsValidator,
    PrimaryPushSettingCard,
    SettingCard,
    SettingCardGroup,
    SwitchSettingCard,
    setTheme,
    Theme,
    isDarkTheme,
)


class _LocalOptionsSettingCard(OptionsSettingCard):
    """仅用于本面板的选项卡：不写 qconfig，全程本地值驱动。"""

    def __init__(self, config_item, icon, title, content=None, texts=None, parent=None):
        self._current_value = config_item.value
        super().__init__(config_item, icon, title, content, texts, parent)
        try:
            self.buttonGroup.buttonClicked.disconnect()
        except Exception:
            pass
        self.buttonGroup.buttonClicked.connect(self._on_button_clicked)
        self.setValue(config_item.value)

    def _on_button_clicked(self, button):
        value = button.property(self.configName)
        if value == self._current_value:
            return
        self._current_value = value
        self.configItem.value = value
        self.choiceLabel.setText(button.text())
        self.choiceLabel.adjustSize()
        self.optionChanged.emit(self.configItem)

    def setValue(self, value):
        self._current_value = value
        for button in self.buttonGroup.buttons():
            is_checked = button.property(self.configName) == value
            button.setChecked(is_checked)
            if is_checked:
                self.choiceLabel.setText(button.text())
                self.choiceLabel.adjustSize()

    def currentValue(self):
        return self._current_value


class GlobalSettingsPanel(QWidget):
    """应用级设置（主题/窗口效果/版本更新）。"""

    apply_requested = pyqtSignal(str, bool)
    check_update_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = True
        self._build_ui()
        self._loading = False

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self._theme_config = OptionsConfigItem(
            "global_settings",
            "theme_mode",
            "auto",
            OptionsValidator(["auto", "light", "dark"]),
        )
        self.settings_group = SettingCardGroup("全局设置", self)
        self.theme_card = _LocalOptionsSettingCard(
            self._theme_config,
            FluentIcon.BRUSH,
            "主题",
            "选择应用主题模式",
            texts=["跟随系统", "浅色", "深色"],
            parent=self.settings_group,
        )
        self.mica_card = SwitchSettingCard(
            FluentIcon.TRANSPARENT,
            "云母效果",
            "开启后使用窗口材质效果",
            parent=self.settings_group,
        )
        self.settings_group.addSettingCards([self.theme_card, self.mica_card])
        self.theme_card.optionChanged.connect(lambda *_: self._emit_apply())
        self.mica_card.checkedChanged.connect(lambda *_: self._emit_apply())
        root.addWidget(self.settings_group)

        self.version_group = SettingCardGroup("版本更新", self)
        self.version_card = SettingCard(
            FluentIcon.INFO,
            "当前版本",
            "-",
            parent=self.version_group,
        )

        # 读取版本号
        try:
            from utils.version import __version__
            version_text = f"v{__version__}"
        except Exception:
            version_text = "-"

        self.update_card = PrimaryPushSettingCard(
            "立即检查",
            FluentIcon.SYNC,
            "检查更新",
            "从 GitHub Release 获取最新版本",
            parent=self.version_group,
        )
        self.update_card.clicked.connect(self.check_update_requested.emit)
        self.version_group.addSettingCards([self.version_card, self.update_card])
        root.addWidget(self.version_group)

        # 开源声明
        notice_group = SettingCardGroup("声明", self)
        from qfluentwidgets import CaptionLabel
        notice_label = CaptionLabel(
            "本软件免费开源  |  GitHub: github.com/luckytiger12138/qq-farm  |  Gitee: gitee.com/luckytiger12138/qq-farm",
            notice_group,
        )
        notice_label.setWordWrap(True)
        notice_label.setStyleSheet("color: #dc2626; font-weight: 700;")
        notice_card = SettingCard(
            FluentIcon.INFO,
            "免费开源声明",
            "如果你花钱购买的，请立即退款！",
            parent=notice_group,
        )
        notice_group.addSettingCards([notice_card])
        root.addWidget(notice_group)

        root.addStretch()

        # 设置初始版本号
        self.version_card.setContent(version_text)

    def _emit_apply(self):
        if self._loading:
            return
        theme_value = str(self.theme_card.currentValue() or "auto")
        # 应用主题
        if theme_value == "dark":
            setTheme(Theme.DARK)
        elif theme_value == "light":
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.AUTO)
        self.apply_requested.emit(theme_value, bool(self.mica_card.isChecked()))

    def set_values(self, theme_mode: str, mica_enabled: bool):
        self._loading = True
        value = str(theme_mode or "auto")
        if value not in {"auto", "light", "dark"}:
            value = "auto"
        self.theme_card.setValue(value)
        self.mica_card.setChecked(bool(mica_enabled))
        self._loading = False

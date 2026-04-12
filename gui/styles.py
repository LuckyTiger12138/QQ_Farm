"""主题样式常量 — Apple Settings 风格

配色参考 macOS Settings:
  浅灰底 + 白色卡片 + 蓝色强调色
"""


class Colors:
    # 背景
    WINDOW_BG = "#f5f5f7"
    CARD_BG = "#ffffff"
    SIDEBAR_BG = "#f5f5f7"
    SIDEBAR_ITEM_HOVER = "rgba(0, 0, 0, 6)"
    SIDEBAR_ITEM_SELECTED = "#007AFF"
    SIDEBAR_ITEM_SELECTED_BG = "rgba(0, 122, 255, 12)"
    TITLEBAR_BG = "#f5f5f7"
    INPUT_BG = "#ffffff"
    INPUT_BG_FOCUS = "#ffffff"

    # 强调色
    PRIMARY = "#007AFF"
    PRIMARY_HOVER = "#0066D6"
    SUCCESS = "#34C759"
    WARNING = "#FF9500"
    DANGER = "#FF3B30"

    # 文字
    TEXT = "#1d1d1f"
    TEXT_SECONDARY = "#86868b"
    TEXT_DIM = "#aeaeb2"

    # 边框
    BORDER = "rgba(0, 0, 0, 12)"
    BORDER_FOCUS = "rgba(0, 122, 255, 120)"

    # 滚动条
    SCROLLBAR_TRACK = "transparent"
    SCROLLBAR_HANDLE = "rgba(0, 0, 0, 25)"

    # 选中
    SELECTION_BG = "rgba(0, 122, 255, 20)"


# ── 全局样式表 ────────────────────────────────────────────

GLASS_STYLESHEET = f"""
QWidget {{
    color: {Colors.TEXT};
    font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif;
    font-size: 13px;
}}

QGroupBox {{
    background-color: {Colors.CARD_BG};
    border: 1px solid {Colors.BORDER};
    border-radius: 10px;
    margin-top: 22px;
    padding: 20px 16px 14px 16px;
    font-weight: 600;
    font-size: 13px;
    color: {Colors.TEXT};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    top: 4px;
    padding: 0 6px;
    color: {Colors.TEXT_DIM};
    background-color: {Colors.CARD_BG};
    font-weight: 600;
    font-size: 12px;
    letter-spacing: 0.5px;
}}

QCheckBox {{
    spacing: 8px;
    color: {Colors.TEXT};
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

QLineEdit, QSpinBox, QComboBox {{
    background-color: {Colors.INPUT_BG};
    border: 1px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {Colors.TEXT};
    selection-background-color: {Colors.SELECTION_BG};
    min-height: 22px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {Colors.BORDER_FOCUS};
}}

QSpinBox::up-button {{
    subcontrol-position: top right; width: 20px;
    border: none; background: transparent;
    border-top-right-radius: 7px;
}}
QSpinBox::down-button {{
    subcontrol-position: bottom right; width: 20px;
    border: none; background: transparent;
    border-bottom-right-radius: 7px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: rgba(0, 122, 255, 20);
}}
QSpinBox::up-arrow {{
    image: url(gui/icons/arrow_up.svg); width: 10px; height: 6px;
}}
QSpinBox::down-arrow {{
    image: url(gui/icons/arrow_down.svg); width: 10px; height: 6px;
}}

QComboBox::down-arrow {{
    image: url(gui/icons/arrow_down.svg); width: 10px; height: 6px;
}}
QComboBox::drop-down {{
    border: none; padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {Colors.CARD_BG};
    color: {Colors.TEXT};
    border: 1px solid rgba(0, 0, 0, 18);
    border-radius: 10px;
    selection-background-color: rgba(0, 122, 255, 12);
    selection-color: {Colors.TEXT};
    outline: none;
    padding: 6px;
    font-size: 13px;
}}
QComboBox QAbstractItemView::item {{
    min-height: 32px;
    padding: 4px 10px;
    border-radius: 6px;
    margin: 2px 4px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: rgba(0, 122, 255, 8);
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: rgba(0, 122, 255, 12);
    color: {Colors.PRIMARY};
    font-weight: 600;
}}

QScrollBar:vertical {{
    background: {Colors.SCROLLBAR_TRACK}; width: 6px; border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {Colors.SCROLLBAR_HANDLE}; border-radius: 3px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(0, 0, 0, 40);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QLabel {{
    color: {Colors.TEXT};
}}

QScrollArea {{
    border: none;
    background: transparent;
}}

QMessageBox {{
    background-color: {Colors.CARD_BG};
    color: {Colors.TEXT};
}}
QMessageBox QLabel {{
    color: {Colors.TEXT};
    background: transparent;
}}
QMessageBox QPushButton {{
    background-color: {Colors.CARD_BG};
    color: {Colors.TEXT};
    border: 1px solid {Colors.BORDER};
    border-radius: 6px;
    padding: 6px 20px;
    min-width: 80px;
}}
QMessageBox QPushButton:hover {{
    background-color: rgba(0,0,0,6);
}}
QMessageBox QDialogButtonBox {{
    background-color: {Colors.CARD_BG};
}}
QMessageBox QScrollArea {{
    background: transparent;
}}

QToolTip {{
    background-color: {Colors.CARD_BG};
    color: {Colors.TEXT};
    border: 1px solid {Colors.BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}

QComboBox QAbstractItemView {{
    background-color: {Colors.CARD_BG};
    color: {Colors.TEXT};
    selection-background-color: rgba(0, 122, 255, 12);
    selection-color: {Colors.TEXT};
    border: none;
    outline: none;
    padding: 4px;
}}

QComboBox QAbstractItemView::item {{
    min-height: 28px;
    padding: 4px 8px;
    border-radius: 4px;
    margin: 2px;
    background-color: {Colors.CARD_BG};
    color: {Colors.TEXT};
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: rgba(0, 122, 255, 12);
    color: {Colors.TEXT};
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: rgba(0, 122, 255, 8);
}}

/* 修复下拉提示框黑色背景问题 */
QToolTip {{
    background-color: {Colors.CARD_BG};
    color: {Colors.TEXT};
    border: 1px solid {Colors.BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
}}
"""


def glass_button_style(color: str, hover: str) -> str:
    return f"""
        QPushButton {{
            background-color: {color}; color: #FFFFFF; border: none;
            border-radius: 8px; padding: 0 20px;
            font-weight: 600; font-size: 13px;
        }}
        QPushButton:hover {{ background-color: {hover}; }}
        QPushButton:disabled {{
            background-color: rgba(0, 0, 0, 10);
            color: {Colors.TEXT_DIM};
        }}
    """


def ghost_button_style() -> str:
    return f"""
        QPushButton {{
            background-color: transparent;
            border: 1px solid transparent;
            color: {Colors.TEXT_SECONDARY};
            padding: 4px 12px;
            font-size: 12px;
            border-radius: 6px;
        }}
        QPushButton:hover {{
            background-color: rgba(0, 0, 0, 8);
            color: {Colors.TEXT};
        }}
    """

"""模板管理面板 — 模板列表 + 启用/禁用 + 内置采集器"""
import json
import os
import time
import numpy as np
import cv2

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QFrame, QCheckBox, QLineEdit, QFileDialog,
    QSizePolicy, QComboBox, QDialog, QFormLayout, QDialogButtonBox,
    QSpacerItem, QSizePolicy as QSP, QSlider, QDoubleSpinBox, QSplitter,
    QMessageBox, QInputDialog, QSpinBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint, QThread, QTimer
from PyQt6.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QBrush, QFont

from gui.styles import Colors, ghost_button_style
from core.cv_detector import CVDetector, TEMPLATE_CATEGORIES
from loguru import logger


# ── 样式常量 ────────────────────────────────────────────────

_CAT_COLORS: dict[str, str] = {
    "button": "#007AFF",
    "status_icon": "#FF9500",
    "crop": "#34C759",
    "ui_element": "#AF52DE",
    "land": "#8E8E93",
    "seed": "#FF2D55",
    "shop": "#5856D6",
    "unknown": "#AEAEB2",
}

_CAT_LABELS: dict[str, str] = {
    "button": "按钮",
    "status_icon": "状态",
    "crop": "作物",
    "ui_element": "UI",
    "land": "土地",
    "seed": "种子",
    "shop": "商店",
    "unknown": "其他",
}

_PREFIX_LABELS: dict[str, str] = {
    "btn": "按钮 (btn_)",
    "bth": "特殊按钮 (bth_)",
    "icon": "状态图标 (icon_)",
    "crop": "作物 (crop_)",
    "ui": "界面元素 (ui_)",
    "land": "土地 (land_)",
    "seed": "种子 (seed_)",
    "shop": "商店 (shop_)",
}


def _tag_style(bg: str) -> str:
    return f"""
        QLabel {{
            background-color: {bg}; color: white;
            font-size: 10px; font-weight: 600;
            border-radius: 4px; padding: 1px 6px;
        }}
    """


def _icon_button(color: str, hover: str) -> str:
    return f"""
        QPushButton {{
            background-color: {color}; color: #fff; border: none;
            border-radius: 8px; padding: 6px 14px;
            font-weight: 600; font-size: 12px;
        }}
        QPushButton:hover {{ background-color: {hover}; }}
        QPushButton:disabled {{
            background-color: rgba(0,0,0,10); color: {Colors.TEXT_DIM};
        }}
    """


def _outline_button(color: str = Colors.TEXT_SECONDARY) -> str:
    return f"""
        QPushButton {{
            background: transparent; border: 1px solid rgba(0,0,0,15);
            color: {color}; border-radius: 8px; padding: 6px 14px;
            font-size: 12px;
        }}
        QPushButton:hover {{
            background-color: rgba(0,0,0,6);
            border-color: rgba(0,0,0,30);
        }}
        QPushButton:disabled {{
            color: {Colors.TEXT_DIM}; border-color: rgba(0,0,0,8);
        }}
    """


def _styled_msg_box(parent, title: str, text: str,
                    icon=QMessageBox.Icon.Question,
                    buttons: QMessageBox.StandardButton = (
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No),
                    default=QMessageBox.StandardButton.No) -> QMessageBox:
    """创建带正确背景色的 QMessageBox"""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.setStandardButtons(buttons)
    box.setDefaultButton(default)
    box.setStyleSheet(f"""
        QMessageBox {{
            background-color: {Colors.CARD_BG};
            color: {Colors.TEXT};
        }}
        QMessageBox QLabel {{
            color: {Colors.TEXT};
            background: transparent;
            font-size: 13px;
        }}
        QMessageBox QPushButton {{
            background-color: {Colors.CARD_BG};
            color: {Colors.TEXT};
            border: 1px solid rgba(0,0,0,20);
            border-radius: 6px;
            padding: 6px 20px;
            min-width: 80px;
            font-size: 13px;
        }}
        QMessageBox QPushButton:hover {{
            background-color: rgba(0,0,0,6);
        }}
        QMessageBox QDialogButtonBox {{
            background-color: {Colors.CARD_BG};
        }}
    """)
    return box


# ── 模板卡片 ────────────────────────────────────────────────


class TemplateCard(QFrame):
    """单个模板卡片：缩略图 + 名称 + 类别标签 + 时间 + 开关"""

    toggle_requested = pyqtSignal(str, bool)
    delete_requested = pyqtSignal(str)
    clicked = pyqtSignal(str)

    def __init__(self, name: str, filepath: str, disabled: bool,
                 mtime: float, parent=None):
        super().__init__(parent)
        self._name = name
        self._filepath = filepath
        self._enabled = not disabled
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        self.setObjectName("templateCard")
        self._apply_style()
        self._build(name, filepath, disabled, mtime)

    def _apply_style(self):
        if self._selected:
            bg, border = "#ffffff", Colors.PRIMARY
        elif self._enabled:
            bg, border = Colors.CARD_BG, Colors.BORDER
        else:
            bg, border = "#fafafa", "rgba(0,0,0,6)"
        hover_border = "rgba(0,122,255,40)" if not self._selected else Colors.PRIMARY
        self.setStyleSheet(f"""
            QFrame#templateCard {{
                background-color: {bg}; border: 2px solid {border};
                border-radius: 10px;
            }}
            QFrame#templateCard:hover {{
                border-color: {hover_border};
            }}
        """)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._apply_style()

    @property
    def name(self) -> str:
        return self._name

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(e.pos())
            # 只有点击 checkbox 或删除按钮时不触发 clicked
            if child is not None and isinstance(child, (QCheckBox, QPushButton)):
                super().mousePressEvent(e)
                return
            self.clicked.emit(self._name)
        super().mousePressEvent(e)

    def _build(self, name: str, filepath: str, disabled: bool, mtime: float):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        # 缩略图
        thumb = QLabel()
        thumb.setFixedSize(44, 44)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0,0,0,6);
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)
        px = self._load_thumb(filepath)
        if px:
            thumb.setPixmap(px.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(thumb)

        # 文字区
        info = QVBoxLayout()
        info.setSpacing(3)

        # 第 1 行：名称 + 类别标签
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(f"font-weight:600; font-size:13px; color:{Colors.TEXT}; background:transparent;")
        row1.addWidget(name_lbl)

        prefix = name.split("_")[0]
        cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")
        cat_color = _CAT_COLORS.get(cat, _CAT_COLORS["unknown"])
        cat_text = _CAT_LABELS.get(cat, cat)
        tag = QLabel(cat_text)
        tag.setFixedHeight(18)
        tag.setStyleSheet(_tag_style(cat_color))
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row1.addWidget(tag)

        # 启用/禁用状态标签
        if disabled:
            status_tag = QLabel("禁用")
            status_tag.setFixedHeight(18)
            status_tag.setStyleSheet(_tag_style(Colors.DANGER))
            status_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row1.addWidget(status_tag)
        row1.addStretch()
        info.addLayout(row1)

        # 第 2 行：修改时间
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        time_lbl = QLabel(ts)
        time_lbl.setStyleSheet(f"font-size:11px; color:{Colors.TEXT_DIM}; background:transparent;")
        info.addWidget(time_lbl)

        lay.addLayout(info, 1)

        # 开关
        self._cb = QCheckBox()
        self._cb.setChecked(not disabled)
        self._cb.setStyleSheet("QCheckBox{spacing:0;}")
        self._cb.stateChanged.connect(self._on_toggle)
        lay.addWidget(self._cb)

        # 删除
        btn = QPushButton("删除")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(42, 26)
        btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:none;
                color:{Colors.TEXT_DIM}; font-size:11px; border-radius:4px;
            }}
            QPushButton:hover {{
                background-color:rgba(255,59,48,12);
                color:{Colors.DANGER};
            }}
        """)
        btn.clicked.connect(lambda: self.delete_requested.emit(self._name))
        lay.addWidget(btn)

    def _load_thumb(self, fp: str) -> QPixmap | None:
        try:
            buf = np.fromfile(fp, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                return None
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            data = rgb.tobytes()
            qimg = QImage(data, w, h, ch * w, QImage.Format.Format_RGB888)
            return QPixmap.fromImage(qimg)
        except Exception:
            return None

    def _on_toggle(self, state: int):
        self._enabled = state == Qt.CheckState.Checked.value
        self._apply_style()
        self.toggle_requested.emit(self._name, self._enabled)


# ── 模板详情面板 ──────────────────────────────────────────


class TemplateDetailPanel(QFrame):
    """右侧模板详情面板：预览 + 信息 + 阈值调节 + 匹配测试"""

    closed = pyqtSignal()
    template_changed = pyqtSignal()
    template_renamed = pyqtSignal(str, str)  # old_name, new_name

    def __init__(self, detector: CVDetector, parent=None):
        super().__init__(parent)
        self._detector = detector
        self._name: str = ""
        self._filepath: str = ""
        self._test_bgr: np.ndarray | None = None
        self._pending_threshold: float | None = None
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._flush_threshold)
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QFrame#detailPanel {{
                background-color: {Colors.CARD_BG};
                border-left: 1px solid {Colors.BORDER};
                border-radius: 0;
            }}
        """)
        self.setObjectName("detailPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ── 顶栏：标题 + 关闭 ──
        top = QHBoxLayout()
        top.setSpacing(6)
        self._title = QLabel("模板详情")
        self._title.setStyleSheet(f"font-weight:700; font-size:15px; color:{Colors.TEXT}; background:transparent;")
        top.addWidget(self._title)
        top.addStretch()
        btn_close = QPushButton("✕")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setFixedSize(24, 24)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:none;
                color:{Colors.TEXT_DIM}; font-size:14px; border-radius:4px;
            }}
            QPushButton:hover {{
                background-color:rgba(0,0,0,8);
                color:{Colors.TEXT};
            }}
        """)
        btn_close.clicked.connect(self.closed.emit)
        top.addWidget(btn_close)
        root.addLayout(top)

        # ── 预览图 ──
        self._preview = QLabel()
        self._preview.setFixedHeight(80)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0,0,0,4);
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)
        root.addWidget(self._preview)

        # ── 信息区 ──
        info = QVBoxLayout()
        info.setSpacing(4)
        self._info_name = QLabel()
        self._info_name.setStyleSheet(f"font-weight:600; font-size:14px; color:{Colors.TEXT}; background:transparent;")
        info.addWidget(self._info_name)

        row_cat = QHBoxLayout()
        row_cat.setSpacing(6)
        self._info_cat_tag = QLabel()
        self._info_cat_tag.setFixedHeight(18)
        self._info_cat_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row_cat.addWidget(self._info_cat_tag)
        self._info_dims = QLabel()
        self._info_dims.setStyleSheet(f"font-size:11px; color:{Colors.TEXT_DIM}; background:transparent;")
        row_cat.addWidget(self._info_dims)
        self._info_filesize = QLabel()
        self._info_filesize.setStyleSheet(f"font-size:11px; color:{Colors.TEXT_DIM}; background:transparent;")
        row_cat.addWidget(self._info_filesize)
        row_cat.addStretch()
        info.addLayout(row_cat)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self._info_status_tag = QLabel()
        self._info_status_tag.setFixedHeight(16)
        self._info_status_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_row.addWidget(self._info_status_tag)
        self._info_threshold_val = QLabel()
        self._info_threshold_val.setStyleSheet(f"font-size:11px; color:{Colors.TEXT}; font-weight:600; background:transparent;")
        status_row.addWidget(self._info_threshold_val)
        status_row.addStretch()
        info.addLayout(status_row)
        root.addLayout(info)

        # ── 操作按钮区 ──
        edit_row = QHBoxLayout()
        edit_row.setSpacing(6)

        self._btn_rename = QPushButton("重命名")
        self._btn_rename.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_rename.setFixedHeight(26)
        self._btn_rename.setStyleSheet(_outline_button())
        self._btn_rename.clicked.connect(self._on_rename)
        edit_row.addWidget(self._btn_rename)

        self._btn_replace = QPushButton("替换图片")
        self._btn_replace.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_replace.setFixedHeight(26)
        self._btn_replace.setStyleSheet(_outline_button())
        self._btn_replace.clicked.connect(self._on_replace_image)
        edit_row.addWidget(self._btn_replace)

        self._btn_toggle_detail = QPushButton()
        self._btn_toggle_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_toggle_detail.setFixedHeight(26)
        self._btn_toggle_detail.setStyleSheet(_outline_button())
        self._btn_toggle_detail.clicked.connect(self._on_toggle_enabled)
        edit_row.addWidget(self._btn_toggle_detail)

        self._btn_delete_detail = QPushButton("删除")
        self._btn_delete_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_delete_detail.setFixedHeight(26)
        self._btn_delete_detail.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:1px solid rgba(255,59,48,30);
                color:{Colors.DANGER}; font-size:11px; border-radius:6px;
                padding: 2px 10px;
            }}
            QPushButton:hover {{
                background-color:rgba(255,59,48,10);
            }}
        """)
        self._btn_delete_detail.clicked.connect(self._on_delete)
        edit_row.addWidget(self._btn_delete_detail)

        root.addLayout(edit_row)

        # ── 分隔线 ──
        sep1 = QFrame()
        sep1.setFixedHeight(1)
        sep1.setStyleSheet(f"background-color:{Colors.BORDER};")
        root.addWidget(sep1)

        # ── 阈值区 ──
        th_label = QLabel("匹配阈值")
        th_label.setStyleSheet(f"font-weight:600; font-size:12px; color:{Colors.TEXT_SECONDARY}; background:transparent;")
        root.addWidget(th_label)

        th_row = QHBoxLayout()
        th_row.setSpacing(8)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(10, 100)
        self._slider.setValue(80)
        self._slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: rgba(0,0,0,12); border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 16px; height: 16px; margin: -6px 0;
                background: {Colors.PRIMARY}; border-radius: 8px;
            }}
            QSlider::sub-page:horizontal {{
                background: {Colors.PRIMARY}; border-radius: 2px;
            }}
        """)
        self._slider.valueChanged.connect(self._on_slider_changed)
        th_row.addWidget(self._slider, 1)
        self._spinbox = QDoubleSpinBox()
        self._spinbox.setRange(0.1, 1.0)
        self._spinbox.setSingleStep(0.01)
        self._spinbox.setDecimals(2)
        self._spinbox.setFixedWidth(72)
        self._spinbox.setStyleSheet(f"""
            QDoubleSpinBox {{
                background: {Colors.WINDOW_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 2px 6px; color: {Colors.TEXT};
                font-size: 12px;
            }}
        """)
        self._spinbox.valueChanged.connect(self._on_spinbox_changed)
        th_row.addWidget(self._spinbox)
        root.addLayout(th_row)

        hint_row = QHBoxLayout()
        self._th_hint = QLabel()
        self._th_hint.setStyleSheet(f"font-size:11px; color:{Colors.TEXT_DIM}; background:transparent;")
        hint_row.addWidget(self._th_hint)
        hint_row.addStretch()
        self._btn_reset_th = QPushButton("重置默认")
        self._btn_reset_th.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset_th.setFixedHeight(22)
        self._btn_reset_th.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                color: {Colors.PRIMARY}; font-size:11px; border-radius:4px;
                padding: 2px 8px;
            }}
            QPushButton:hover {{
                background-color: rgba(0,122,255,8);
            }}
        """)
        self._btn_reset_th.clicked.connect(self._on_reset_threshold)
        hint_row.addWidget(self._btn_reset_th)
        root.addLayout(hint_row)

        # ── 分隔线 ──
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background-color:{Colors.BORDER};")
        root.addWidget(sep2)

        # ── 匹配测试区 ──
        test_label = QLabel("匹配测试")
        test_label.setStyleSheet(f"font-weight:600; font-size:12px; color:{Colors.TEXT_SECONDARY}; background:transparent;")
        root.addWidget(test_label)

        test_btns = QHBoxLayout()
        test_btns.setSpacing(8)
        self._btn_capture_test = QPushButton("截取窗口")
        self._btn_capture_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_capture_test.setStyleSheet(_outline_button())
        self._btn_capture_test.clicked.connect(self._on_capture_test_image)
        test_btns.addWidget(self._btn_capture_test)
        self._btn_select_img = QPushButton("选择图片")
        self._btn_select_img.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_select_img.setStyleSheet(_outline_button())
        self._btn_select_img.clicked.connect(self._on_select_test_image)
        test_btns.addWidget(self._btn_select_img)
        self._btn_run_test = QPushButton("开始测试")
        self._btn_run_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_run_test.setStyleSheet(_icon_button(Colors.PRIMARY, Colors.PRIMARY_HOVER))
        self._btn_run_test.setEnabled(False)
        self._btn_run_test.clicked.connect(self._on_run_test)
        test_btns.addWidget(self._btn_run_test)
        root.addLayout(test_btns)

        self._test_info = QLabel()
        self._test_info.setStyleSheet(f"font-size:11px; color:{Colors.TEXT_DIM}; background:transparent;")
        self._test_info.setWordWrap(True)
        root.addWidget(self._test_info)

        # 测试结果预览 — 使用 ScrollArea 包裹，允许缩放查看
        self._test_scroll = QScrollArea()
        self._test_scroll.setWidgetResizable(True)
        self._test_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._test_scroll.setMinimumHeight(120)
        self._test_preview = QLabel()
        self._test_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._test_preview.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0,0,0,4);
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)
        self._test_scroll.setWidget(self._test_preview)
        root.addWidget(self._test_scroll, 1)

    # ── 加载模板 ──

    def load_template(self, name: str, filepath: str):
        """加载模板详情"""
        self._name = name
        self._filepath = filepath
        self._test_bgr = None
        self._btn_run_test.setEnabled(False)
        self._test_info.clear()
        self._test_preview.clear()

        # 预览图
        px = self._load_pixmap(filepath)
        if px:
            scaled = px.scaled(280, 96, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self._preview.setPixmap(scaled)
        else:
            self._preview.clear()

        # 信息
        self._info_name.setText(name)
        prefix = name.split("_")[0]
        cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")
        cat_color = _CAT_COLORS.get(cat, _CAT_COLORS["unknown"])
        cat_text = _CAT_LABELS.get(cat, cat)
        self._info_cat_tag.setText(cat_text)
        self._info_cat_tag.setStyleSheet(_tag_style(cat_color))

        # 图片尺寸
        if px:
            self._info_dims.setText(f"{px.width()} × {px.height()} px")
        else:
            self._info_dims.setText("")

        # 文件大小
        try:
            fsize = os.path.getsize(filepath)
            if fsize > 1024:
                self._info_filesize.setText(f"文件大小: {fsize / 1024:.1f} KB")
            else:
                self._info_filesize.setText(f"文件大小: {fsize} B")
        except OSError:
            self._info_filesize.setText("")

        # 状态
        is_disabled = self._detector.is_template_disabled(name)
        if is_disabled:
            self._info_status_tag.setText("已禁用")
            self._info_status_tag.setStyleSheet(_tag_style(Colors.DANGER))
            self._btn_toggle_detail.setText("启用")
        else:
            self._info_status_tag.setText("已启用")
            self._info_status_tag.setStyleSheet(_tag_style(Colors.SUCCESS))
            self._btn_toggle_detail.setText("禁用")

        # 阈值
        current_th = self._detector.get_template_threshold(name)
        all_th = self._detector.get_all_thresholds()
        has_custom = name in all_th
        cat_default = self._detector.CATEGORY_DEFAULTS.get(cat, 0.8)
        th_source = "自定义" if has_custom else f"类别默认 {cat_default:.2f}"
        self._info_threshold_val.setText(f"{current_th:.2f}（{th_source}）")

        self._slider.blockSignals(True)
        self._spinbox.blockSignals(True)
        self._slider.setValue(int(current_th * 100))
        self._spinbox.setValue(current_th)
        self._slider.blockSignals(False)
        self._spinbox.blockSignals(False)

        cat_default = self._detector.CATEGORY_DEFAULTS.get(cat, 0.8)
        if has_custom:
            self._th_hint.setText(f"自定义阈值（类别默认 {cat_default:.2f}）")
            self._btn_reset_th.show()
        else:
            self._th_hint.setText(f"使用类别默认阈值 {cat_default:.2f}")
            self._btn_reset_th.hide()

        self.show()

    def clear(self):
        """重置面板"""
        self._name = ""
        self._filepath = ""
        self._test_bgr = None
        self._preview.clear()
        self._info_name.clear()
        self._info_cat_tag.clear()
        self._info_dims.clear()
        self._info_filesize.clear()
        self._test_info.clear()
        self._test_preview.clear()
        self.hide()

    # ── 阈值操作 ──

    def _on_slider_changed(self, val: int):
        th = val / 100.0
        self._spinbox.blockSignals(True)
        self._spinbox.setValue(th)
        self._spinbox.blockSignals(False)
        self._save_threshold(th)

    def _on_spinbox_changed(self, val: float):
        self._slider.blockSignals(True)
        self._slider.setValue(int(val * 100))
        self._slider.blockSignals(False)
        self._save_threshold(val)

    def _save_threshold(self, val: float):
        if self._name:
            self._pending_threshold = val
            self._debounce_timer.start()
            prefix = self._name.split("_")[0]
            cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")
            cat_default = self._detector.CATEGORY_DEFAULTS.get(cat, 0.8)
            self._th_hint.setText(f"自定义阈值（类别默认 {cat_default:.2f}）")
            self._btn_reset_th.show()

    def _flush_threshold(self):
        if self._name and self._pending_threshold is not None:
            self._detector.set_template_threshold(self._name, self._pending_threshold)
            self._pending_threshold = None

    def _on_reset_threshold(self):
        if not self._name:
            return
        self._detector.reset_template_threshold(self._name)
        # 重新加载
        cat_default = self._detector.get_template_threshold(self._name)
        self._slider.blockSignals(True)
        self._spinbox.blockSignals(True)
        self._slider.setValue(int(cat_default * 100))
        self._spinbox.setValue(cat_default)
        self._slider.blockSignals(False)
        self._spinbox.blockSignals(False)
        prefix = self._name.split("_")[0]
        cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")
        default = self._detector.CATEGORY_DEFAULTS.get(cat, 0.8)
        self._th_hint.setText(f"使用类别默认阈值 {default:.2f}")
        self._btn_reset_th.hide()

    # ── 匹配测试 ──

    def _on_capture_test_image(self):
        """截取游戏窗口作为测试图片"""
        try:
            from core.window_manager import WindowManager
            from core.screen_capture import ScreenCapture
            wm = WindowManager()
            sc = ScreenCapture()
            window = wm.find_window("QQ经典农场")
            if not window:
                self._test_info.setText("未找到游戏窗口")
                return
            wm.activate_window()
            import time as _t
            _t.sleep(0.5)
            rect = (window.left, window.top, window.width, window.height)
            pil_img = sc.capture_region(rect)
            if pil_img is None:
                self._test_info.setText("截屏失败")
                return
            rgb = np.array(pil_img.convert("RGB"))
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            self._test_bgr = bgr
            h, w = bgr.shape[:2]
            self._test_info.setText(f"已截取窗口 ({w}×{h})")
            self._btn_run_test.setEnabled(True)
            self._show_test_image(bgr)
        except Exception as e:
            self._test_info.setText(f"截取失败: {e}")

    def _on_select_test_image(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择测试图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*)")
        if not fp:
            return
        try:
            buf = np.fromfile(fp, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                self._test_info.setText("无法读取图片")
                return
            self._test_bgr = img
            h, w = img.shape[:2]
            self._test_info.setText(f"已选择: {os.path.basename(fp)} ({w}×{h})")
            self._btn_run_test.setEnabled(True)

            # 显示缩略图
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            data = rgb.tobytes()
            qimg = QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888)
            px = QPixmap.fromImage(qimg)
            self._test_preview.setPixmap(
                px.scaled(self._test_preview.width(), 160,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation))
        except Exception as e:
            self._test_info.setText(f"读取失败: {e}")

    def _on_run_test(self):
        if self._test_bgr is None or not self._name:
            return

        threshold = self._spinbox.value()

        # 确保模板已加载
        if not self._detector._loaded:
            self._detector.load_templates()

        results = self._detector.detect_single_template(
            self._test_bgr, self._name, threshold
        )

        if not results:
            self._test_info.setText(f"未匹配到结果（阈值 {threshold:.2f}）")
            # 仍然显示原图
            self._show_test_image(self._test_bgr)
            return

        # 显示结果
        confs = [r.confidence for r in results]
        self._test_info.setText(
            f"匹配到 {len(results)} 处，"
            f"置信度: {min(confs):.2f} ~ {max(confs):.2f}"
        )

        # 绘制标注图
        annotated = self._detector.draw_results(self._test_bgr, results)
        self._show_test_image(annotated)

    def _show_test_image(self, bgr: np.ndarray):
        """显示测试图片，自适应预览区域大小"""
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        data = rgb.tobytes()
        qimg = QImage(data, w, h, ch * w, QImage.Format.Format_RGB888)
        px = QPixmap.fromImage(qimg)
        # 限制最大显示尺寸，适应容器宽度
        max_h = self._test_scroll.viewport().height() - 4
        scaled = px.scaled(
            self._test_scroll.viewport().width() - 4,
            max(max_h, 200),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._test_preview.setPixmap(scaled)
        self._test_preview.setMinimumSize(scaled.size())

    # ── 编辑操作 ──

    def _on_rename(self):
        """重命名模板"""
        if not self._name:
            return
        dlg = QInputDialog(self)
        dlg.setWindowTitle("重命名模板")
        dlg.setLabelText("新名称:")
        dlg.setTextValue(self._name)
        dlg.setStyleSheet(f"""
            QInputDialog, QInputDialog QFrame {{
                background-color: {Colors.CARD_BG};
                color: {Colors.TEXT};
            }}
            QInputDialog QLabel {{
                color: {Colors.TEXT};
                background: transparent;
                font-size: 13px;
            }}
            QInputDialog QLineEdit {{
                background-color: {Colors.WINDOW_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                color: {Colors.TEXT};
            }}
            QInputDialog QPushButton {{
                background-color: {Colors.CARD_BG};
                color: {Colors.TEXT};
                border: 1px solid rgba(0,0,0,20);
                border-radius: 6px;
                padding: 6px 20px;
                min-width: 80px;
            }}
            QInputDialog QPushButton:hover {{
                background-color: rgba(0,0,0,6);
            }}
            QInputDialog QDialogButtonBox {{
                background-color: {Colors.CARD_BG};
            }}
        """)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = dlg.textValue().strip()
        if not new_name or new_name == self._name:
            return
        # 验证前缀
        prefix = new_name.split("_")[0]
        if prefix not in TEMPLATE_CATEGORIES:
            box = _styled_msg_box(self, "前缀无效",
                "名称必须以有效前缀开头（btn_, bth_, icon_, crop_, ui_, land_, seed_, shop_）",
                icon=QMessageBox.Icon.Warning,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()
            return
        old_name = self._name
        # 检查是否已存在
        new_fp = os.path.join(self._detector._templates_dir, f"{new_name}.png")
        if os.path.exists(new_fp) and new_name != self._name:
            box = _styled_msg_box(self, "覆盖",
                f"「{new_name}」已存在，是否覆盖？")
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        # 重命名文件
        old_fp = self._filepath
        try:
            if os.path.exists(new_fp) and new_name != old_name:
                os.remove(new_fp)
            os.rename(old_fp, new_fp)
            # 更新阈值和禁用状态
            old_th = self._detector.get_all_thresholds()
            if old_name in old_th:
                self._detector.set_template_threshold(new_name, old_th[old_name])
                self._detector.reset_template_threshold(old_name)
            if self._detector.is_template_disabled(old_name):
                self._detector.set_template_enabled(old_name, True)
                self._detector.set_template_enabled(new_name, False)
            self._filepath = new_fp
            self._name = new_name
            self._info_name.setText(new_name)
            self.template_renamed.emit(old_name, new_name)
        except Exception as e:
            box = _styled_msg_box(self, "重命名失败", str(e),
                icon=QMessageBox.Icon.Warning,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()

    def _on_replace_image(self):
        """替换模板图片"""
        if not self._filepath:
            return
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择新图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*)")
        if not fp:
            return
        try:
            buf = np.fromfile(fp, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
            if img is None:
                box = _styled_msg_box(self, "错误", "无法读取图片",
                    icon=QMessageBox.Icon.Warning,
                    buttons=QMessageBox.StandardButton.Ok)
                box.exec()
                return
                return
            ok, out = cv2.imencode('.png', img)
            if ok:
                out.tofile(self._filepath)
                # 刷新预览
                px = self._load_pixmap(self._filepath)
                if px:
                    scaled = px.scaled(260, 80, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                    self._preview.setPixmap(scaled)
                self.template_changed.emit()
        except Exception as e:
            box = _styled_msg_box(self, "替换失败", str(e),
                icon=QMessageBox.Icon.Warning,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()

    def _on_toggle_enabled(self):
        """切换启用/禁用状态"""
        if not self._name:
            return
        is_disabled = self._detector.is_template_disabled(self._name)
        self._detector.set_template_enabled(self._name, is_disabled)
        self._update_status_display()
        self.template_changed.emit()

    def _update_status_display(self):
        is_disabled = self._detector.is_template_disabled(self._name)
        if is_disabled:
            self._info_status_tag.setText("已禁用")
            self._info_status_tag.setStyleSheet(_tag_style(Colors.DANGER))
            self._btn_toggle_detail.setText("启用")
        else:
            self._info_status_tag.setText("已启用")
            self._info_status_tag.setStyleSheet(_tag_style(Colors.SUCCESS))
            self._btn_toggle_detail.setText("禁用")

    def _on_delete(self):
        """删除当前模板"""
        if not self._name:
            return
        box = _styled_msg_box(self, "删除模板",
            f"确定删除「{self._name}」？此操作不可撤销。",
            icon=QMessageBox.Icon.Warning)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        if os.path.exists(self._filepath):
            os.remove(self._filepath)
            self._detector.set_template_enabled(self._name, True)
            self.template_changed.emit()
            self.closed.emit()

    # ── 工具方法 ──

    @staticmethod
    def _load_pixmap(fp: str) -> QPixmap | None:
        try:
            buf = np.fromfile(fp, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                return None
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            data = rgb.tobytes()
            qimg = QImage(data, w, h, ch * w, QImage.Format.Format_RGB888)
            return QPixmap.fromImage(qimg)
        except Exception:
            return None


# ── 保存模板对话框 ──────────────────────────────────────────


class SaveTemplateDialog(QDialog):
    """保存模板对话框：选类型 → 自动加前缀 → 输入名称"""

    def __init__(self, crop_size: tuple[int, int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存模板")
        self.setMinimumWidth(380)
        self._result_name: str | None = None
        self._build(crop_size)

    def _build(self, size: tuple[int, int]):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {Colors.CARD_BG};
            }}
            QLabel {{ color: {Colors.TEXT}; font-size:13px; }}
            QComboBox, QLineEdit {{
                background-color: {Colors.WINDOW_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 6px 10px; color: {Colors.TEXT};
                min-height: 22px;
            }}
            QComboBox:focus, QLineEdit:focus {{
                border-color: {Colors.BORDER_FOCUS};
            }}
        """)

        form = QFormLayout(self)
        form.setSpacing(12)
        form.setContentsMargins(20, 20, 20, 16)

        # 选区大小
        size_lbl = QLabel(f"{size[0]} x {size[1]} px")
        size_lbl.setStyleSheet(f"color:{Colors.TEXT_SECONDARY}; font-size:12px;")
        form.addRow("选区大小:", size_lbl)

        # 类型选择
        self._type_combo = QComboBox()
        for prefix, label in _PREFIX_LABELS.items():
            cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")
            cat_color = _CAT_COLORS.get(cat, "#AEAEB2")
            self._type_combo.addItem(label, prefix)
        self._type_combo.currentIndexChanged.connect(self._update_preview)
        form.addRow("模板类型:", self._type_combo)

        # 名称（不含前缀）
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("输入名称（如 harvest、weed、mature）")
        self._name_edit.textChanged.connect(self._update_preview)
        form.addRow("名称:", self._name_edit)

        # 完整文件名预览
        self._preview = QLabel()
        self._preview.setStyleSheet(
            f"font-weight:600; font-size:13px; color:{Colors.PRIMARY}; padding:4px 0;"
        )
        form.addRow("文件名:", self._preview)
        self._update_preview()

        # 按钮
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        for b in btns.buttons():
            b.setStyleSheet(_icon_button(Colors.PRIMARY, Colors.PRIMARY_HOVER)
                            if b == btns.button(QDialogButtonBox.StandardButton.Save)
                            else _outline_button())
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _update_preview(self):
        prefix = self._type_combo.currentData() or "btn"
        name = self._name_edit.text().strip()
        self._preview.setText(f"{prefix}_{name}.png" if name else f"{prefix}_.png")

    def _on_accept(self):
        name = self._name_edit.text().strip()
        if not name:
            self._name_edit.setStyleSheet(
                self._name_edit.styleSheet().replace(
                    f"border: 1px solid {Colors.BORDER}",
                    f"border: 2px solid {Colors.DANGER}"
                )
            )
            return
        prefix = self._type_combo.currentData()
        self._result_name = f"{prefix}_{name}"
        self.accept()

    def get_name(self) -> str | None:
        return self._result_name


# ── 截屏采集 Worker ────────────────────────────────────────


class CaptureWorker(QThread):
    captured = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, keyword: str = "QQ经典农场"):
        super().__init__()
        self._keyword = keyword

    def run(self):
        try:
            from core.window_manager import WindowManager
            from core.screen_capture import ScreenCapture
            wm = WindowManager()
            sc = ScreenCapture()
            window = wm.find_window(self._keyword)
            if not window:
                self.error.emit(f"未找到包含 '{self._keyword}' 的窗口")
                return
            wm.activate_window()
            time.sleep(0.5)
            rect = (window.left, window.top, window.width, window.height)
            image = sc.capture_region(rect)
            if image is None:
                self.error.emit("截屏失败")
                return
            self.captured.emit(image)
        except Exception as e:
            self.error.emit(str(e))


# ── 截屏选择器 ──────────────────────────────────────────────


class ScreenshotSelector(QWidget):
    """截图显示 + 鼠标拖拽框选"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._ox = 0
        self._oy = 0
        self._start: tuple[float, float] | None = None
        self._end: tuple[float, float] | None = None
        self._drawing = False
        self._bgr: np.ndarray | None = None
        self._pil = None
        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, pil_image, bgr: np.ndarray):
        self._pil = pil_image
        self._bgr = bgr
        self._start = self._end = None
        self._drawing = False
        self._build()
        self.update()

    def _build(self):
        if self._pil is None:
            return
        rgb = self._pil.convert("RGB")
        # 必须保持 data 引用，否则 QImage 底层缓冲被 GC 回收后 pixmap 变野指针
        self._img_data = rgb.tobytes("raw", "RGB")
        qimg = QImage(self._img_data, rgb.width, rgb.height,
                      3 * rgb.width, QImage.Format.Format_RGB888)
        if qimg.isNull():
            return
        full = QPixmap.fromImage(qimg)
        self._scale = min(self.width() / rgb.width, self.height() / rgb.height, 1.0)
        self._pixmap = full.scaled(
            int(rgb.width * self._scale), int(rgb.height * self._scale),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._ox = (self.width() - self._pixmap.width()) // 2
        self._oy = (self.height() - self._pixmap.height()) // 2

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._build()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._pixmap:
            p.drawPixmap(self._ox, self._oy, self._pixmap)
            if self._start and self._end:
                p.setPen(QPen(QColor(0, 200, 80), 2, Qt.PenStyle.SolidLine))
                p.setBrush(QBrush(QColor(0, 200, 80, 35)))
                r = self._rect()
                p.drawRect(r)
                # 显示尺寸提示
                w = int(abs(self._end[0] - self._start[0]) / self._scale)
                h = int(abs(self._end[1] - self._start[1]) / self._scale)
                p.setPen(QColor(0, 200, 80))
                p.drawText(r.topRight() + QPoint(6, -4), f"{w}x{h}")
        else:
            p.setPen(QColor(Colors.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "点击「截屏采集」按钮获取游戏画面")
        p.end()

    def _rect(self) -> QRect:
        if not self._start or not self._end:
            return QRect()
        x1, y1 = self._start
        x2, y2 = self._end
        return QRect(int(min(x1, x2)), int(min(y1, y2)),
                     int(abs(x2 - x1)), int(abs(y2 - y1)))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._pixmap:
            self._start = (e.position().x(), e.position().y())
            self._end = self._start
            self._drawing = True
            self.update()

    def mouseMoveEvent(self, e):
        if self._drawing:
            self._end = (e.position().x(), e.position().y())
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drawing:
            self._end = (e.position().x(), e.position().y())
            self._drawing = False
            self.update()

    def get_crop(self) -> tuple | None:
        if not self._start or not self._end or self._bgr is None or not self._pixmap:
            return None
        x1 = (min(self._start[0], self._end[0]) - self._ox) / self._scale
        y1 = (min(self._start[1], self._end[1]) - self._oy) / self._scale
        x2 = (max(self._start[0], self._end[0]) - self._ox) / self._scale
        y2 = (max(self._start[1], self._end[1]) - self._oy) / self._scale
        ox1, oy1, ox2, oy2 = int(x1), int(y1), int(x2), int(y2)
        if ox2 - ox1 < 5 or oy2 - oy1 < 5:
            return None
        oh, ow = self._bgr.shape[:2]
        ox1, oy1 = max(0, ox1), max(0, oy1)
        ox2, oy2 = min(ox2, ow), min(oy2, oh)
        return (self._bgr[oy1:oy2, ox1:ox2].copy(), ox2 - ox1, oy2 - oy1)

    def clear(self):
        self._pixmap = self._bgr = self._pil = None
        self._img_data = None
        self._start = self._end = None
        self.update()


# ── 模板管理面板 ────────────────────────────────────────────

_SORT_MAP = {
    "名称 A-Z": lambda x: x[0],
    "名称 Z-A": lambda x: x[0],      # reversed separately
    "最近修改": lambda x: x[2],
    "最早修改": lambda x: x[2],
    "按类别":   lambda x: (x[0].split("_")[0], x[0]),
}
_FILTER_ALL = "全部类型"


class TemplatePanel(QWidget):
    templates_changed = pyqtSignal()

    def __init__(self, detector: CVDetector, parent=None):
        super().__init__(parent)
        self._detector = detector
        self._cards: list[TemplateCard] = []
        self._worker: CaptureWorker | None = None
        self._items: list[tuple[str, str, float]] = []
        self._selected_name: str = ""
        self._init_ui()
        self._load_templates()

    # ── UI 构建 ────────────────────────────────────────────

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏
        root.addWidget(self._build_toolbar())
        # 筛选栏
        root.addWidget(self._build_filter_bar())
        # 内容
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)

        # ── 左右分栏 ──
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: transparent; width: 0px;
            }}
        """)

        # 左侧：模板列表
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._scroll.setMinimumWidth(260)
        self._list_w = QWidget()
        self._list_w.setStyleSheet("background:transparent;border:none;")
        self._list_lay = QVBoxLayout(self._list_w)
        self._list_lay.setContentsMargins(0, 4, 0, 0)
        self._list_lay.setSpacing(4)
        self._list_lay.addStretch()
        self._scroll.setWidget(self._list_w)
        self._splitter.addWidget(self._scroll)

        # 右侧：详情面板（默认隐藏）
        self._detail = TemplateDetailPanel(self._detector)
        self._detail.closed.connect(self._on_detail_close)
        self._detail.template_changed.connect(self._on_detail_changed)
        self._detail.template_renamed.connect(self._on_detail_renamed)
        self._detail.hide()
        self._splitter.addWidget(self._detail)

        # 初始比例
        self._splitter.setSizes([500, 350])
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)

        self._content_lay.addWidget(self._splitter)

        # 采集页
        self._collector = self._build_collector()
        self._content_lay.addWidget(self._collector)
        self._collector.hide()

        root.addWidget(self._content, 1)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border-bottom: 1px solid {Colors.BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(8)

        self._btn_capture = QPushButton("截屏采集")
        self._btn_capture.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_capture.setStyleSheet(_icon_button(Colors.PRIMARY, Colors.PRIMARY_HOVER))
        self._btn_capture.clicked.connect(self._on_capture)
        lay.addWidget(self._btn_capture)

        self._btn_import = QPushButton("导入")
        self._btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_import.setStyleSheet(_icon_button("#34C759", "#2DA44E"))
        self._btn_import.clicked.connect(self._on_import)
        lay.addWidget(self._btn_import)

        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_refresh.setStyleSheet(_outline_button())
        self._btn_refresh.clicked.connect(self._load_templates)
        lay.addWidget(self._btn_refresh)

        self._btn_cat_thresh = QPushButton("类别阈值")
        self._btn_cat_thresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cat_thresh.setStyleSheet(_outline_button())
        self._btn_cat_thresh.clicked.connect(self._on_category_thresholds)
        lay.addWidget(self._btn_cat_thresh)

        lay.addStretch()

        self._count = QLabel("")
        self._count.setStyleSheet(f"color:{Colors.TEXT_DIM};font-size:12px;")
        lay.addWidget(self._count)

        return bar

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(f"background-color:{Colors.WINDOW_BG};border:none;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 6, 16, 6)
        lay.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索模板...")
        self._search.setFixedHeight(30)
        self._search.setMinimumWidth(140)
        self._search.setMaximumWidth(200)
        self._search.textChanged.connect(self._apply_filters)
        lay.addWidget(self._search)

        lay.addWidget(self._make_label("类型"))
        self._filter = QComboBox()
        self._filter.setFixedHeight(30)
        self._filter.setMinimumWidth(130)
        self._fill_filter()
        self._filter.currentIndexChanged.connect(self._apply_filters)
        lay.addWidget(self._filter)

        lay.addWidget(self._make_label("排序"))
        self._sort = QComboBox()
        self._sort.setFixedHeight(30)
        self._sort.setMinimumWidth(110)
        self._sort.addItems(list(_SORT_MAP.keys()))
        self._sort.setCurrentIndex(2)  # 默认"最近修改"
        self._sort.currentIndexChanged.connect(self._apply_filters)
        lay.addWidget(self._sort)

        lay.addStretch()
        return bar

    @staticmethod
    def _make_label(text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(f"color:{Colors.TEXT_SECONDARY};font-size:12px;background:transparent;")
        return l

    def _fill_filter(self):
        self._filter.clear()
        self._filter.addItem(_FILTER_ALL)
        for prefix, cat in TEMPLATE_CATEGORIES.items():
            color = _CAT_COLORS.get(cat, "#AEAEB2")
            label = _CAT_LABELS.get(cat, cat)
            self._filter.addItem(f"{prefix}_  {label}")

    def _build_collector(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;border:none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 工具栏
        tb = QFrame()
        tb.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border-bottom: 1px solid {Colors.BORDER};
            }}
        """)
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(16, 8, 16, 8)
        tbl.setSpacing(8)

        btn_retake = QPushButton("重新截屏")
        btn_retake.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_retake.setStyleSheet(_outline_button())
        btn_retake.clicked.connect(self._on_capture)
        tbl.addWidget(btn_retake)

        btn_save = QPushButton("保存选区")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setStyleSheet(_icon_button(Colors.SUCCESS, "#2DA44E"))
        btn_save.clicked.connect(self._on_save_crop)
        tbl.addWidget(btn_save)

        btn_back = QPushButton("返回列表")
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet(_outline_button())
        btn_back.clicked.connect(self._show_list)
        tbl.addWidget(btn_back)

        tbl.addStretch()

        self._ct_hint = QLabel("鼠标拖拽框选要采集的区域")
        self._ct_hint.setStyleSheet(f"color:{Colors.TEXT_DIM};font-size:12px;")
        tbl.addWidget(self._ct_hint)

        lay.addWidget(tb)

        # 选择器
        self._selector = ScreenshotSelector()
        self._selector.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(0,0,0,6);
                border: none;
            }}
        """)
        lay.addWidget(self._selector, 1)
        return w

    # ── 列表管理 ───────────────────────────────────────────

    def _load_templates(self):
        d = self._detector._templates_dir
        if not os.path.exists(d):
            self._items = []
            self._apply_filters()
            return
        items = []
        for fn in os.listdir(d):
            if not fn.lower().endswith(('.png', '.jpg', '.jpeg')):
                continue
            name = os.path.splitext(fn)[0]
            fp = os.path.join(d, fn)
            items.append((name, fp, os.path.getmtime(fp)))
        self._items = items
        self._apply_filters()

    def _apply_filters(self):
        for c in self._cards:
            self._list_lay.removeWidget(c)
            c.deleteLater()
        self._cards.clear()

        q = self._search.text().strip().lower()
        fi = self._filter.currentIndex()
        sk = self._sort.currentText()

        # 筛选
        out = []
        for name, fp, mt in self._items:
            if q and q not in name.lower():
                continue
            if fi > 0:
                # _filter index 1..N maps to TEMPLATE_CATEGORIES items (ordered dict)
                prefixes = list(TEMPLATE_CATEGORIES.keys())
                if fi - 1 < len(prefixes):
                    want = prefixes[fi - 1]
                    if name.split("_")[0] != want:
                        continue
            out.append((name, fp, mt))

        # 排序
        key_fn = _SORT_MAP.get(sk, lambda x: x[0])
        reverse = sk in ("名称 Z-A", "最近修改")
        out.sort(key=key_fn, reverse=reverse)

        dis = self._detector.get_disabled_templates()
        for name, fp, mt in out:
            card = TemplateCard(name, fp, name in dis, mt)
            card.toggle_requested.connect(self._on_toggle)
            card.delete_requested.connect(self._on_delete)
            card.clicked.connect(self._on_card_clicked)
            if name == self._selected_name:
                card.set_selected(True)
            self._list_lay.insertWidget(self._list_lay.count() - 1, card)
            self._cards.append(card)

        total = len(self._items)
        shown = len(out)
        en = sum(1 for n, _, _ in out if n not in dis)
        if shown == total:
            self._count.setText(f"共 {total} 个，{en} 启用")
        else:
            self._count.setText(f"{shown}/{total} 个，{en} 启用")

    def _on_toggle(self, name: str, enabled: bool):
        self._detector.set_template_enabled(name, enabled)
        self._apply_filters()
        self.templates_changed.emit()

    def _on_card_clicked(self, name: str):
        """点击模板卡片，显示/更新右侧详情面板"""
        if self._selected_name == name:
            # 再次点击同一卡片 → 关闭详情
            self._on_detail_close()
            return
        self._selected_name = name
        # 更新卡片选中状态
        for card in self._cards:
            card.set_selected(card.name == name)
        # 查找文件路径
        fp = ""
        for n, f, _ in self._items:
            if n == name:
                fp = f
                break
        if fp:
            self._detail.load_template(name, fp)
            # 调整 splitter 让详情面板可见
            if not self._detail.isVisible():
                self._detail.show()
                self._splitter.setSizes([400, 350])

    def _on_detail_close(self):
        """关闭详情面板"""
        self._selected_name = ""
        self._detail.clear()
        for card in self._cards:
            card.set_selected(False)

    def _on_detail_changed(self):
        """详情面板编辑后刷新列表"""
        self._load_templates()
        self.templates_changed.emit()

    def _on_detail_renamed(self, old_name: str, new_name: str):
        """模板重命名后刷新"""
        self._selected_name = new_name
        self._load_templates()
        self.templates_changed.emit()

    def _on_delete(self, name: str):
        box = _styled_msg_box(self, "删除模板",
            f"确定删除「{name}」？此操作不可撤销。")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        fp = os.path.join(self._detector._templates_dir, f"{name}.png")
        if not os.path.exists(fp):
            fp = os.path.join(self._detector._templates_dir, f"{name}.jpg")
        if os.path.exists(fp):
            os.remove(fp)
            self._detector.set_template_enabled(name, True)
            if self._selected_name == name:
                self._on_detail_close()
            self._load_templates()
            self.templates_changed.emit()

    def _on_import(self):
        from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择模板图片", "",
            "图片 (*.png *.jpg *.jpeg);;所有文件 (*)")
        if not files:
            return
        n = 0
        for fp in files:
            fn = os.path.basename(fp)
            name, _ = os.path.splitext(fn)
            prefix = name.split("_")[0]
            if prefix not in TEMPLATE_CATEGORIES:
                dlg = SaveTemplateDialog((0, 0), self)
                dlg.setWindowTitle("命名导入模板")
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    continue
                name = dlg.get_name()
                if not name:
                    continue
            dest = os.path.join(self._detector._templates_dir, f"{name}.png")
            if os.path.exists(dest):
                box = _styled_msg_box(self, "覆盖",
                    f"「{name}」已存在，是否覆盖？")
                if box.exec() != QMessageBox.StandardButton.Yes:
                    continue
            buf = np.fromfile(fp, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
            if img is not None:
                ok, out = cv2.imencode('.png', img)
                if ok:
                    out.tofile(dest)
                    n += 1
        if n:
            self._load_templates()
            self.templates_changed.emit()

    # ── 类别默认阈值 ───────────────────────────────────────

    def _on_category_thresholds(self):
        """打开类别默认阈值设置对话框"""
        from PyQt6.QtWidgets import QSlider, QFormLayout, QSpinBox, QDoubleSpinBox
        from PyQt6.QtCore import Qt
        from gui.styles import Colors

        cat_map = self._detector.get_category_defaults()
        if not cat_map:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("类别默认阈值设置")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: {Colors.CARD_BG}; color: {Colors.TEXT};
            }}
            QDialog QLabel {{
                color: {Colors.TEXT}; background: transparent;
            }}
            QDialog QSlider {{
                min-height: 22px;
            }}
            QDialog QDoubleSpinBox {{
                background-color: {Colors.WINDOW_BG}; color: {Colors.TEXT};
                border: 1px solid rgba(0,0,0,25); border-radius: 6px;
                padding: 4px 8px; min-width: 70px;
            }}
            QDialog QPushButton {{
                background-color: {Colors.CARD_BG}; color: {Colors.TEXT};
                border: 1px solid rgba(0,0,0,25); border-radius: 6px;
                padding: 6px 20px; min-width: 80px;
            }}
            QDialog QPushButton:hover {{
                background-color: rgba(0,0,0,6);
            }}
            QDialog QDialogButtonBox {{
                background-color: {Colors.CARD_BG};
            }}
        """)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        hint = QLabel("调整每个类别的默认匹配阈值。未单独设置阈值的模板将使用其类别的默认值。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{Colors.TEXT_SECONDARY}; font-size:12px; background:transparent;")
        layout.addWidget(hint)

        # 滚动区域容纳所有类别
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        container = QWidget()
        container.setStyleSheet("background:transparent;")
        form = QVBoxLayout(container)
        form.setSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)

        sliders: dict[str, QSlider] = {}
        spinboxes: dict[str, QDoubleSpinBox] = {}

        builtin = CVDetector._BUILTIN_CATEGORY_DEFAULTS
        for cat, default_val in cat_map.items():
            cat_color = _CAT_COLORS.get(cat, "#AEAEB2")
            cat_label = _CAT_LABELS.get(cat, cat)
            builtin_val = builtin.get(cat, 0.8)

            row = QHBoxLayout()
            row.setSpacing(8)

            # 类别标签（带颜色圆点）
            tag = QLabel(f"● {cat_label}")
            tag.setFixedWidth(100)
            tag.setStyleSheet(
                f"color:{cat_color}; font-weight:600; font-size:12px; background:transparent;")

            # 滑块
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(10, 100)
            slider.setValue(int(default_val * 100))
            slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    height: 4px; background: rgba(0,0,0,12); border-radius: 2px;
                }}
                QSlider::handle:horizontal {{
                    width: 16px; height: 16px; margin: -6px 0;
                    background: {cat_color}; border-radius: 8px;
                }}
                QSlider::sub-page:horizontal {{
                    background: {cat_color}; border-radius: 2px;
                }}
            """)

            # 数值显示
            spin = QDoubleSpinBox()
            spin.setRange(0.1, 1.0)
            spin.setSingleStep(0.01)
            spin.setDecimals(2)
            spin.setValue(default_val)
            spin.setFixedWidth(72)

            # 内置默认提示
            builtin_lbl = QLabel(f"(内置 {builtin_val:.1f})")
            builtin_lbl.setStyleSheet(
                f"color:{Colors.TEXT_DIM}; font-size:10px; background:transparent;")
            builtin_lbl.setFixedWidth(65)

            # 双向绑定
            def _slider_changed(val, s=spin):
                s.blockSignals(True)
                s.setValue(val / 100.0)
                s.blockSignals(False)

            def _spin_changed(val, s=slider):
                s.blockSignals(True)
                s.setValue(int(val * 100))
                s.blockSignals(False)

            slider.valueChanged.connect(_slider_changed)
            spin.valueChanged.connect(_spin_changed)

            row.addWidget(tag)
            row.addWidget(slider, 1)
            row.addWidget(spin)
            row.addWidget(builtin_lbl)

            sliders[cat] = slider
            spinboxes[cat] = spin
            form.addLayout(row)

        form.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_reset = QPushButton("恢复内置默认")
        btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset.setStyleSheet(_outline_button())

        def _reset_all():
            self._detector.reset_category_defaults()
            for cat, builtin_val in builtin.items():
                if cat in sliders:
                    sliders[cat].blockSignals(True)
                    spinboxes[cat].blockSignals(True)
                    sliders[cat].setValue(int(builtin_val * 100))
                    spinboxes[cat].setValue(builtin_val)
                    sliders[cat].blockSignals(False)
                    spinboxes[cat].blockSignals(False)

        btn_reset.clicked.connect(_reset_all)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.setStyleSheet(f"background-color:{Colors.CARD_BG};")
        btn_row.addWidget(btn_box)
        layout.addLayout(btn_row)

        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            for cat, spin in spinboxes.items():
                self._detector.set_category_default(cat, spin.value())
            self._load_templates()

    # ── 采集 ───────────────────────────────────────────────

    def _on_capture(self):
        self._btn_capture.setEnabled(False)
        self._worker = CaptureWorker("QQ经典农场")
        self._worker.captured.connect(self._on_captured)
        self._worker.error.connect(self._on_cap_err)
        self._worker.finished.connect(self._clean_worker)
        self._worker.start()

    def _clean_worker(self):
        if self._worker:
            self._worker.deleteLater()
            self._worker = None

    def _on_captured(self, pil_image):
        self._btn_capture.setEnabled(True)
        rgb = np.array(pil_image.convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        self._selector.set_image(pil_image, bgr)
        self._show_collector()
        self._ct_hint.setText("鼠标拖拽框选要采集的区域")

    def _on_cap_err(self, msg: str):
        self._btn_capture.setEnabled(True)
        box = _styled_msg_box(self, "截屏失败", msg,
            icon=QMessageBox.Icon.Warning,
            buttons=QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_save_crop(self):
        result = self._selector.get_crop()
        if not result:
            box = _styled_msg_box(self, "提示", "请先用鼠标框选一个区域",
                icon=QMessageBox.Icon.Information,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()
            return
        crop_bgr, w, h = result

        dlg = SaveTemplateDialog((w, h), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.get_name()
        if not name:
            return

        fp = os.path.join(self._detector._templates_dir, f"{name}.png")
        if os.path.exists(fp):
            box = _styled_msg_box(self, "覆盖",
                f"「{name}」已存在，是否覆盖？")
            if box.exec() != QMessageBox.StandardButton.Yes:
                return

        ok, buf = cv2.imencode('.png', crop_bgr)
        if ok:
            buf.tofile(fp)
            self._ct_hint.setText(f"已保存 {name}.png ({w}x{h})")
            self._load_templates()
            self.templates_changed.emit()
        else:
            box = _styled_msg_box(self, "保存失败", "无法编码图片",
                icon=QMessageBox.Icon.Warning,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()

    def _show_collector(self):
        self._splitter.hide()
        self._collector.show()

    def _show_list(self):
        self._collector.hide()
        self._splitter.show()
        self._selector.clear()

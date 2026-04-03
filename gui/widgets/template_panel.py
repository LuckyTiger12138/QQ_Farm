"""模板管理面板 — 模板列表 + 启用/禁用 + 内置采集器"""
import os
import time
import numpy as np
import cv2

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QFrame, QCheckBox, QLineEdit, QFileDialog,
    QSizePolicy, QComboBox, QDialog, QFormLayout, QDialogButtonBox,
    QSpacerItem, QSizePolicy as QSP,
)
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint, QThread
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


# ── 模板卡片 ────────────────────────────────────────────────


class TemplateCard(QFrame):
    """单个模板卡片：缩略图 + 名称 + 类别标签 + 时间 + 开关"""

    toggle_requested = pyqtSignal(str, bool)
    delete_requested = pyqtSignal(str)

    def __init__(self, name: str, filepath: str, disabled: bool,
                 mtime: float, parent=None):
        super().__init__(parent)
        self._name = name
        self._filepath = filepath
        self._enabled = not disabled
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        self._apply_style()
        self._build(name, filepath, disabled, mtime)

    def _apply_style(self):
        if self._enabled:
            bg, border = Colors.CARD_BG, Colors.BORDER
        else:
            bg, border = "#fafafa", "rgba(0,0,0,6)"
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg}; border: 1px solid {border};
                border-radius: 10px;
            }}
            QFrame:hover {{
                border-color: rgba(0,122,255,40);
            }}
        """)

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

        # 列表页
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._list_w = QWidget()
        self._list_w.setStyleSheet("background:transparent;border:none;")
        self._list_lay = QVBoxLayout(self._list_w)
        self._list_lay.setContentsMargins(0, 4, 0, 0)
        self._list_lay.setSpacing(4)
        self._list_lay.addStretch()
        self._scroll.setWidget(self._list_w)
        self._content_lay.addWidget(self._scroll)

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

    def _on_delete(self, name: str):
        from PyQt6.QtWidgets import QMessageBox
        r = QMessageBox.question(
            self, "删除模板", f"确定删除「{name}」？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        fp = os.path.join(self._detector._templates_dir, f"{name}.png")
        if not os.path.exists(fp):
            fp = os.path.join(self._detector._templates_dir, f"{name}.jpg")
        if os.path.exists(fp):
            os.remove(fp)
            self._detector.set_template_enabled(name, True)
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
                r = QMessageBox.question(
                    self, "覆盖", f"「{name}」已存在，是否覆盖？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r == QMessageBox.StandardButton.No:
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
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "截屏失败", msg)

    def _on_save_crop(self):
        from PyQt6.QtWidgets import QMessageBox
        result = self._selector.get_crop()
        if not result:
            QMessageBox.information(self, "提示", "请先用鼠标框选一个区域")
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
            r = QMessageBox.question(
                self, "覆盖", f"「{name}」已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return

        ok, buf = cv2.imencode('.png', crop_bgr)
        if ok:
            buf.tofile(fp)
            self._ct_hint.setText(f"已保存 {name}.png ({w}x{h})")
            self._load_templates()
            self.templates_changed.emit()
        else:
            QMessageBox.warning(self, "保存失败", "无法编码图片")

    def _show_collector(self):
        self._scroll.hide()
        self._collector.show()

    def _show_list(self):
        self._collector.hide()
        self._scroll.show()
        self._selector.clear()

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
from core.cv_detector import CVDetector, TEMPLATE_CATEGORIES, DetectResult
from loguru import logger


# ── 可点击的 QLabel ──────────────────────────────────────────

class ClickableLabel(QLabel):
    """支持点击事件的 QLabel"""
    clicked = pyqtSignal()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


# ── 图片放大预览对话框 ──────────────────────────────────────

class ImagePreviewDialog(QDialog):
    """全屏/大窗口图片预览对话框（带匹配结果列表）"""

    def __init__(self, bgr_image: np.ndarray, title: str = "预览",
                 match_results: list[DetectResult] | None = None, parent=None):
        super().__init__(parent)
        self._bgr = bgr_image
        self._results = match_results or []
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumSize(900, 600)
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 主分割器：左预览 右列表
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧预览区
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet(f"font-size:12px; color:{Colors.TEXT_DIM}; background:transparent;")
        toolbar.addWidget(self._zoom_label)
        toolbar.addStretch()
        close_btn = QPushButton("✕ 关闭")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                color: {Colors.TEXT_DIM}; font-size: 13px;
                padding: 4px 12px; border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: rgba(0,0,0,8);
                color: {Colors.TEXT};
            }}
        """)
        close_btn.clicked.connect(self.close)
        toolbar.addWidget(close_btn)
        left_lay.addLayout(toolbar)

        self._viewer = QLabel()
        self._viewer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._viewer.setStyleSheet("background-color: #1e1e1e;")
        self._viewer.setCursor(Qt.CursorShape.OpenHandCursor)
        left_lay.addWidget(self._viewer, 1)

        splitter.addWidget(left_widget)

        # 右侧匹配列表
        right_widget = QWidget()
        right_widget.setFixedWidth(280)
        right_widget.setStyleSheet(f"background-color:{Colors.CARD_BG}; border-left:1px solid {Colors.BORDER};")
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        list_title = QLabel("匹配结果")
        list_title.setStyleSheet(f"font-weight:700; font-size:13px; color:{Colors.TEXT}; padding:10px 12px 6px;")
        right_lay.addWidget(list_title)

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._list_widget = QWidget()
        self._list_inner = QVBoxLayout(self._list_widget)
        self._list_inner.setContentsMargins(8, 4, 8, 8)
        self._list_inner.setSpacing(4)
        self._list_inner.addStretch()
        self._list_scroll.setWidget(self._list_widget)
        right_lay.addWidget(self._list_scroll, 1)

        splitter.addWidget(right_widget)
        splitter.setSizes([620, 280])
        root.addWidget(splitter, 1)

        # 标注图片（带编号）
        annotated = self._draw_numbered_boxes(self._bgr, self._results)
        self._build_result_list()
        self._set_image(annotated)

    def _build_result_list(self):
        """构建右侧匹配结果列表"""
        # 清除旧条目
        while self._list_inner.count() > 1:
            item = self._list_inner.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._results:
            hint = QLabel("无匹配结果")
            hint.setStyleSheet(f"color:{Colors.TEXT_DIM}; font-size:12px; padding:8px;")
            self._list_inner.insertWidget(0, hint)
            return

        # 按置信度排序
        sorted_results = sorted(self._results, key=lambda r: r.confidence, reverse=True)

        for i, res in enumerate(sorted_results):
            card = self._make_result_card(res, i + 1)
            self._list_inner.insertWidget(i, card)

    def _draw_numbered_boxes(self, bgr: np.ndarray, results: list) -> np.ndarray:
        """在图片上画带编号的矩形框"""
        output = bgr.copy()

        # 类别颜色映射
        cat_colors = {
            "button": (0, 200, 255),
            "status_icon": (0, 100, 255),
            "crop": (0, 255, 100),
            "ui_element": (255, 255, 0),
            "land": (180, 180, 180),
            "seed": (255, 50, 255),
            "shop": (0, 200, 200),
            "warehouse_seed": (130, 159, 255),
            "unknown": (0, 0, 255),
        }

        # 按置信度排序，保证编号一致
        sorted_results = sorted(results, key=lambda r: r.confidence, reverse=True)

        for idx, res in enumerate(sorted_results):
            num = idx + 1
            color = cat_colors.get(res.category, (0, 0, 255))
            x1, y1, x2, y2 = res.bbox

            # 半透明填充
            overlay = output.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            cv2.addWeighted(overlay, 0.2, output, 0.8, 0, output)

            # 边框
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 3)

            # 编号圆形徽章（左上角）
            cx, cy = x1 + 18, y1 + 18
            radius = 14
            cv2.circle(output, (cx, cy), radius, color, -1)
            cv2.circle(output, (cx, cy), radius, (255, 255, 255), 2)
            cv2.putText(output, str(num), (cx - 6, cy + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)

        return output

    def _make_result_card(self, res: 'DetectResult', index: int) -> QFrame:
        """创建单条匹配结果卡片（带模板缩略图）"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.WINDOW_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 6px 8px;
            }}
        """)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(8)

        # 左侧编号徽章
        badge = QLabel(str(index))
        badge.setFixedSize(24, 24)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cat_colors = {
            "button": "#00C8FF", "status_icon": "#0064FF", "crop": "#00FF64",
            "ui_element": "#FFFF00", "land": "#B4B4B4", "seed": "#FF32FF",
            "shop": "#00C8C8", "warehouse_seed": "#829FFF", "unknown": "#FF0000",
        }
        badge_color = cat_colors.get(res.category, "#FF0000")
        badge.setStyleSheet(f"""
            QLabel {{
                background-color: {badge_color};
                color: white; font-weight: 700; font-size: 13px;
                border-radius: 12px;
            }}
        """)
        lay.addWidget(badge)

        # 模板缩略图
        thumb = QLabel()
        thumb.setFixedSize(40, 40)
        thumb.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0,0,0,6);
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
            }}
        """)
        px = self._load_thumb(res.name)
        if px:
            thumb.setPixmap(px.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(thumb)

        # 右侧信息
        info_lay = QVBoxLayout()
        info_lay.setSpacing(2)

        # 模板名称
        name_lbl = QLabel(res.name)
        name_lbl.setStyleSheet(f"font-weight:600; font-size:12px; color:{Colors.TEXT};")
        info_lay.addWidget(name_lbl)

        # 置信度
        conf_lbl = QLabel(f"{res.confidence:.2%}")
        conf_color = "#34C759" if res.confidence >= 0.8 else "#FF9500" if res.confidence >= 0.65 else Colors.DANGER
        conf_lbl.setStyleSheet(f"font-size:11px; color:{conf_color}; font-weight:600;")
        info_lay.addWidget(conf_lbl)

        # 坐标
        pos_lbl = QLabel(f"({res.x}, {res.y})  {res.w}×{res.h}")
        pos_lbl.setStyleSheet(f"font-size:10px; color:{Colors.TEXT_DIM};")
        info_lay.addWidget(pos_lbl)

        info_lay.addStretch()
        lay.addLayout(info_lay, 1)

        return card

    def _load_thumb(self, name: str) -> QPixmap | None:
        """加载模板缩略图"""
        try:
            fp = os.path.join("templates", f"{name}.png")
            if not os.path.exists(fp):
                return None
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

    def _set_image(self, bgr: np.ndarray):
        if bgr is None:
            return
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        data = rgb.tobytes()
        qimg = QImage(data, w, h, ch * w, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)
        self._fit_to_view()

    def _fit_to_view(self):
        if not hasattr(self, '_pixmap') or self._pixmap.isNull():
            return
        vw = self._viewer.width() - 20
        vh = self._viewer.height() - 20
        if vw <= 0 or vh <= 0:
            return
        scaled = self._pixmap.scaled(vw, vh, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        self._viewer.setPixmap(scaled)
        scale_pct = scaled.width() / self._pixmap.width() * 100
        self._zoom_label.setText(f"{scale_pct:.0f}%")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_to_view()


# ── 裁切画布（支持鼠标拖拽矩形框选） ──────────────────────────

class CropCanvas(QLabel):
    """可交互的裁切画布：鼠标拖拽绘制矩形选区"""

    rect_changed = pyqtSignal(int, int, int, int)  # x, y, w, h（原始图坐标）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._original: np.ndarray | None = None   # 原始 BGRA 图像
        self._display_px: QPixmap | None = None     # 缩放后的显示 QPixmap
        self._scale = 1.0                           # 缩放比例
        self._dragging = False
        self._start_pos: QPoint | None = None
        self._end_pos: QPoint | None = None
        self._crop_rect: tuple[int, int, int, int] | None = None  # (x, y, w, h) 原始坐标
        self.setMinimumSize(400, 300)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0,0,0,6);
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)

    def set_image(self, bgra: np.ndarray):
        """加载图像并渲染"""
        self._original = bgra.copy()
        self._crop_rect = None
        self._draw_rect = None
        h, w = bgra.shape[:2]

        # 创建棋盘格 + alpha 混合的显示图
        checker = np.zeros((h, w, 3), dtype=np.uint8)
        tile = 10
        for y in range(h):
            for x in range(w):
                if ((x // tile) + (y // tile)) % 2 == 0:
                    checker[y, x] = (240, 240, 240)
                else:
                    checker[y, x] = (200, 200, 200)
        if bgra.shape[2] == 4:
            alpha = bgra[:, :, 3:4].astype(np.float32) / 255.0
            bgr = bgra[:, :, :3].astype(np.float32)
            blended = (bgr * alpha + checker.astype(np.float32) * (1 - alpha)).astype(np.uint8)
        else:
            blended = bgra[:, :, :3]

        rgb = cv2.cvtColor(blended, cv2.COLOR_BGR2RGB)
        dh, dw, ch = rgb.shape
        data = rgb.tobytes()
        qimg = QImage(data, dw, dh, ch * dw, QImage.Format.Format_RGB888)
        px = QPixmap.fromImage(qimg)

        # 自适应缩放
        max_w, max_h = 600, 400
        self._scale = min(max_w / w, max_h / h, 1.0)
        scaled = px.scaled(int(w * self._scale), int(h * self._scale),
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._display_px = scaled
        self.setPixmap(scaled)
        self.setMinimumSize(scaled.size())
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._original is not None:
            self._dragging = True
            self._start_pos = e.pos()
            self._end_pos = e.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._end_pos = e.pos()
            self._update_crop_rect()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._end_pos = e.pos()
            self._update_crop_rect()
        super().mouseReleaseEvent(e)

    def _update_crop_rect(self):
        """计算裁切矩形（映射回原始图坐标）"""
        if self._start_pos is None or self._end_pos is None:
            return

        # 获取图片在 QLabel 中的偏移（因为 AlignCenter）
        px_w = self._display_px.width() if self._display_px else 0
        px_h = self._display_px.height() if self._display_px else 0
        off_x = (self.width() - px_w) // 2
        off_y = (self.height() - px_h) // 2

        # 转为画布内坐标
        x1 = self._start_pos.x() - off_x
        y1 = self._start_pos.y() - off_y
        x2 = self._end_pos.x() - off_x
        y2 = self._end_pos.y() - off_y

        # 映射回原始图坐标
        ox1 = int(x1 / self._scale)
        oy1 = int(y1 / self._scale)
        ox2 = int(x2 / self._scale)
        oy2 = int(y2 / self._scale)

        # 规范化
        cx = max(0, min(ox1, ox2))
        cy = max(0, min(oy1, oy2))
        h, w = self._original.shape[:2]
        cw = min(max(abs(ox2 - ox1), 1), w - cx)
        ch = min(max(abs(oy2 - oy1), 1), h - cy)

        self._crop_rect = (cx, cy, cw, ch)
        self.rect_changed.emit(cx, cy, cw, ch)
        self._redraw()

    def _redraw(self):
        """重绘：显示图 + 矩形框"""
        if self._display_px is None:
            return
        overlay = QPixmap(self._display_px)
        if self._crop_rect:
            cx, cy, cw, ch = self._crop_rect
            # 映射到显示坐标
            dx = int(cx * self._scale)
            dy = int(cy * self._scale)
            dw = int(cw * self._scale)
            dh = int(ch * self._scale)

            painter = QPainter(overlay)
            painter.setPen(QPen(QColor("#007AFF"), 2, Qt.PenStyle.SolidLine))
            painter.setBrush(QBrush(QColor(0, 122, 255, 30)))
            painter.drawRect(dx, dy, dw, dh)
            painter.end()

        self.setPixmap(overlay)

    def get_crop(self) -> np.ndarray | None:
        """获取裁切后的 BGRA 图像"""
        if self._original is None or self._crop_rect is None:
            return None
        cx, cy, cw, ch = self._crop_rect
        return self._original[cy:cy+ch, cx:cx+cw].copy()

    def reset(self):
        """重置裁切"""
        self._crop_rect = None
        if self._original is not None:
            self._redraw()


# ── 裁切对话框 ────────────────────────────────────────────────

class CropDialog(QDialog):
    """模板裁切对话框：鼠标拖拽矩形框选裁切"""

    def __init__(self, name: str, filepath: str, parent=None):
        super().__init__(parent)
        self._name = name
        self._filepath = filepath
        self._original_bgra: np.ndarray | None = None
        self._init_ui()
        self._load_image()

    def _init_ui(self):
        self.setWindowTitle(f"裁切模板: {self._name}")
        self.setModal(True)
        self.setMinimumWidth(650)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {Colors.CARD_BG};
                color: {Colors.TEXT};
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 标题
        title = QLabel(f"✂ 裁切: {self._name}")
        title.setStyleSheet(f"font-weight:700; font-size:16px; color:{Colors.TEXT}; background:transparent;")
        root.addWidget(title)

        # 裁切画布
        self._canvas = CropCanvas()
        self._canvas.rect_changed.connect(self._on_rect_changed)
        root.addWidget(self._canvas)

        # 信息行
        self._info_label = QLabel("鼠标拖拽框选要保留的区域")
        self._info_label.setStyleSheet(f"font-size:12px; color:{Colors.TEXT_DIM}; background:transparent;")
        root.addWidget(self._info_label)

        # 快捷按钮行
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)

        self._btn_auto_crop = QPushButton("自动裁切透明边")
        self._btn_auto_crop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_auto_crop.setFixedHeight(28)
        self._btn_auto_crop.setStyleSheet(_outline_button("#34C759"))
        self._btn_auto_crop.clicked.connect(self._auto_crop_transparent)
        quick_row.addWidget(self._btn_auto_crop)

        self._btn_reset = QPushButton("重置选区")
        self._btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset.setFixedHeight(28)
        self._btn_reset.setStyleSheet(_outline_button())
        self._btn_reset.clicked.connect(self._reset_crop)
        quick_row.addWidget(self._btn_reset)

        quick_row.addStretch()
        root.addLayout(quick_row)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.setFixedHeight(32)
        self._btn_cancel.setStyleSheet(_outline_button())
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)

        self._btn_save = QPushButton("保存")
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.setFixedHeight(32)
        self._btn_save.setStyleSheet(_icon_button(Colors.PRIMARY, Colors.PRIMARY_HOVER))
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)

        root.addLayout(btn_row)

    def _load_image(self):
        """加载模板图片"""
        try:
            buf = np.fromfile(self._filepath, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
            if img is None:
                self._info_label.setText("✗ 图片加载失败")
                return

            if len(img.shape) == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
                img[:, :, 3] = 255

            self._original_bgra = img
            h, w = img.shape[:2]
            self._info_label.setText(f"原始尺寸: {w} × {h} px | 鼠标拖拽框选要保留的区域")
            self._canvas.set_image(img)
        except Exception as e:
            self._info_label.setText(f"✗ 加载失败: {e}")
            logger.error(f"裁切对话框加载图片失败: {e}")

    def _on_rect_changed(self, x: int, y: int, w: int, h: int):
        """裁切矩形变化时更新信息"""
        self._info_label.setText(f"选区: ({x}, {y}) {w}×{h} px")

    def _auto_crop_transparent(self):
        """自动裁切透明边缘"""
        if self._original_bgra is None:
            return

        alpha = self._original_bgra[:, :, 3]
        non_transparent = np.where(alpha > 10)
        if len(non_transparent[0]) == 0:
            self._info_label.setText("⚠ 图片完全透明，无法裁切")
            return

        y_min, x_min = non_transparent[0].min(), non_transparent[1].min()
        y_max, x_max = non_transparent[0].max(), non_transparent[1].max()

        h, w = self._original_bgra.shape[:2]
        cx = x_min
        cy = y_min
        cw = x_max - x_min + 1
        ch = y_max - y_min + 1

        # 直接设置裁切矩形
        self._canvas._crop_rect = (cx, cy, cw, ch)
        self._canvas._redraw()
        self._info_label.setText(f"自动裁切: ({cx}, {cy}) {cw}×{ch} px")
        logger.info(f"自动裁切透明边: x={cx}, y={cy}, w={cw}, h={ch}")

    def _reset_crop(self):
        """重置选区"""
        self._canvas.reset()
        h, w = self._original_bgra.shape[:2] if self._original_bgra is not None else (0, 0)
        self._info_label.setText(f"原始尺寸: {w} × {h} px | 鼠标拖拽框选要保留的区域")

    def _on_save(self):
        """保存裁切结果"""
        cropped = self._canvas.get_crop()
        if cropped is None:
            QMessageBox.warning(self, "裁切失败", "请先拖拽框选要保留的区域")
            return

        try:
            ok, buf = cv2.imencode('.png', cropped)
            if not ok:
                QMessageBox.critical(self, "保存失败", "图片编码失败")
                return

            buf.tofile(self._filepath)
            logger.info(f"裁切保存成功: {self._filepath}")
            QMessageBox.information(self, "保存成功", f"模板 '{self._name}' 已裁切并保存")
            self.accept()
        except Exception as e:
            logger.error(f"裁切保存失败: {e}")
            QMessageBox.critical(self, "保存失败", f"保存失败: {e}")


# ── 样式常量 ────────────────────────────────────────────────

_CAT_COLORS: dict[str, str] = {
    "button": "#007AFF",
    "status_icon": "#FF9500",
    "crop": "#34C759",
    "ui_element": "#AF52DE",
    "land": "#8E8E93",
    "seed": "#FF2D55",
    "shop": "#5856D6",
    "warehouse_seed": "#FF9F0A",  # 仓库种子颜色（橙色）
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
    "warehouse_seed": "仓库种子",
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
    "ws": "仓库种子 (ws_)",  # 新增：仓库种子
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
        self.setFixedHeight(68)
        self.setObjectName("templateCard")
        self._apply_style()
        self._build(name, filepath, disabled, mtime)

    def _apply_style(self):
        if self._selected:
            bg = "#ffffff"
            border = Colors.PRIMARY
            shadow = "rgba(0, 122, 255, 15)"
        elif self._enabled:
            bg = Colors.CARD_BG
            border = Colors.BORDER
            shadow = "rgba(0, 0, 0, 6)"
        else:
            bg = "#fafafa"
            border = "rgba(0,0,0,6)"
            shadow = "transparent"
        self.setStyleSheet(f"""
            QFrame#templateCard {{
                background-color: {bg};
                border: 1.5px solid {border};
                border-radius: 12px;
            }}
            QFrame#templateCard:hover {{
                border-color: rgba(0,122,255,30);
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
            if child is not None and isinstance(child, (QCheckBox, QPushButton)):
                super().mousePressEvent(e)
                return
            self.clicked.emit(self._name)
        super().mousePressEvent(e)

    def _build(self, name: str, filepath: str, disabled: bool, mtime: float):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        thumb = QLabel()
        thumb.setFixedSize(46, 46)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0,0,0,5);
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)
        px = self._load_thumb(filepath)
        if px:
            thumb.setPixmap(px.scaled(42, 42, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(thumb)

        info = QVBoxLayout()
        info.setSpacing(4)

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

        if disabled:
            status_tag = QLabel("禁用")
            status_tag.setFixedHeight(18)
            status_tag.setStyleSheet(_tag_style(Colors.DANGER))
            status_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row1.addWidget(status_tag)
        row1.addStretch()
        info.addLayout(row1)

        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        time_lbl = QLabel(ts)
        time_lbl.setStyleSheet(f"font-size:11px; color:{Colors.TEXT_DIM}; background:transparent;")
        info.addWidget(time_lbl)

        lay.addLayout(info, 1)

        self._cb = QCheckBox()
        self._cb.setChecked(not disabled)
        self._cb.setStyleSheet("QCheckBox{spacing:0;}")
        self._cb.stateChanged.connect(self._on_toggle)
        lay.addWidget(self._cb)

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
    closed = pyqtSignal()
    template_changed = pyqtSignal()
    template_renamed = pyqtSignal(str, str)
    capture_replace_requested = pyqtSignal()

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
            }}
        """)
        self.setObjectName("detailPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

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

        edit_row = QHBoxLayout()
        edit_row.setSpacing(6)

        self._btn_crop = QPushButton("✂ 裁切")
        self._btn_crop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_crop.setFixedHeight(26)
        self._btn_crop.setStyleSheet(_outline_button("#FF9500"))
        self._btn_crop.clicked.connect(self._on_crop)
        edit_row.addWidget(self._btn_crop)

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

        self._btn_capture_replace = QPushButton("截屏替换")
        self._btn_capture_replace.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_capture_replace.setFixedHeight(26)
        self._btn_capture_replace.setStyleSheet(_outline_button())
        self._btn_capture_replace.clicked.connect(self._on_capture_replace)
        edit_row.addWidget(self._btn_capture_replace)

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

        sep1 = QFrame()
        sep1.setFixedHeight(1)
        sep1.setStyleSheet(f"background-color:{Colors.BORDER};")
        root.addWidget(sep1)

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

        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background-color:{Colors.BORDER};")
        root.addWidget(sep2)

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
        self._btn_run_test = QPushButton("测试单个")
        self._btn_run_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_run_test.setStyleSheet(_icon_button(Colors.PRIMARY, Colors.PRIMARY_HOVER))
        self._btn_run_test.setEnabled(False)
        self._btn_run_test.clicked.connect(self._on_run_test)
        test_btns.addWidget(self._btn_run_test)
        self._btn_batch_test = QPushButton("批量测试同类别")
        self._btn_batch_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_batch_test.setStyleSheet(_outline_button("#34C759"))
        self._btn_batch_test.setEnabled(False)
        self._btn_batch_test.clicked.connect(self._on_batch_test)
        test_btns.addWidget(self._btn_batch_test)
        root.addLayout(test_btns)

        self._test_info = QLabel()
        self._test_info.setStyleSheet(f"font-size:11px; color:{Colors.TEXT_DIM}; background:transparent;")
        self._test_info.setWordWrap(True)
        root.addWidget(self._test_info)

        # 可点击的测试预览
        self._test_scroll = QScrollArea()
        self._test_scroll.setWidgetResizable(True)
        self._test_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._test_scroll.setMinimumHeight(120)
        self._test_preview = ClickableLabel()
        self._test_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._test_preview.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0,0,0,4);
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
            QLabel:hover {{
                border-color: {Colors.BORDER_FOCUS};
            }}
        """)
        self._test_preview.clicked.connect(self._on_preview_click)
        self._test_scroll.setWidget(self._test_preview)
        root.addWidget(self._test_scroll, 1)

    def load_template(self, name: str, filepath: str):
        self._name = name
        self._filepath = filepath
        self._test_bgr = None
        self._test_bgr_annotated = None
        self._test_bgr_results = []
        self._btn_run_test.setEnabled(False)
        self._test_info.clear()
        self._test_preview.clear()

        px = self._load_pixmap(filepath)
        if px:
            scaled = px.scaled(280, 96, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self._preview.setPixmap(scaled)
        else:
            self._preview.clear()

        self._info_name.setText(name)
        prefix = name.split("_")[0]
        cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")
        cat_color = _CAT_COLORS.get(cat, _CAT_COLORS["unknown"])
        cat_text = _CAT_LABELS.get(cat, cat)
        self._info_cat_tag.setText(cat_text)
        self._info_cat_tag.setStyleSheet(_tag_style(cat_color))

        if px:
            self._info_dims.setText(f"{px.width()} × {px.height()} px")
        else:
            self._info_dims.setText("")

        try:
            fsize = os.path.getsize(filepath)
            if fsize > 1024:
                self._info_filesize.setText(f"文件大小: {fsize / 1024:.1f} KB")
            else:
                self._info_filesize.setText(f"文件大小: {fsize} B")
        except OSError:
            self._info_filesize.setText("")

        is_disabled = self._detector.is_template_disabled(name)
        if is_disabled:
            self._info_status_tag.setText("已禁用")
            self._info_status_tag.setStyleSheet(_tag_style(Colors.DANGER))
            self._btn_toggle_detail.setText("启用")
        else:
            self._info_status_tag.setText("已启用")
            self._info_status_tag.setStyleSheet(_tag_style(Colors.SUCCESS))
            self._btn_toggle_detail.setText("禁用")

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

    def _on_capture_test_image(self):
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
            self._btn_batch_test.setEnabled(True)
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
            self._btn_batch_test.setEnabled(True)

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

        if not self._detector._loaded:
            self._detector.load_templates()

        results = self._detector.detect_single_template(
            self._test_bgr, self._name, threshold
        )

        if not results:
            self._test_info.setText(f"未匹配到结果（阈值 {threshold:.2f}）")
            self._show_test_image(self._test_bgr)
            return

        confs = [r.confidence for r in results]
        self._test_info.setText(
            f"匹配到 {len(results)} 处，"
            f"置信度: {min(confs):.2f} ~ {max(confs):.2f}"
        )

        annotated = self._detector.draw_results(self._test_bgr, results)
        self._show_test_image(annotated)

    def _on_batch_test(self):
        """批量测试同类别所有模板"""
        if self._test_bgr is None or not self._name:
            return

        if not self._detector._loaded:
            self._detector.load_templates()

        # 获取当前模板的类别
        prefix = self._name.split("_")[0]
        cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")

        # 获取该类别所有模板名称
        all_templates = self._detector.get_all_template_names()
        cat_templates = [n for n in all_templates if n.split("_")[0] == prefix]

        if not cat_templates:
            self._test_info.setText(f"类别 {cat} 下无模板")
            return

        # 逐个测试
        matched = []  # (tpl_name, count, min_conf, max_conf)
        unmatched = []

        for tpl_name in cat_templates:
            threshold = self._detector.get_template_threshold(tpl_name)
            results = self._detector.detect_single_template(
                self._test_bgr, tpl_name, threshold
            )
            if results:
                confs = [r.confidence for r in results]
                matched.append((tpl_name, len(results), min(confs), max(confs)))
            else:
                unmatched.append(tpl_name)

        # 按最高置信度降序排列
        matched.sort(key=lambda x: x[3], reverse=True)

        # 构建摘要（匹配到的在前，未匹配的在后）
        summary_lines = [f"类别: {cat} | 共 {len(cat_templates)} 个模板 | 匹配 {len(matched)} | 未匹配 {len(unmatched)}"]
        for tpl_name, count, min_conf, max_conf in matched:
            summary_lines.append(
                f"  ✓ {tpl_name}: {count}处, 置信度 {min_conf:.2f}~{max_conf:.2f}"
            )
        for tpl_name in unmatched:
            summary_lines.append(f"  ✗ {tpl_name}: 未匹配")

        # 收集所有匹配结果用于绘制
        all_results = []
        for tpl_name, _, _, _ in matched:
            threshold = self._detector.get_template_threshold(tpl_name)
            results = self._detector.detect_single_template(
                self._test_bgr, tpl_name, threshold
            )
            all_results.extend(results)

        # 按类别分组 NMS 去重
        final_results = self._detector._nms_by_category(all_results, iou_threshold=0.3)

        self._test_info.setText("\n".join(summary_lines))

        # 带编号的标注图（同步显示在小预览和放大弹窗）
        annotated = self._draw_results_with_numbers(self._test_bgr, final_results)
        self._show_test_image(annotated, final_results)

    def _draw_results_with_numbers(self, bgr: np.ndarray, results: list[DetectResult]) -> np.ndarray:
        """在图片上画带编号的矩形框（同步用于小预览和放大弹窗）"""
        output = bgr.copy()

        cat_colors = {
            "button": (0, 200, 255),
            "status_icon": (0, 100, 255),
            "crop": (0, 255, 100),
            "ui_element": (255, 255, 0),
            "land": (180, 180, 180),
            "seed": (255, 50, 255),
            "shop": (0, 200, 200),
            "warehouse_seed": (130, 159, 255),
            "unknown": (0, 0, 255),
        }

        sorted_results = sorted(results, key=lambda r: r.confidence, reverse=True)

        for idx, res in enumerate(sorted_results):
            num = idx + 1
            color = cat_colors.get(res.category, (0, 0, 255))
            x1, y1, x2, y2 = res.bbox

            # 半透明填充
            overlay = output.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            cv2.addWeighted(overlay, 0.2, output, 0.8, 0, output)

            # 边框
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 3)

            # 编号圆形徽章（左上角）
            cx, cy = x1 + 18, y1 + 18
            radius = 14
            cv2.circle(output, (cx, cy), radius, color, -1)
            cv2.circle(output, (cx, cy), radius, (255, 255, 255), 2)
            cv2.putText(output, str(num), (cx - 6, cy + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)

        return output

    def _show_test_image(self, bgr: np.ndarray, results: list[DetectResult] | None = None):
        self._test_bgr_annotated = bgr  # 保存标注后的图，用于点击放大
        self._test_bgr_results = results or []  # 保存匹配结果
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        data = rgb.tobytes()
        qimg = QImage(data, w, h, ch * w, QImage.Format.Format_RGB888)
        px = QPixmap.fromImage(qimg)
        max_h = self._test_scroll.viewport().height() - 4
        scaled = px.scaled(
            self._test_scroll.viewport().width() - 4,
            max(max_h, 200),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._test_preview.setPixmap(scaled)
        self._test_preview.setMinimumSize(scaled.size())

    def _on_preview_click(self):
        """点击测试预览图，打开放大预览"""
        if not hasattr(self, '_test_bgr_annotated') or self._test_bgr_annotated is None:
            return
        title = f"匹配结果预览 - {self._name}" if self._name else "匹配结果预览"
        results = getattr(self, '_test_bgr_results', [])
        dlg = ImagePreviewDialog(self._test_bgr_annotated, title, results, self)
        dlg.show()

    def _on_crop(self):
        """打开裁切对话框"""
        if not self._name or not self._filepath:
            return
        dlg = CropDialog(self._name, self._filepath, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # 裁切已保存，刷新预览
            px = self._load_pixmap(self._filepath)
            if px:
                scaled = px.scaled(280, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                self._preview.setPixmap(scaled)
                self._info_dims.setText(f"{px.width()} × {px.height()} px")
            self.template_changed.emit()

    def _on_rename(self):
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
        prefix = new_name.split("_")[0]
        if prefix not in TEMPLATE_CATEGORIES:
            box = _styled_msg_box(self, "前缀无效",
                "名称必须以有效前缀开头（btn_, bth_, icon_, crop_, ui_, land_, seed_, shop_）",
                icon=QMessageBox.Icon.Warning,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()
            return
        old_name = self._name
        new_fp = os.path.join(self._detector._templates_dir, f"{new_name}.png")
        if os.path.exists(new_fp) and new_name != self._name:
            box = _styled_msg_box(self, "覆盖",
                f"「{new_name}」已存在，是否覆盖？")
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        old_fp = self._filepath
        try:
            if os.path.exists(new_fp) and new_name != old_name:
                os.remove(new_fp)
            os.rename(old_fp, new_fp)
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

    def _on_capture_replace(self):
        """请求截屏采集替换当前模板图片"""
        if not self._name:
            return
        self.capture_replace_requested.emit()

    def _on_replace_image(self):
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

        size_lbl = QLabel(f"{size[0]} x {size[1]} px")
        size_lbl.setStyleSheet(f"color:{Colors.TEXT_SECONDARY}; font-size:12px;")
        form.addRow("选区大小:", size_lbl)

        self._type_combo = QComboBox()
        for prefix, label in _PREFIX_LABELS.items():
            cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")
            cat_color = _CAT_COLORS.get(cat, "#AEAEB2")
            self._type_combo.addItem(label, prefix)
        self._type_combo.currentIndexChanged.connect(self._update_preview)
        form.addRow("模板类型:", self._type_combo)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("输入名称（如 harvest、weed、mature）")
        self._name_edit.textChanged.connect(self._update_preview)
        form.addRow("名称:", self._name_edit)

        self._preview = QLabel()
        self._preview.setStyleSheet(
            f"font-weight:600; font-size:13px; color:{Colors.PRIMARY}; padding:4px 0;"
        )
        form.addRow("文件名:", self._preview)
        self._update_preview()

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
    """截图显示 + 鼠标框选（支持矩形和多边形 + alpha蒙版）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._ox = 0
        self._oy = 0
        
        # 绘图模式："rect" (矩形) 或 "polygon" (多边形)
        self._mode = "rect" 
        
        # 矩形相关
        self._rect_start: tuple[float, float] | None = None
        self._rect_end: tuple[float, float] | None = None
        self._dragging: bool = False

        # 多边形相关
        self._points: list[tuple[float, float]] = []  # 多边形顶点（显示坐标）
        self._mouse_pos: tuple[float, float] | None = None  # 当前鼠标位置

        self._bgr: np.ndarray | None = None
        self._pil = None
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, pil_image, bgr: np.ndarray):
        self._pil = pil_image
        self._bgr = bgr
        # Reset states
        self._points = []
        self._mouse_pos = None
        self._rect_start = None
        self._rect_end = None
        self._dragging = False
        self._build()
        self.update()

    def toggle_mode(self):
        """切换 矩形/多边形 模式"""
        if self._mode == "rect":
            self._mode = "polygon"
            # 切换时清除当前选区，避免混淆
            self._rect_start = None
            self._rect_end = None
        else:
            self._mode = "rect"
            self._points = []
            self._mouse_pos = None
        self.update()
        return self._mode

    def _is_selected(self) -> bool:
        """是否有有效的选区"""
        if self._mode == "rect":
            return self._rect_start is not None and self._rect_end is not None
        else:
            return len(self._points) >= 3

    def _build(self):
        if self._pil is None:
            return
        rgb = self._pil.convert("RGB")
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
            
            # 绘制逻辑：矩形 或 多边形
            if self._mode == "rect":
                # 绘制矩形框
                pen = QPen(QColor(0, 200, 80), 2, Qt.PenStyle.SolidLine)
                brush = QBrush(QColor(0, 200, 80, 35))
                p.setPen(pen)
                p.setBrush(brush)
                
                if self._rect_start and self._rect_end:
                    x1, y1 = int(self._rect_start[0]), int(self._rect_start[1])
                    x2, y2 = int(self._rect_end[0]), int(self._rect_end[1])
                    p.drawRect(x1, y1, x2 - x1, y2 - y1)
                elif self._rect_start and self._mouse_pos:
                    # 拖拽中
                    x1, y1 = int(self._rect_start[0]), int(self._rect_start[1])
                    x2, y2 = int(self._mouse_pos[0]), int(self._mouse_pos[1])
                    p.drawRect(x1, y1, x2 - x1, y2 - y1)
            else:
                # 绘制多边形（原逻辑）
                if len(self._points) >= 2:
                    pts = [QPoint(int(x), int(y)) for x, y in self._points]
                    p.setPen(QPen(QColor(0, 200, 80), 2, Qt.PenStyle.SolidLine))
                    p.setBrush(QBrush(QColor(0, 200, 80, 35)))
                    for i in range(len(pts) - 1):
                        p.drawLine(pts[i], pts[i + 1])
                    p.setBrush(QBrush(QColor(0, 255, 0)))
                    for pt in pts:
                        p.drawEllipse(pt, 4, 4)
                    if self._mouse_pos and not self._is_closed():
                        mx, my = int(self._mouse_pos[0]), int(self._mouse_pos[1])
                        p.drawLine(pts[-1], QPoint(mx, my))
                        p.drawEllipse(QPoint(mx, my), 3, 3)
                elif len(self._points) == 1:
                    px, py = int(self._points[0][0]), int(self._points[0][1])
                    p.setBrush(QBrush(QColor(0, 255, 0)))
                    p.drawEllipse(QPoint(px, py), 4, 4)
            
            # 提示文字
            if not self._is_selected():
                p.setPen(QColor(0, 200, 80))
                text = "拖拽绘制矩形" if self._mode == "rect" else "点击绘制多边形，Enter完成"
                p.drawText(self._ox + 10, self._oy + 20, text)
        else:
            p.setPen(QColor(Colors.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "点击「截屏采集」按钮获取游戏画面")
        p.end()

    def _is_closed(self) -> bool:
        """多边形是否已闭合（至少3个点）"""
        return len(self._points) >= 3

    def mousePressEvent(self, e):
        if not self._pixmap:
            return
            
        if e.button() == Qt.MouseButton.LeftButton:
            # 双击完成多边形
            if e.type() == e.Type.MouseButtonDblClick and self._mode == "polygon":
                if self._is_closed():
                    self._mouse_pos = None
                    self.update()
                return
                
            pos = (e.position().x(), e.position().y())
            if self._mode == "rect":
                # 矩形模式：记录起点
                self._rect_start = pos
                self._rect_end = pos
                self._dragging = True
                self._points = [] # 清除多边形
            else:
                # 多边形模式：添加点
                self._points.append(pos)
                self._mouse_pos = None
                self._rect_start = None
                self._rect_end = None
            
            self.update()
            
        elif e.button() == Qt.MouseButton.RightButton:
            # 右键撤销（仅多边形模式）
            if self._mode == "polygon" and self._points:
                self._points.pop()
                self.update()

    def mouseMoveEvent(self, e):
        pos = (e.position().x(), e.position().y())
        if self._pixmap:
            if self._mode == "rect" and self._dragging:
                self._rect_end = pos
                self.update()
            elif self._mode == "polygon" and len(self._points) > 0 and not self._is_closed():
                self._mouse_pos = pos
                self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._mode == "rect":
            self._dragging = False
            if self._rect_start:
                # 确保有宽高
                self._rect_end = (e.position().x(), e.position().y())
                self.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Enter 键完成多边形 (仅在多边形模式下有效)
            if self._mode == "polygon" and self._is_closed():
                self._mouse_pos = None
                self.update()
            e.accept()
        elif e.key() == Qt.Key.Key_Escape:
            # Esc 清除选区 (支持两种模式)
            self._points = []
            self._mouse_pos = None
            self._rect_start = None
            self._rect_end = None
            self._dragging = False
            self.update()
            e.accept()
        else:
            super().keyPressEvent(e)

    def _display_to_original(self, x: float, y: float) -> tuple[int, int]:
        """将显示坐标转换为原图坐标"""
        ox = int((x - self._ox) / self._scale)
        oy = int((y - self._oy) / self._scale)
        oh, ow = self._bgr.shape[:2]
        ox = max(0, min(ox, ow - 1))
        oy = max(0, min(oy, oh - 1))
        return ox, oy

    def get_crop(self) -> tuple | None:
        """获取选区裁剪（支持矩形和多边形，带alpha通道）"""
        if not self._is_selected() or self._bgr is None or not self._pixmap:
            return None

        # 矩形模式
        if self._mode == "rect":
            if not self._rect_start or not self._rect_end:
                return None
            
            # 获取原图坐标
            p1 = self._display_to_original(*self._rect_start)
            p2 = self._display_to_original(*self._rect_end)
            
            x1, y1 = p1
            x2, y2 = p2
            
            # 规范化坐标
            x = min(x1, x2)
            y = min(y1, y2)
            w = abs(x1 - x2)
            h = abs(y1 - y2)
            
            if w < 5 or h < 5:
                return None
            
            cropped = self._bgr[y:y+h, x:x+w].copy()
            
            # 矩形蒙版全白
            mask = np.ones((h, w), dtype=np.uint8) * 255
            bgra = cv2.cvtColor(cropped, cv2.COLOR_BGR2BGRA)
            bgra[:, :, 3] = mask
            return (bgra, w, h)

        # 多边形模式
        else:
            if len(self._points) < 3:
                return None

            # 转换为原图坐标
            original_points = [self._display_to_original(x, y) for x, y in self._points]
            pts = np.array(original_points, dtype=np.int32)

            # 计算边界框
            x, y, w, h = cv2.boundingRect(pts)
            if w < 5 or h < 5:
                return None

            # 裁剪区域
            cropped = self._bgr[y:y+h, x:x+w].copy()

            # 创建蒙版
            mask = np.zeros((h, w), dtype=np.uint8)
            pts_offset = pts - [x, y]
            cv2.fillPoly(mask, [pts_offset], 255)

            # 合并为 BGRA 四通道图像
            bgra = cv2.cvtColor(cropped, cv2.COLOR_BGR2BGRA)
            bgra[:, :, 3] = mask

            return (bgra, w, h)

    def clear(self):
        self._pixmap = self._bgr = self._pil = None
        self._img_data = None
        self._points = []
        self._mouse_pos = None
        # 清除矩形选区状态
        self._rect_start = None
        self._rect_end = None
        self._dragging = False
        self.update()


# ── 模板管理面板 ────────────────────────────────────────────

_SORT_MAP = {
    "名称 A-Z": lambda x: x[0],
    "名称 Z-A": lambda x: x[0],
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

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_filter_bar())

        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: transparent; width: 0px;
            }}
        """)

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

        self._detail = TemplateDetailPanel(self._detector)
        self._detail.closed.connect(self._on_detail_close)
        self._detail.template_changed.connect(self._on_detail_changed)
        self._detail.template_renamed.connect(self._on_detail_renamed)
        self._detail.capture_replace_requested.connect(self._on_capture_replace)
        self._detail.hide()
        self._splitter.addWidget(self._detail)

        self._splitter.setSizes([500, 350])
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)

        self._content_lay.addWidget(self._splitter)

        self._collector = self._build_collector()
        self._content_lay.addWidget(self._collector)
        self._collector.hide()

        root.addWidget(self._content, 1)

    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border-bottom: 1px solid {Colors.BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        title = QLabel("模板管理")
        title.setStyleSheet(f"""
            color: {Colors.TEXT}; font-size: 18px; font-weight: 700;
            background: transparent; border: none;
        """)
        lay.addWidget(title)

        self._count = QLabel("")
        self._count.setStyleSheet(f"color:{Colors.TEXT_DIM};font-size:12px;")
        lay.addWidget(self._count)

        lay.addStretch()

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

        return bar

    def _build_filter_bar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.WINDOW_BG};
                border-bottom: 1px solid {Colors.BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(10)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索模板...")
        self._search.setFixedHeight(32)
        self._search.setMinimumWidth(140)
        self._search.setMaximumWidth(200)
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 4px 12px;
                color: {Colors.TEXT};
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.BORDER_FOCUS};
            }}
        """)
        self._search.textChanged.connect(self._apply_filters)
        lay.addWidget(self._search)

        lay.addWidget(self._make_label("类型"))
        self._filter = QComboBox()
        self._filter.setFixedHeight(32)
        self._filter.setMinimumWidth(130)
        self._filter.setStyleSheet(f"""
            QComboBox {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 4px 10px;
                color: {Colors.TEXT};
                font-size: 12px;
            }}
            QComboBox:focus {{
                border-color: {Colors.BORDER_FOCUS};
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.CARD_BG};
                color: {Colors.TEXT};
                border: 1px solid rgba(0, 0, 0, 18);
                border-radius: 8px;
                selection-background-color: rgba(0, 122, 255, 12);
                selection-color: {Colors.TEXT};
                outline: none;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 4px 8px;
                border-radius: 4px;
                margin: 1px 2px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: rgba(0, 122, 255, 8);
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: rgba(0, 122, 255, 12);
                color: {Colors.PRIMARY};
                font-weight: 600;
            }}
        """)
        self._fill_filter()
        self._filter.currentIndexChanged.connect(self._apply_filters)
        lay.addWidget(self._filter)

        lay.addWidget(self._make_label("排序"))
        self._sort = QComboBox()
        self._sort.setFixedHeight(32)
        self._sort.setMinimumWidth(110)
        self._sort.setStyleSheet(f"""
            QComboBox {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 4px 10px;
                color: {Colors.TEXT};
                font-size: 12px;
            }}
            QComboBox:focus {{
                border-color: {Colors.BORDER_FOCUS};
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.CARD_BG};
                color: {Colors.TEXT};
                border: 1px solid rgba(0, 0, 0, 18);
                border-radius: 8px;
                selection-background-color: rgba(0, 122, 255, 12);
                selection-color: {Colors.TEXT};
                outline: none;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 4px 8px;
                border-radius: 4px;
                margin: 1px 2px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: rgba(0, 122, 255, 8);
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: rgba(0, 122, 255, 12);
                color: {Colors.PRIMARY};
                font-weight: 600;
            }}
        """)
        self._sort.addItems(list(_SORT_MAP.keys()))
        self._sort.setCurrentIndex(2)
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

        self._btn_toggle = QPushButton("模式: 矩形")
        self._btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_toggle.setStyleSheet(_icon_button("#AF52DE", "#9838C8"))
        self._btn_toggle.clicked.connect(self._on_toggle_mode)
        tbl.addWidget(self._btn_toggle)

        self._btn_save = QPushButton("保存选区")
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.setStyleSheet(_icon_button(Colors.SUCCESS, "#2DA44E"))
        self._btn_save.clicked.connect(self._on_save_crop)
        tbl.addWidget(self._btn_save)

        btn_back = QPushButton("返回列表")
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet(_outline_button())
        btn_back.clicked.connect(self._show_list)
        tbl.addWidget(btn_back)

        tbl.addStretch()

        self._ct_hint = QLabel("鼠标拖拽绘制矩形")
        self._ct_hint.setStyleSheet(f"color:{Colors.TEXT_DIM};font-size:12px;")
        tbl.addWidget(self._ct_hint)

        # ... (rest of layout code)

        lay.addWidget(tb)

        self._selector = ScreenshotSelector()
        self._selector.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(0,0,0,6);
                border: none;
            }}
        """)
        lay.addWidget(self._selector, 1)
        return w

    def _load_templates(self):
        self._detector.load_templates()
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

        out = []
        for name, fp, mt in self._items:
            if q and q not in name.lower():
                continue
            if fi > 0:
                prefixes = list(TEMPLATE_CATEGORIES.keys())
                if fi - 1 < len(prefixes):
                    want = prefixes[fi - 1]
                    if name.split("_")[0] != want:
                        continue
            out.append((name, fp, mt))

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
        if self._selected_name == name:
            self._on_detail_close()
            return
        self._selected_name = name
        for card in self._cards:
            card.set_selected(card.name == name)
        fp = ""
        for n, f, _ in self._items:
            if n == name:
                fp = f
                break
        if fp:
            self._detail.load_template(name, fp)
            if not self._detail.isVisible():
                self._detail.show()
                self._splitter.setSizes([400, 350])

    def _on_detail_close(self):
        self._selected_name = ""
        self._detail.clear()
        for card in self._cards:
            card.set_selected(False)

    def _on_detail_changed(self):
        self._load_templates()
        self.templates_changed.emit()

    def _on_detail_renamed(self, old_name: str, new_name: str):
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

    def _on_category_thresholds(self):
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

            tag = QLabel(f"● {cat_label}")
            tag.setFixedWidth(100)
            tag.setStyleSheet(
                f"color:{cat_color}; font-weight:600; font-size:12px; background:transparent;")

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

            spin = QDoubleSpinBox()
            spin.setRange(0.1, 1.0)
            spin.setSingleStep(0.01)
            spin.setDecimals(2)
            spin.setValue(default_val)
            spin.setFixedWidth(72)

            builtin_lbl = QLabel(f"(内置 {builtin_val:.1f})")
            builtin_lbl.setStyleSheet(
                f"color:{Colors.TEXT_DIM}; font-size:10px; background:transparent;")
            builtin_lbl.setFixedWidth(65)

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
        self._ct_hint.setText("左键点击添加顶点绘制多边形，Enter完成，右键撤销")

    def _on_capture_replace(self):
        """截屏采集替换当前选中的模板"""
        if not self._detail._name or not self._detail._filepath:
            return
        self._replace_target_name = self._detail._name
        self._replace_target_path = self._detail._filepath
        self._btn_capture.setEnabled(False)
        self._worker = CaptureWorker("QQ经典农场")
        self._worker.captured.connect(self._on_capture_replace_ready)
        self._worker.error.connect(self._on_cap_err)
        self._worker.finished.connect(self._clean_worker)
        self._worker.start()

    def _on_capture_replace_ready(self, pil_image):
        self._btn_capture.setEnabled(True)
        rgb = np.array(pil_image.convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        self._selector.set_image(pil_image, bgr)
        self._show_collector()
        self._ct_hint.setText(f"左键点击添加顶点绘制多边形，Enter完成，右键撤销 — 替换「{self._replace_target_name}」")
        # 替换保存按钮的行为
        self._save_btn_override = True
        self._btn_save.clicked.disconnect()
        self._btn_save.clicked.connect(self._on_save_crop_replace)
        self._btn_save.setText("保存替换")

    def _on_save_crop_replace(self):
        result = self._selector.get_crop()
        if not result:
            box = _styled_msg_box(self, "提示", "请先用多边形框选一个区域（至少3个顶点，Enter完成）",
                icon=QMessageBox.Icon.Information,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()
            return
        crop_bgra, w, h = result

        box = _styled_msg_box(self, "确认替换",
            f"确定替换模板「{self._replace_target_name}」？\n"
            f"选区大小: {w}x{h} px\n"
            f"此操作不可撤销。",
            icon=QMessageBox.Icon.Question,
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default=QMessageBox.StandardButton.Yes)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        fp = self._replace_target_path
        # 确保保存为 BGRA 四通道（保留 alpha 蒙版）
        if crop_bgra.shape[2] == 3:
            crop_bgra = cv2.cvtColor(crop_bgra, cv2.COLOR_BGR2BGRA)
        ok, buf = cv2.imencode('.png', crop_bgra)
        if ok:
            buf.tofile(fp)
            self._ct_hint.setText(f"已替换 {self._replace_target_name}.png ({w}x{h})")
            self._detail.load_template(self._replace_target_name, fp)
            self._detail.template_changed.emit()
            self._load_templates()
            self.templates_changed.emit()
            self._reset_save_button()
        else:
            box = _styled_msg_box(self, "保存失败", "无法编码图片",
                icon=QMessageBox.Icon.Warning,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()

    def _reset_save_button(self):
        self._save_btn_override = False
        self._btn_save.clicked.disconnect()
        self._btn_save.clicked.connect(self._on_save_crop)
        self._btn_save.setText("保存选区")

    def _on_cap_err(self, msg: str):
        self._btn_capture.setEnabled(True)
        box = _styled_msg_box(self, "截屏失败", msg,
            icon=QMessageBox.Icon.Warning,
            buttons=QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_save_crop(self):
        result = self._selector.get_crop()
        if not result:
            box = _styled_msg_box(self, "提示", "请先用多边形框选一个区域（至少3个顶点，Enter完成）",
                icon=QMessageBox.Icon.Information,
                buttons=QMessageBox.StandardButton.Ok)
            box.exec()
            return
        crop_bgra, w, h = result

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

        # 确保保存为 BGRA 四通道（保留 alpha 蒙版）
        if crop_bgra.shape[2] == 3:
            crop_bgra = cv2.cvtColor(crop_bgra, cv2.COLOR_BGR2BGRA)
        ok, buf = cv2.imencode('.png', crop_bgra)
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

    def _on_toggle_mode(self):
        """切换矩形/多边形模式"""
        mode = self._selector.toggle_mode()
        if mode == "rect":
            self._btn_toggle.setText("模式: 矩形")
            self._ct_hint.setText("鼠标拖拽绘制矩形")
        else:
            self._btn_toggle.setText("模式: 多边形")
            self._ct_hint.setText("鼠标点击绘制多边形，双击或Enter完成")

    def _show_collector(self):
        self._splitter.hide()
        self._collector.show()

    def _show_list(self):
        self._collector.hide()
        self._splitter.show()
        self._selector.clear()

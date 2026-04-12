"""实例侧边栏 — 多开实例切换 + 并发运行

显示所有实例列表，支持：
  - 点击切换实例
  - 每个实例独立启动/停止
  - 全部启动/全部停止
  - 新增、删除、克隆、重命名实例
  - 实时显示实例状态（空闲/运行中/已暂停）
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QMessageBox,
    QInputDialog,
    QScrollArea,
)

from gui.styles import Colors


class InstanceRow(QFrame):
    """单个实例行：名称 + 状态 + 启动/停止按钮（使用 QFrame 确保鼠标事件正常传递）"""

    start_requested = pyqtSignal(str)  # instance_id
    stop_requested = pyqtSignal(str)
    selected = pyqtSignal(str)

    def __init__(self, instance_id: str, name: str, state: str = 'idle', parent=None):
        super().__init__(parent)
        self._instance_id = instance_id
        self._state = state
        self._selected = False
        self._name_display_text = name
        self._init_ui()

    def _init_ui(self):
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        # 状态指示灯（鼠标穿透）
        self._indicator = QLabel()
        self._indicator.setFixedSize(8, 8)
        self._indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._update_indicator_style()
        layout.addWidget(self._indicator)

        # 名称 + 状态（鼠标穿透）
        info_widget = QWidget()
        info_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(1)

        self._name_label = QLabel(self._name_display())
        self._name_label.setStyleSheet(f"""
            color: {Colors.TEXT};
            font-size: 13px;
            font-weight: 600;
            background: transparent;
            border: none;
        """)
        info_layout.addWidget(self._name_label)

        self._state_label = QLabel(self._state_text())
        self._state_label.setStyleSheet(f"""
            color: {Colors.TEXT_SECONDARY};
            font-size: 11px;
            background: transparent;
            border: none;
        """)
        info_layout.addWidget(self._state_label)

        layout.addWidget(info_widget, 1)

        # 启动/停止按钮
        self._btn = QPushButton()
        self._btn.setFixedHeight(26)
        self._btn.setFixedWidth(52)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._on_btn_clicked)
        layout.addWidget(self._btn)

        self._update_btn()
        self._update_row_style()

    def _update_indicator_style(self):
        color = {
            'running': Colors.SUCCESS,
            'paused': Colors.WARNING,
            'idle': Colors.TEXT_DIM,
            'error': Colors.DANGER,
        }.get(self._state, Colors.TEXT_DIM)
        self._indicator.setStyleSheet(f"""
            background-color: {color};
            border-radius: 4px;
        """)

    def _update_row_style(self):
        bg = Colors.SIDEBAR_ITEM_SELECTED_BG if self._selected else 'transparent'
        self.setStyleSheet(f"""
            InstanceRow {{
                background: {bg};
                border-radius: 6px;
            }}
            InstanceRow:hover {{
                background: {Colors.SIDEBAR_ITEM_HOVER};
            }}
        """)

    def _on_btn_clicked(self):
        """按钮点击时，同时触发选择和启动/停止"""
        self.selected.emit(self._instance_id)
        if self._state == 'running':
            self.stop_requested.emit(self._instance_id)
        else:
            self.start_requested.emit(self._instance_id)

    def mousePressEvent(self, event):
        """点击行本身触发选择"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._instance_id)
        super().mousePressEvent(event)

    def _name_display(self) -> str:
        name = str(self._name_display_text if hasattr(self, '_name_display_text') else self._instance_id)
        return name[:12] + '...' if len(name) > 12 else name

    def _state_text(self) -> str:
        return {
            'running': '运行中',
            'paused': '已暂停',
            'idle': '空闲',
            'error': '错误',
        }.get(str(self._state or 'idle').lower(), '未知')

    def _update_btn(self):
        if self._state == 'running':
            self._btn.setText('停止')
            self._btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid {Colors.DANGER};
                    color: {Colors.DANGER};
                    border-radius: 4px;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background: {Colors.DANGER};
                    color: white;
                }}
            """)
        else:
            self._btn.setText('启动')
            self._btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid {Colors.SUCCESS};
                    color: {Colors.SUCCESS};
                    border-radius: 4px;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background: {Colors.SUCCESS};
                    color: white;
                }}
            """)

    def set_state(self, state: str):
        self._state = state
        self._state_label.setText(self._state_text())
        
        indicator_color = {
            'running': Colors.SUCCESS,
            'paused': Colors.WARNING,
            'idle': Colors.TEXT_DIM,
            'error': Colors.DANGER,
        }.get(self._state, Colors.TEXT_DIM)
        self._indicator.setStyleSheet(f"""
            background-color: {indicator_color};
            border-radius: 4px;
        """)

        self._update_btn()
        self._update_row_style()

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_row_style()

    def set_name(self, name: str):
        self._name_display_text = name
        self._name_label.setText(self._name_display())


class InstanceSidebar(QWidget):
    """实例列表与实例操作栏"""

    instance_selected = pyqtSignal(str)  # 用户点击实例时发出
    instance_start_requested = pyqtSignal(str)  # 启动单个实例
    instance_stop_requested = pyqtSignal(str)  # 停止单个实例
    start_all_requested = pyqtSignal()
    stop_all_requested = pyqtSignal()
    create_requested = pyqtSignal()
    delete_requested = pyqtSignal(str)
    clone_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, InstanceRow] = {}  # instance_id -> InstanceRow
        self._active_instance_id: str = ''
        self._build_ui()

    def _build_ui(self) -> None:
        self.setObjectName('instanceSidebar')
        self.setFixedWidth(200)
        self.setStyleSheet(f"""
            QWidget#instanceSidebar {{
                background: {Colors.SIDEBAR_BG};
                border-right: 1px solid {Colors.BORDER};
            }}
            QLabel#instanceTitle {{
                color: {Colors.TEXT};
                font-weight: 700;
                font-size: 13px;
                padding: 4px 12px;
            }}
            QFrame#actionsWrap {{
                border-top: 1px solid {Colors.BORDER};
                padding-top: 8px;
            }}
            QPushButton#instanceActionBtn {{
                background: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                color: {Colors.TEXT};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QPushButton#instanceActionBtn:hover {{
                background: rgba(0, 122, 255, 10);
                border-color: {Colors.PRIMARY};
            }}
            QPushButton#instanceActionBtn:disabled {{
                color: {Colors.TEXT_DIM};
                background: rgba(0, 0, 0, 5);
            }}
            QPushButton#startAllBtn {{
                background: {Colors.SUCCESS};
                border: none;
                color: white;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#startAllBtn:hover {{
                background: #2DA44E;
            }}
            QPushButton#stopAllBtn {{
                background: {Colors.DANGER};
                border: none;
                color: white;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#stopAllBtn:hover {{
                background: #b91c1c;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 16, 8, 8)
        root.setSpacing(8)

        # 标题
        self._title = QLabel('实例')
        self._title.setObjectName('instanceTitle')
        root.addWidget(self._title)

        # 实例列表滚动区域
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_container)
        root.addWidget(self._scroll, 1)

        # 全部启动/全部停止按钮
        batch_row = QHBoxLayout()
        batch_row.setSpacing(6)

        self._btn_start_all = QPushButton('全部启动')
        self._btn_start_all.setObjectName('startAllBtn')
        self._btn_start_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start_all.clicked.connect(self.start_all_requested.emit)

        self._btn_stop_all = QPushButton('全部停止')
        self._btn_stop_all.setObjectName('stopAllBtn')
        self._btn_stop_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_stop_all.clicked.connect(self.stop_all_requested.emit)

        batch_row.addWidget(self._btn_start_all)
        batch_row.addWidget(self._btn_stop_all)
        root.addLayout(batch_row)

        # 操作按钮
        self._actions_wrap = QFrame()
        self._actions_wrap.setObjectName('actionsWrap')
        actions = QVBoxLayout(self._actions_wrap)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)

        self._btn_create = QPushButton('新增')
        self._btn_delete = QPushButton('删除')
        self._btn_clone = QPushButton('克隆')
        self._btn_rename = QPushButton('重命名')

        self._btn_create.clicked.connect(self.create_requested.emit)
        self._btn_delete.clicked.connect(self._emit_delete)
        self._btn_clone.clicked.connect(self._emit_clone)
        self._btn_rename.clicked.connect(self._emit_rename)

        for btn in (self._btn_create, self._btn_delete, self._btn_clone, self._btn_rename):
            btn.setObjectName('instanceActionBtn')
            btn.setFixedHeight(32)
            actions.addWidget(btn)

        root.addWidget(self._actions_wrap, 0)

    def _emit_delete(self) -> None:
        if self._active_instance_id:
            self.delete_requested.emit(self._active_instance_id)

    def _emit_clone(self) -> None:
        if self._active_instance_id:
            self.clone_requested.emit(self._active_instance_id)

    def _emit_rename(self) -> None:
        if self._active_instance_id:
            self.rename_requested.emit(self._active_instance_id)

    def set_instances(self, instances: list[dict[str, Any]]) -> None:
        """刷新实例列表"""
        # 构建新实例集合
        new_ids = set()
        for item in instances:
            iid = str(item.get('id') or '')
            if not iid:
                continue
            new_ids.add(iid)

            if iid not in self._rows:
                # 新实例，创建新行
                name = str(item.get('name') or iid)
                state = str(item.get('state') or 'idle')
                row = InstanceRow(iid, name, state)
                row.start_requested.connect(self.instance_start_requested.emit)
                row.stop_requested.connect(self.instance_stop_requested.emit)
                row.selected.connect(self._on_row_selected)
                self._rows[iid] = row

                # 插入到列表中（在 stretch 之前）
                self._list_layout.insertWidget(self._list_layout.count() - 1, row)

            # 更新状态
            state = str(item.get('state') or 'idle')
            name = str(item.get('name') or iid)
            self._rows[iid].set_state(state)
            self._rows[iid].set_name(name)

        # 删除不存在的实例
        for iid in list(self._rows.keys()):
            if iid not in new_ids:
                row = self._rows.pop(iid)
                row.deleteLater()

        # 更新选中状态
        if self._active_instance_id in self._rows:
            self._rows[self._active_instance_id].set_selected(True)

    def _on_row_selected(self, instance_id: str):
        """行被点击，触发实例切换"""
        self._active_instance_id = instance_id
        # 更新所有行的选中状态
        for iid, row in self._rows.items():
            row.set_selected(iid == instance_id)
        # 发送信号给 MainWindow
        self.instance_selected.emit(instance_id)

    def set_active_instance(self, instance_id: str) -> None:
        """高亮当前实例"""
        self._active_instance_id = instance_id
        for iid, row in self._rows.items():
            row.set_selected(iid == instance_id)

    def update_instance_state(self, instance_id: str, state: str, name: str | None = None) -> None:
        """更新实例状态显示"""
        if instance_id in self._rows:
            self._rows[instance_id].set_state(state)
            if name:
                self._rows[instance_id].set_name(name)

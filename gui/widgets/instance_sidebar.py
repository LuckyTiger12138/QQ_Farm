"""实例侧边栏 — 参考 qq-farm-copilot 设计

默认只显示实例列表，点击"管理"展开操作面板。
简洁干净，突出实例切换功能。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QScrollArea,
)

from gui.styles import Colors


class InstanceItem(QFrame):
    """单个实例项：状态指示灯 + 名称（可点击切换）"""

    selected = pyqtSignal(str)  # instance_id

    def __init__(self, instance_id: str, name: str, state: str = 'idle', parent=None):
        super().__init__(parent)
        self._instance_id = instance_id
        self._state = state
        self._selected = False
        self._name_display_text = name
        self._init_ui()

    def _init_ui(self):
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # 状态指示灯
        self._indicator = QLabel()
        self._indicator.setFixedSize(6, 6)
        self._indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._update_indicator_style()
        layout.addWidget(self._indicator)

        # 实例名称
        self._name_label = QLabel(self._name_display())
        self._name_label.setStyleSheet(f"""
            color: {Colors.TEXT};
            font-size: 13px;
            background: transparent;
            border: none;
        """)
        layout.addWidget(self._name_label, 1)

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
            border-radius: 3px;
        """)

    def _update_row_style(self):
        if self._selected:
            bg = Colors.SIDEBAR_ITEM_SELECTED_BG
            text_color = Colors.PRIMARY
        else:
            bg = 'transparent'
            text_color = Colors.TEXT
        self.setStyleSheet(f"""
            InstanceItem {{
                background: {bg};
                border-radius: 6px;
            }}
            InstanceItem:hover {{
                background: {Colors.SIDEBAR_ITEM_HOVER};
            }}
        """)
        self._name_label.setStyleSheet(f"""
            color: {text_color};
            font-size: 13px;
            background: transparent;
            border: none;
        """)

    def mousePressEvent(self, event):
        """点击触发选择"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._instance_id)
        super().mousePressEvent(event)

    def _name_display(self) -> str:
        name = str(self._name_display_text if hasattr(self, '_name_display_text') else self._instance_id)
        return name[:14] + '...' if len(name) > 14 else name

    def set_state(self, state: str):
        self._state = state
        self._update_indicator_style()

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_row_style()

    def set_name(self, name: str):
        self._name_display_text = name
        self._name_label.setText(self._name_display())


class InstanceSidebar(QWidget):
    """实例侧边栏：默认显示实例列表，点击"管理"展开操作按钮"""

    instance_selected = pyqtSignal(str)
    instance_start_requested = pyqtSignal(str)
    instance_stop_requested = pyqtSignal(str)
    start_all_requested = pyqtSignal()
    stop_all_requested = pyqtSignal()
    create_requested = pyqtSignal()
    delete_requested = pyqtSignal(str)
    clone_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, InstanceItem] = {}
        self._active_instance_id: str = ''
        self._management_mode = False  # 是否处于管理模式
        self._build_ui()

    def _build_ui(self) -> None:
        self.setObjectName('instanceSidebar')
        self.setFixedWidth(180)
        self.setStyleSheet(f"""
            QWidget#instanceSidebar {{
                background: {Colors.SIDEBAR_BG};
                border-left: 1px solid {Colors.BORDER};
            }}
            QLabel#instanceTitle {{
                color: {Colors.TEXT};
                font-weight: 700;
                font-size: 14px;
                padding: 12px 10px 8px 10px;
            }}
            QPushButton#manageBtn {{
                background: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                color: {Colors.TEXT_SECONDARY};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#manageBtn:hover {{
                background: rgba(0, 122, 255, 10);
                color: {Colors.PRIMARY};
                border-color: {Colors.PRIMARY};
            }}
            QPushButton#manageBtn:checked {{
                background: {Colors.PRIMARY};
                color: white;
                border-color: {Colors.PRIMARY};
            }}
            QPushButton#actionBtn {{
                background: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                color: {Colors.TEXT};
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
            }}
            QPushButton#actionBtn:hover {{
                background: rgba(0, 122, 255, 10);
                border-color: {Colors.PRIMARY};
                color: {Colors.PRIMARY};
            }}
            QPushButton#startAllBtn {{
                background: {Colors.SUCCESS};
                border: none;
                color: white;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 12px;
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
                padding: 8px 12px;
            }}
            QPushButton#stopAllBtn:hover {{
                background: #b91c1c;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 12, 8, 8)
        root.setSpacing(8)

        # 标题行：实例标题 + 管理按钮
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        
        self._title = QLabel('实例')
        self._title.setObjectName('instanceTitle')
        title_row.addWidget(self._title)
        
        self._btn_manage = QPushButton('管理')
        self._btn_manage.setObjectName('manageBtn')
        self._btn_manage.setCheckable(True)
        self._btn_manage.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_manage.clicked.connect(self._toggle_management)
        title_row.addWidget(self._btn_manage)
        
        root.addLayout(title_row)

        # 实例列表
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(2, 2, 2, 2)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_container)
        root.addWidget(self._scroll, 1)

        # 管理面板（默认隐藏）
        self._manage_panel = QFrame()
        self._manage_panel.setObjectName('managePanel')
        self._manage_panel.setStyleSheet(f"""
            QFrame#managePanel {{
                background: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}
        """)
        manage_layout = QVBoxLayout(self._manage_panel)
        manage_layout.setContentsMargins(6, 8, 6, 8)
        manage_layout.setSpacing(6)

        # 管理操作按钮
        self._btn_create = QPushButton('新增')
        self._btn_delete = QPushButton('删除')
        self._btn_clone = QPushButton('克隆')
        self._btn_rename = QPushButton('重命名')

        for btn in (self._btn_create, self._btn_delete, self._btn_clone, self._btn_rename):
            btn.setObjectName('actionBtn')
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            manage_layout.addWidget(btn)

        self._btn_create.clicked.connect(self.create_requested.emit)
        self._btn_delete.clicked.connect(self._emit_delete)
        self._btn_clone.clicked.connect(self._emit_clone)
        self._btn_rename.clicked.connect(self._emit_rename)

        self._manage_panel.setVisible(False)
        root.addWidget(self._manage_panel)

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

    def _toggle_management(self):
        """切换管理模式"""
        self._management_mode = self._btn_manage.isChecked()
        self._manage_panel.setVisible(self._management_mode)

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
        new_ids = set()
        for item in instances:
            iid = str(item.get('id') or '')
            if not iid:
                continue
            new_ids.add(iid)

            if iid not in self._rows:
                name = str(item.get('name') or iid)
                state = str(item.get('state') or 'idle')
                row = InstanceItem(iid, name, state)
                row.selected.connect(self._on_row_selected)
                self._rows[iid] = row
                self._list_layout.insertWidget(self._list_layout.count() - 1, row)

            state = str(item.get('state') or 'idle')
            name = str(item.get('name') or iid)
            self._rows[iid].set_state(state)
            self._rows[iid].set_name(name)

        for iid in list(self._rows.keys()):
            if iid not in new_ids:
                row = self._rows.pop(iid)
                row.deleteLater()

        if self._active_instance_id in self._rows:
            self._rows[self._active_instance_id].set_selected(True)

    def _on_row_selected(self, instance_id: str):
        """实例被点击"""
        self._active_instance_id = instance_id
        for iid, row in self._rows.items():
            row.set_selected(iid == instance_id)
        self.instance_selected.emit(instance_id)

    def set_active_instance(self, instance_id: str) -> None:
        """高亮当前实例"""
        self._active_instance_id = instance_id
        for iid, row in self._rows.items():
            row.set_selected(iid == instance_id)

    def update_instance_state(self, instance_id: str, state: str, name: str | None = None) -> None:
        """更新实例状态"""
        if instance_id in self._rows:
            self._rows[instance_id].set_state(state)
            if name:
                self._rows[instance_id].set_name(name)

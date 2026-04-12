"""实例侧边栏 — 参考 qq-farm-copilot 设计

默认只显示实例列表，右键菜单进行操作。
- 空白处右键：新增
- 实例右键：克隆、重命名、删除
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QScrollArea,
)

from gui.styles import Colors


class InstanceItem(QFrame):
    """单个实例项：状态指示灯 + 名称（可点击切换，右键菜单）"""

    selected = pyqtSignal(str)
    clone_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

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
        layout.setContentsMargins(10, 4, 10, 4)
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
        """左键选择，右键菜单"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._instance_id)
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def _show_context_menu(self, pos):
        """显示实例右键菜单"""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 28px 8px 16px;
                border-radius: 6px;
                font-size: 13px;
                margin: 2px 0px;
            }}
            QMenu::item:hover {{
                background-color: rgba(0, 122, 255, 12);
                color: {Colors.PRIMARY};
            }}
            QMenu::separator {{
                height: 1px;
                background: {Colors.BORDER};
                margin: 6px 10px;
            }}
        """)
        
        clone_act = menu.addAction('克隆')
        clone_act.triggered.connect(lambda: self.clone_requested.emit(self._instance_id))
        
        rename_act = menu.addAction('重命名')
        rename_act.triggered.connect(lambda: self.rename_requested.emit(self._instance_id))
        
        menu.addSeparator()
        
        delete_act = menu.addAction('删除')
        delete_act.triggered.connect(lambda: self.delete_requested.emit(self._instance_id))
        
        menu.exec(pos)

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
    """实例侧边栏：右键菜单操作"""

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

        # 标题
        self._title = QLabel('实例')
        self._title.setObjectName('instanceTitle')
        root.addWidget(self._title)

        # 实例列表
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._scroll.customContextMenuRequested.connect(self._show_global_menu)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(2, 2, 2, 2)
        self._list_layout.setSpacing(2)
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

    def _show_global_menu(self, pos):
        """空白处右键菜单"""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.BORDER};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 28px 8px 16px;
                border-radius: 6px;
                font-size: 13px;
                margin: 2px 0px;
            }}
            QMenu::item:hover {{
                background-color: rgba(0, 122, 255, 12);
                color: {Colors.PRIMARY};
            }}
        """)
        
        add_act = menu.addAction('新增')
        add_act.triggered.connect(self.create_requested.emit)
        
        menu.exec(self._scroll.mapToGlobal(pos))

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
                row.clone_requested.connect(self.clone_requested.emit)
                row.rename_requested.connect(self.rename_requested.emit)
                row.delete_requested.connect(self.delete_requested.emit)
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

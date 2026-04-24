"""窗口管理器 - 定位并管理微信小程序窗口"""
import ctypes
import ctypes.wintypes
import os
import subprocess
import time
from dataclasses import dataclass
from loguru import logger

import pygetwindow as gw
from PyQt6.QtCore import QCoreApplication

from models.config import WindowPosition


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int


class WindowManager:
    def __init__(self):
        self._cached_window: WindowInfo | None = None
        self._pinned_hwnd: int | None = None  # 锁定的窗口句柄，防止多实例串台

    def launch_game(self, shortcut_path: str) -> bool:
        """通过快捷方式启动游戏"""
        if not shortcut_path or not os.path.exists(shortcut_path):
            logger.warning(f"快捷方式路径无效：{shortcut_path}")
            return False
        try:
            os.startfile(shortcut_path)
            logger.info(f"已启动游戏快捷方式：{shortcut_path}")
            return True
        except Exception as e:
            logger.error(f"启动游戏失败：{e}")
            return False

    def _verify_and_pin_window(self, hwnd: int, title_keyword: str) -> WindowInfo | None:
        """验证已锁定的句柄是否依然有效"""
        try:
            all_windows = gw.getAllWindows()
            for win in all_windows:
                if int(getattr(win, '_hWnd', 0) or 0) == hwnd:
                    title = str(getattr(win, 'title', '') or '')
                    if title_keyword.lower() in title.lower():
                        info = self._create_window_info(win)
                        return info
        except Exception as e:
            logger.error(f"验证 hwnd {hwnd} 失败: {e}")
        return None

    def find_window(self, title_keyword: str = "QQ 经典农场", auto_launch: bool = False, shortcut_path: str = "", select_rule: str = "auto") -> WindowInfo | None:
        """通过标题关键词和选择规则查找窗口，可选自动启动游戏

        Args:
            title_keyword: 窗口标题关键词
            auto_launch: 未找到时是否自动启动
            shortcut_path: 游戏快捷方式路径
            select_rule: 窗口选择规则 ('auto' 或 'index:N')
        """
        try:
            # 1. 优先复用已锁定的窗口（防止窗口移动后排序变化导致选错）
            if self._pinned_hwnd:
                valid_window = self._verify_and_pin_window(self._pinned_hwnd, title_keyword)
                if valid_window:
                    return valid_window
                else:
                    logger.warning(f"已锁定窗口 (hwnd={self._pinned_hwnd}) 已丢失，重新查找...")
                    self._pinned_hwnd = None  # 清除锁定

            # 2. 列出所有匹配窗口
            windows = self._list_all_windows(title_keyword)
            if not windows:
                # 如果还是没找到且启用了自动启动
                if auto_launch and shortcut_path:
                    logger.info(f"未找到窗口 '{title_keyword}'，尝试启动游戏...")
                    if self.launch_game(shortcut_path):
                        # 等待游戏启动（最多 30 秒）
                        logger.info("等待游戏启动（最多 30 秒）...")
                        for i in range(60):
                            time.sleep(0.5)
                            QCoreApplication.processEvents()
                            windows = self._list_all_windows(title_keyword)
                            if windows:
                                break
                        else:
                            all_titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
                            logger.warning(f"等待超时，当前窗口列表：{all_titles}")
                            return None
                else:
                    logger.warning(f"未找到包含 '{title_keyword}' 的窗口")
                    return None

            # 3. 根据规则选择目标窗口
            if str(select_rule or '').strip().lower() in {'', 'auto'}:
                target_index = 0
            else:
                target_index = self._resolve_select_index(select_rule, len(windows))

            w = windows[target_index]
            # 锁定选中的窗口句柄
            self._pinned_hwnd = int(getattr(w, '_hWnd', 0) or 0)
            return self._create_window_info(w)

        except Exception as e:
            logger.error(f"查找窗口失败：{e}")
            return None

    def _create_window_info(self, w) -> WindowInfo:
        """创建窗口信息对象"""
        info = WindowInfo(
            hwnd=w._hWnd,
            title=w.title,
            left=w.left,
            top=w.top,
            width=w.width,
            height=w.height,
        )
        self._cached_window = info
        logger.trace(f"找到窗口：{info.title} ({info.width}x{info.height})")
        return info

    def get_window_rect(self) -> tuple[int, int, int, int] | None:
        """获取缓存窗口的区域 (left, top, width, height)"""
        if not self._cached_window:
            return None
        w = self._cached_window
        return (w.left, w.top, w.width, w.height)

    def activate_window(self) -> bool:
        """激活并置顶窗口"""
        if not self._cached_window:
            return False
        try:
            hwnd = self._cached_window.hwnd
            # 使用 win32 API 置顶窗口
            SW_RESTORE = 9
            ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            logger.debug("窗口已激活")
            return True
        except Exception as e:
            logger.error(f"激活窗口失败：{e}")
            return False

    def resize_window(self, width: int, height: int, window_position: WindowPosition = WindowPosition.BOTTOM_LEFT) -> bool:
        """调整窗口大小并放置到指定位置
        
        Args:
            width: 窗口宽度
            height: 窗口高度
            window_position: 窗口在屏幕中的位置（左上、右上、左下、右下、居中）
        """
        if not self._cached_window:
            return False
        try:
            hwnd = self._cached_window.hwnd
            # 获取工作区域（排除任务栏）
            work_area = ctypes.wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0)
            
            work_width = work_area.right - work_area.left
            work_height = work_area.bottom - work_area.top
            
            # 根据位置计算坐标
            if window_position == WindowPosition.TOP_LEFT:
                pos_x = work_area.left
                pos_y = work_area.top
            elif window_position == WindowPosition.TOP_RIGHT:
                pos_x = work_area.left + work_width - width
                pos_y = work_area.top
            elif window_position == WindowPosition.BOTTOM_LEFT:
                pos_x = work_area.left
                pos_y = work_area.bottom - height
            elif window_position == WindowPosition.BOTTOM_RIGHT:
                pos_x = work_area.left + work_width - width
                pos_y = work_area.bottom - height
            elif window_position == WindowPosition.CENTER:
                pos_x = work_area.left + (work_width - width) // 2
                pos_y = work_area.top + (work_height - height) // 2
            else:
                # 默认左下角
                pos_x = work_area.left
                pos_y = work_area.bottom - height
            
            # 确保坐标不超出工作区
            pos_x = max(work_area.left, min(pos_x, work_area.right - width))
            pos_y = max(work_area.top, min(pos_y, work_area.bottom - height))
            
            ctypes.windll.user32.MoveWindow(hwnd, pos_x, pos_y, width, height, True)
            self._cached_window.left = pos_x
            self._cached_window.top = pos_y
            self._cached_window.width = width
            self._cached_window.height = height
            logger.info(f"窗口调整为 {width}x{height}，位置({pos_x},{pos_y}) [{window_position.value}]")
            return True
        except Exception as e:
            logger.error(f"调整窗口大小失败：{e}")
            return False

    def is_window_visible(self) -> bool:
        """检查窗口是否可见"""
        if not self._cached_window:
            return False
        try:
            return bool(ctypes.windll.user32.IsWindowVisible(self._cached_window.hwnd))
        except Exception:
            return False

    def get_window_handle(self) -> int | None:
        """获取当前缓存窗口的句柄"""
        if not self._cached_window:
            return None
        return self._cached_window.hwnd

    def refresh_window_info(self, title_keyword: str = "QQ 农场", auto_launch: bool = False, shortcut_path: str = "") -> WindowInfo | None:
        """刷新窗口位置信息，可选自动启动游戏"""
        return self.find_window(title_keyword, auto_launch, shortcut_path)

    def _list_all_windows(self, title_keyword: str) -> list:
        """列出所有匹配的窗口"""
        try:
            all_windows = gw.getAllWindows()
            matched = []
            seen_hwnd: set[int] = set()

            for win in all_windows:
                title = str(getattr(win, 'title', '') or '')
                if not title.strip():
                    continue

                # 关键词匹配
                keyword = title_keyword.lower()
                title_lower = title.lower()
                if keyword not in title_lower:
                    # 回退：包含"农场"且不是"助手"
                    if '农场' not in title_lower or '助手' in title_lower:
                        continue

                hwnd = int(getattr(win, '_hWnd', 0) or 0)
                if hwnd <= 0 or hwnd in seen_hwnd:
                    continue

                width = int(getattr(win, 'width', 0) or 0)
                height = int(getattr(win, 'height', 0) or 0)
                # 严格过滤：过小的窗口不要（Chrome 标签提示、工具提示等）
                if width < 300 or height < 300:
                    continue

                # 过滤：排除浏览器等非游戏窗口
                try:
                    pid = ctypes.c_ulong()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value:
                        try:
                            import psutil
                            proc_name = psutil.Process(pid.value).name().lower()
                        except Exception:
                            proc_name = ''
                        # 排除浏览器进程
                        if proc_name in ('chrome.exe', 'msedge.exe', 'firefox.exe',
                                         'brave.exe', 'opera.exe', 'vivaldi.exe',
                                         'code.exe', 'explorer.exe'):
                            continue
                except Exception:
                    pass

                matched.append(win)
                seen_hwnd.add(hwnd)

            # 按位置排序，保证顺序稳定
            matched.sort(key=lambda w: (int(getattr(w, 'left', 0)), int(getattr(w, 'top', 0)), int(getattr(w, '_hWnd', 0))))
            return matched
        except Exception as e:
            logger.error(f'列出窗口失败: {e}')
            return []

    @staticmethod
    def _resolve_select_index(select_rule: str, total: int) -> int:
        """将选择规则解析为窗口索引，非法规则回退到 0"""
        if total <= 0:
            return 0
        text = str(select_rule or 'auto').strip().lower()
        if not text or text == 'auto':
            return 0
        if text.startswith('index:'):
            suffix = text.split(':', 1)[1]
            try:
                idx = int(suffix)
            except Exception:
                return 0
            if idx < 0:
                return 0
            if idx >= total:
                # 静默回退，不打日志（窗口可能已关闭）
                return 0
            return idx
        return 0

    def hide_game_window(self, title_keyword: str = "QQ 农场", auto_find: bool = True) -> bool:
        """将游戏窗口完美隐藏（老板键）

        两步隐藏：
        1. 添加 WS_EX_TOOLWINDOW 样式，隐藏任务栏图标
        2. 设置透明度为 0，窗口完全透明
        这样老板既看不到窗口，也看不到任务栏图标！
        """
        # 如果未缓存窗口，尝试自动查找
        if not self._cached_window:
            if auto_find:
                logger.info("未找到缓存窗口，尝试自动查找游戏窗口...")
                window = self.find_window(title_keyword, auto_launch=False, shortcut_path="")
                if not window:
                    logger.warning("无法隐藏游戏窗口：未找到窗口（请先启动游戏）")
                    return False
            else:
                logger.warning("无法隐藏游戏窗口：未找到窗口")
                return False

        try:
            hwnd = self._cached_window.hwnd

            # 步骤 1：隐藏任务栏图标
            # 添加 WS_EX_TOOLWINDOW 样式（工具窗口，不显示在任务栏）
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_LAYERED = 0x00080000

            current_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # 添加工具窗口样式 + 分层窗口样式
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, current_style | WS_EX_TOOLWINDOW | WS_EX_LAYERED)

            # 步骤 2：设置透明度为 0（完全透明）
            LWA_ALPHA = 0x00000002
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)

            # 等待窗口状态稳定
            import time
            time.sleep(0.1)

            logger.info(f"游戏窗口已完美隐藏（任务栏图标隐藏 + 透明度 0）：{self._cached_window.title}")
            return True
        except Exception as e:
            logger.error(f"隐藏游戏窗口失败：{e}")
            return False

    def show_game_window(self) -> bool:
        """恢复游戏窗口的透明度和任务栏图标"""
        if not self._cached_window:
            logger.warning("无法恢复游戏窗口：未找到窗口")
            return False
        try:
            hwnd = self._cached_window.hwnd

            # 步骤 1：移除 WS_EX_TOOLWINDOW 样式，恢复任务栏图标
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080

            current_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # 移除工具窗口样式
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, current_style & ~WS_EX_TOOLWINDOW)

            # 等待样式应用完成
            import time
            time.sleep(0.05)

            # 步骤 2：恢复透明度为 255（完全不透明）
            LWA_ALPHA = 0x00000002
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)

            # 等待透明度应用完成
            time.sleep(0.05)

            # 步骤 3：温和刷新（避免过度刷新导致卡死）
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004

            ctypes.windll.user32.SetWindowPos(
                hwnd,
                None,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
            )

            # 等待刷新完成
            time.sleep(0.05)

            logger.info(f"游戏窗口已恢复：{self._cached_window.title}")
            return True
        except Exception as e:
            logger.error(f"恢复游戏窗口失败：{e}")
            return False


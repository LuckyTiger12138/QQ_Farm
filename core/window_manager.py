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

    def launch_game(self, shortcut_path: str) -> bool:
        """通过快捷方式启动游戏"""
        if not shortcut_path or not os.path.exists(shortcut_path):
            logger.warning(f"快捷方式路径无效：{shortcut_path}")
            return False
        try:
            # 启动快捷方式
            os.startfile(shortcut_path)
            logger.info(f"已启动游戏快捷方式：{shortcut_path}")
            return True
        except Exception as e:
            logger.error(f"启动游戏失败：{e}")
            return False

    def find_window(self, title_keyword: str = "QQ 经典农场", auto_launch: bool = False, shortcut_path: str = "") -> WindowInfo | None:
        """通过标题关键词查找窗口，可选自动启动游戏"""
        try:
            # 使用用户填写的关键词精确搜索
            windows = gw.getWindowsWithTitle(title_keyword)

            if windows:
                w = windows[0]
                return self._create_window_info(w)

            # 没找到则模糊搜索（包含关键词所有部分）
            if not windows:
                kw = title_keyword.lower().replace(" ", "")
                all_windows = gw.getAllWindows()
                for w in all_windows:
                    title = w.title.lower().replace(" ", "")
                    # 包含关键词的所有字符（如"QQ 农场"能匹配"QQ 经典农场"）
                    if all(c in title for c in kw):
                        windows = [w]
                        break

            # 再试一次：包含"农场"（排除"助手"）
            if not windows:
                for w in gw.getAllWindows():
                    if "农场" in w.title and "助手" not in w.title:
                        windows = [w]
                        logger.info(f"通过'农场'关键词找到窗口：{w.title}")
                        break

            # 如果还是没找到且启用了自动启动
            if not windows and auto_launch and shortcut_path:
                logger.info(f"未找到窗口 '{title_keyword}'，尝试启动游戏...")
                if self.launch_game(shortcut_path):
                    # 等待游戏启动（最多 30 秒）
                    logger.info("等待游戏启动（最多 30 秒）...")
                    for i in range(60):
                        time.sleep(0.5)
                        QCoreApplication.processEvents()

                        # 优先使用用户填写的关键词
                        windows = gw.getWindowsWithTitle(title_keyword)
                        if windows:
                            logger.info(f"游戏已启动，检测到窗口")
                            break

                        # 降级模糊搜索（排除助手窗口）
                        for w in gw.getAllWindows():
                            if "农场" in w.title and "助手" not in w.title:
                                windows = [w]
                                logger.info(f"检测到窗口：{w.title}")
                                break
                        if windows:
                            break
                    else:
                        # 超时后列出所有窗口帮助调试
                        all_titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
                        logger.warning(f"等待超时，当前窗口列表：{all_titles}")
                        return None

            if not windows:
                logger.warning(f"未找到包含 '{title_keyword}' 的窗口")
                return None

            w = windows[0]
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
        logger.debug(f"找到窗口：{info.title} ({info.width}x{info.height})")
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

    def resize_window(self, width: int, height: int) -> bool:
        """调整窗口大小并移到屏幕左下角（预留任务栏）"""
        if not self._cached_window:
            return False
        try:
            hwnd = self._cached_window.hwnd
            # 获取工作区域（排除任务栏）
            work_area = ctypes.wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0)
            pos_x = work_area.left
            pos_y = max(work_area.top, work_area.bottom - height)
            # ✅ 恢复使用 MoveWindow。
            # 之前尝试改用 SetWindowPos 配合 NOACTIVATE，但在初始化阶段导致了窗口被强制激活到前台。
            ctypes.windll.user32.MoveWindow(hwnd, pos_x, pos_y, width, height, True)
            self._cached_window.left = pos_x
            self._cached_window.top = pos_y
            self._cached_window.width = width
            self._cached_window.height = height
            logger.info(f"窗口调整为 {width}x{height}，位置({pos_x},{pos_y})")
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

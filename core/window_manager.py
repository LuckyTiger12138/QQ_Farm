"""窗口管理器 - 定位并管理微信小程序窗口"""
import ctypes
import ctypes.wintypes
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from loguru import logger

import pygetwindow as gw
from PyQt6.QtCore import QCoreApplication

from models.config import WindowPosition
from core.qq_client_recovery import QQClientRecovery, get_window_pid, list_qq_processes, resolve_qq_client


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int


class WindowManager:
    _all_claimed_hwnds: set[int] = set()  # 类级共享：所有实例已占用的窗口句柄
    _launch_lock = threading.Lock()  # 关闭/启动/选择账号在所有实例间串行执行

    def __init__(self):
        self._cached_window: WindowInfo | None = None
        self._pinned_hwnd: int | None = None  # 锁定的窗口句柄，防止多实例串台
        self._select_account_handled: set[int] = set()  # 已处理过的选择账号窗口 hwnd
        self._select_account_attempted_at: dict[int, float] = {}
        self._pending_resource_prompt: int | None = None
        self.last_launch_error = ""
        self.stopped: Callable[[], bool] = lambda: False
        self.qq_recovery_allowed: Callable[[], bool] = lambda: False
        self.qq_restart_wait_seconds: Callable[[], int] = lambda: 30
        self.notify: Callable[[str], None] = logger.info

    @classmethod
    def launch_in_progress(cls) -> bool:
        """窗口监控在任一实例启动/恢复期间暂缓判断，避免重复关闭窗口。"""
        return cls._launch_lock.locked()

    def _wait_for_launch(self, seconds: float) -> bool:
        """启动等待可随停止按钮中断，GUI 线程启动时仍处理界面事件。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.stopped():
                return False
            QCoreApplication.processEvents()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return not self.stopped()

    def _launch_and_wait(
        self, title_keyword: str, shortcut_path: str, account_keyword: str,
    ) -> list:
        """等待农场启动；确认为资源下载弹窗时，最多恢复一次 QQ 后重试。"""
        restarted_qq_pid: int | None = None
        for attempt in range(2):
            if self.stopped():
                self.last_launch_error = "已停止启动"
                return []
            existing_hwnds = {int(w._hWnd) for w in gw.getAllWindows()}
            existing_hwnds.discard(self._pending_resource_prompt)
            existing_game_hwnds = {int(w._hWnd) for w in self._list_all_windows(title_keyword)}
            self._select_account_handled.clear()
            self._select_account_attempted_at.clear()
            if not self.launch_game(shortcut_path):
                self.last_launch_error = "游戏快捷方式启动失败"
                return []
            logger.info("等待游戏启动（最多 30 秒）...")
            deadline = time.monotonic() + 30
            next_prompt_check = 0.0
            account_selected = False
            account_grace_extended = False
            while time.monotonic() < deadline:
                if not self._wait_for_launch(0.5):
                    self.last_launch_error = "已停止启动"
                    return []
                prompt = None
                if time.monotonic() >= next_prompt_check:
                    next_prompt_check = time.monotonic() + 2
                    try:
                        prompt = QQClientRecovery.find_resource_prompt(existing_hwnds)
                    except Exception as exc:
                        logger.debug(f"QQ 资源弹窗识别暂不可用: {type(exc).__name__}")
                if prompt:
                    logger.warning(f"资源下载弹窗已识别，进入 QQ 恢复流程: hwnd={prompt}")
                    self._pending_resource_prompt = prompt
                    self.last_launch_error = "QQ 小程序资源下载异常，请检查 QQ 登录状态"
                    if attempt:
                        self.notify("✗ QQ 重启后仍出现资源下载弹窗，本轮停止恢复，避免循环重启")
                        return []
                    if not self.qq_recovery_allowed():
                        self.notify("✗ 检测到资源下载弹窗；自动恢复或重启任务未启用，请手动重启 QQ")
                        return []
                    if not account_selected:
                        self.last_launch_error = "QQ 恢复前未确认目标账号，取消农场重启"
                        self.notify(f"✗ {self.last_launch_error}")
                        return []
                    restarted_qq_pid = QQClientRecovery.restart_for_prompt(prompt, self.stopped, self.notify)
                    if not restarted_qq_pid:
                        return []
                    self._pending_resource_prompt = None
                    self._pinned_hwnd = None
                    self._cached_window = None
                    self._all_claimed_hwnds.intersection_update({
                        hwnd for hwnd in self._all_claimed_hwnds
                        if ctypes.windll.user32.IsWindow(ctypes.wintypes.HWND(hwnd))
                    })
                    wait_seconds = max(1, min(120, self.qq_restart_wait_seconds()))
                    if not self._wait_for_launch(wait_seconds):
                        self.last_launch_error = "QQ 已重开，农场启动已停止"
                        return []
                    break
                if account_keyword:
                    if self._handle_select_account(existing_hwnds, account_keyword):
                        account_selected = True
                        if not account_grace_extended:
                            # QQ 账号确认后，资源弹窗可能延迟数分钟才出现。
                            deadline = max(deadline, time.monotonic() + 300)
                            account_grace_extended = True
                            logger.info("账号已确认，资源弹窗监测窗口延长至 5 分钟")
                if self.stopped():
                    self.last_launch_error = "已停止启动"
                    return []
                windows = [
                    w for w in self._list_all_windows(title_keyword)
                    if int(w._hWnd) not in existing_game_hwnds
                    and int(w._hWnd) not in self._all_claimed_hwnds
                ]
                if windows:
                    if account_keyword and not (account_selected or restarted_qq_pid):
                        logger.warning("发现农场窗口，但目标 QQ 账号尚未确认；拒绝绑定该窗口")
                        continue
                    if restarted_qq_pid:
                        processes = list_qq_processes()
                        windows = [
                            window for window in windows
                            if (owner := resolve_qq_client(get_window_pid(int(window._hWnd)), processes))
                            and owner.pid == restarted_qq_pid
                        ]
                        if not windows:
                            logger.warning("新农场窗口不属于刚重启的 QQ，拒绝绑定")
                            continue
                    self.last_launch_error = ""
                    return windows
            else:
                self.last_launch_error = "等待农场启动超时，请确认对应 QQ 账号已登录"
                self.notify(f"✗ {self.last_launch_error}")
                return []
        return []

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

    # ============================================================
    # 选择账号窗口检测与处理
    # ============================================================

    def _get_qq_hwnds(self) -> set[int]:
        """获取当前标题为 QQ 的窗口 hwnd 集合（轻量，不遍历全部窗口）"""
        hwnds: set[int] = set()
        try:
            for w in gw.getWindowsWithTitle("QQ"):
                hwnd = int(getattr(w, '_hWnd', 0) or 0)
                if hwnd > 0:
                    hwnds.add(hwnd)
        except Exception:
            pass
        return hwnds

    def _handle_select_account(self, existing_hwnds: set[int], keyword: str) -> bool:
        """检测选择账号窗口并处理：OCR 匹配关键词 → 点击账号 → 点击确定

        Args:
            existing_hwnds: 启动前已存在的 QQ 窗口 hwnd 集合
            keyword: 要匹配的 QQ 号关键词

        Returns:
            bool: 是否找到并处理了选择账号窗口
        """
        if not keyword or self.stopped():
            return False

        target = None
        try:
            for w in gw.getWindowsWithTitle("QQ"):
                hwnd = int(getattr(w, '_hWnd', 0) or 0)
                if hwnd <= 0 or hwnd in existing_hwnds or hwnd in self._select_account_handled:
                    continue
                fw = int(getattr(w, 'width', 0) or 0)
                fh = int(getattr(w, 'height', 0) or 0)
                if 200 < fw < 500 and 200 < fh < 500:
                    target = w
                    break
        except Exception:
            return False

        if target is None:
            return False

        hwnd = int(getattr(target, '_hWnd', 0) or 0)
        now = time.monotonic()
        if now - self._select_account_attempted_at.get(hwnd, -10.0) < 2:
            return False
        self._select_account_attempted_at[hwnd] = now
        logger.info(f"检测到选择账号窗口: hwnd={hwnd} {target.width}x{target.height}")

        from core.screen_capture import ScreenCapture
        sc = ScreenCapture()
        img = sc.capture((0, 0, 0, 0), hwnd=hwnd)
        if img is None:
            logger.warning("选择账号窗口截图失败")
            return False

        try:
            from utils.ocr_utils import OCRTool
            import cv2
            import numpy as np
        except ImportError:
            logger.warning("OCR 依赖不可用，无法自动选择账号")
            return False

        cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        ocr = OCRTool.get_instance()
        items = ocr.detect(cv_img)
        logger.debug(f"选择账号窗口 OCR: {[item.text for item in items]}")

        # 激活窗口到前台（Electron 窗口必须前台才能接收点击）
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        if not self._wait_for_launch(0.5):
            return False

        for item in items:
            if keyword in item.text:
                if self.stopped():
                    return False
                tw = int(getattr(target, 'width', 0) or 0)
                cx = int(tw * 0.12)
                cy = int(sum(p[1] for p in item.box) / 4)
                abs_x = int(target.left) + cx
                abs_y = int(target.top) + cy
                logger.info(f"选择账号: 匹配 '{keyword}' → 点击 ({abs_x}, {abs_y})")
                self._click_screen(abs_x, abs_y)
                if not self._wait_for_launch(1.0):
                    return False
                if not self._click_confirm(hwnd, target):
                    return False
                self._select_account_handled.add(hwnd)
                return True

        logger.warning(f"选择账号: 未找到包含 '{keyword}' 的账号")
        return False

    def _click_confirm(self, hwnd: int, target: gw.Win32Window) -> bool:
        """点击选择账号窗口的确定按钮"""
        if self.stopped():
            return False
        from core.screen_capture import ScreenCapture
        sc = ScreenCapture()
        img = sc.capture((0, 0, 0, 0), hwnd=hwnd)
        if img is not None:
            try:
                from utils.ocr_utils import OCRTool
                import cv2
                import numpy as np
                cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                ocr = OCRTool.get_instance()
                items = ocr.detect(cv_img)
                for item in items:
                    if "确定" in item.text or "进入" in item.text:
                        cx = int(sum(p[0] for p in item.box) / 4)
                        cy = int(sum(p[1] for p in item.box) / 4)
                        abs_x = int(target.left) + cx
                        abs_y = int(target.top) + cy
                        logger.info(f"点击确定: ({abs_x}, {abs_y})")
                        if self.stopped():
                            return False
                        self._click_screen(abs_x, abs_y)
                        return True
            except Exception as e:
                logger.debug(f"确定按钮 OCR 失败: {e}")

        fx = int(target.left) + int(target.width * 0.5)
        fy = int(target.top) + int(target.height * 0.85)
        logger.info(f"点击确定(兜底坐标): ({fx}, {fy})")
        if self.stopped():
            return False
        self._click_screen(fx, fy)
        return True

    def _click_screen(self, x: int, y: int) -> None:
        """在屏幕绝对坐标处模拟左键点击"""
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

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

    def find_window(
        self, title_keyword: str = "QQ 经典农场", auto_launch: bool = False,
        shortcut_path: str = "", select_rule: str = "auto",
        select_account_keyword: str = "", saved_hwnd: int = 0,
        force_restart: bool = False,
    ) -> WindowInfo | None:
        """串行查找/启动窗口；强制重启也在同一把锁内关闭旧窗口。"""
        if not self._launch_lock.acquire(blocking=False):
            self.last_launch_error = "其他实例正在启动或恢复 QQ，稍后重试"
            return None
        try:
            self.last_launch_error = ""
            if auto_launch and self.stopped():
                self.last_launch_error = "已停止启动"
                return None
            if force_restart:
                old_hwnd = self._pinned_hwnd
                if old_hwnd and self._verify_and_pin_window(old_hwnd, title_keyword):
                    ctypes.windll.user32.PostMessageW(ctypes.wintypes.HWND(old_hwnd), 0x0010, 0, 0)
                    deadline = time.monotonic() + 5
                    while ctypes.windll.user32.IsWindow(ctypes.wintypes.HWND(old_hwnd)):
                        if time.monotonic() >= deadline or not self._wait_for_launch(0.1):
                            self.last_launch_error = "旧游戏窗口未关闭或任务已停止"
                            return None
                self._all_claimed_hwnds.discard(old_hwnd)
                self._pinned_hwnd = None
                self._cached_window = None
                saved_hwnd = 0
                if auto_launch and shortcut_path:
                    windows = self._launch_and_wait(title_keyword, shortcut_path, select_account_keyword)
                    if not windows:
                        return None
                    window = windows[0]
                    self._pinned_hwnd = int(window._hWnd)
                    self._all_claimed_hwnds.add(self._pinned_hwnd)
                    return self._create_window_info(window)
            return self._find_window_locked(
                title_keyword, auto_launch, shortcut_path, select_rule, select_account_keyword, saved_hwnd,
            )
        except Exception as exc:
            self.last_launch_error = f"查找或重启窗口失败: {type(exc).__name__}"
            logger.exception(self.last_launch_error)
            return None
        finally:
            self._launch_lock.release()

    def _find_window_locked(self, title_keyword: str = "QQ 经典农场", auto_launch: bool = False, shortcut_path: str = "", select_rule: str = "auto", select_account_keyword: str = "", saved_hwnd: int = 0) -> WindowInfo | None:
        """通过标题关键词和选择规则查找窗口，可选自动启动游戏

        Args:
            title_keyword: 窗口标题关键词
            auto_launch: 未找到时是否自动启动
            shortcut_path: 游戏快捷方式路径
            select_rule: 窗口选择规则 ('auto' 或 'index:N')
            select_account_keyword: 选择账号窗口匹配关键词（QQ号）
            saved_hwnd: 上次启动时的窗口句柄，重启后优先复用（持久化匹配）
        """
        try:
            # 0. 持久化匹配：优先尝试上次保存的窗口句柄（重启后复用）
            if saved_hwnd and not self._pinned_hwnd:
                if saved_hwnd not in self._all_claimed_hwnds:
                    valid_window = self._verify_and_pin_window(saved_hwnd, title_keyword)
                    if valid_window:
                        self._pinned_hwnd = saved_hwnd
                        self._all_claimed_hwnds.add(saved_hwnd)
                        logger.info(f"持久化匹配: 复用上次窗口 hwnd={saved_hwnd} → 实例窗口已恢复")
                        return valid_window

            # 1. 优先复用已锁定的窗口（防止窗口移动后排序变化导致选错）
            if self._pinned_hwnd:
                valid_window = self._verify_and_pin_window(self._pinned_hwnd, title_keyword)
                if valid_window:
                    return valid_window
                else:
                    lost = self._pinned_hwnd
                    logger.warning(f"已锁定窗口 (hwnd={lost}) 已丢失，重新查找...")
                    self._pinned_hwnd = None
                    self._all_claimed_hwnds.discard(lost)

            # 2. 列出所有匹配窗口
            windows = self._list_all_windows(title_keyword)

            # 2.5 多实例模式：过滤掉已被其他实例占用的窗口
            if not self._pinned_hwnd:
                claimed_by_others = self._all_claimed_hwnds - {self._pinned_hwnd}
                if claimed_by_others:
                    before = len(windows)
                    windows = [
                        w for w in windows
                        if int(getattr(w, '_hWnd', 0) or 0) not in claimed_by_others
                    ]
                    if len(windows) < before:
                        logger.info(
                            f"多实例模式：过滤掉 {before - len(windows)} 个"
                            f"已被其他实例占用的窗口，剩余 {len(windows)} 个"
                        )

            if not windows:
                # 如果还是没找到且启用了自动启动
                if auto_launch and shortcut_path:
                    logger.info(f"未找到窗口 '{title_keyword}'，尝试启动游戏...")
                    windows = self._launch_and_wait(title_keyword, shortcut_path, select_account_keyword)
                    if not windows:
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
            self._all_claimed_hwnds.add(self._pinned_hwnd)
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


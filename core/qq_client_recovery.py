"""QQ 小程序资源弹窗识别与所属客户端的限次恢复。"""

import ctypes
import ctypes.wintypes
import json
import ntpath
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger
import win32api


@dataclass(frozen=True)
class QQProcess:
    pid: int
    parent_pid: int
    executable: str
    command_line: str
    created_at: str


def is_resource_download_prompt(text: str) -> bool:
    """只接受小程序工具资源下载提示，避免把普通下载确认当成故障。"""
    compact = re.sub(r"\s+", "", text)
    return all(word in compact for word in ("首次使用", "小程序", "工具资源", "下载"))


def get_window_pid(hwnd: int) -> int:
    """读取窗口所属进程，句柄失效时返回 0。"""
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(ctypes.wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def get_process_image(pid: int) -> str:
    """用有限查询权限读取进程路径，不读取进程内存。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
        ctypes.wintypes.LPWSTR, ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def list_qq_processes() -> dict[int, QQProcess]:
    """获取 QQ 进程的父子关系和原始启动命令，供精确恢复使用。"""
    script = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "@(Get-CimInstance Win32_Process -Filter \"Name = 'QQ.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,"
        "@{Name='CreatedAt';Expression={$_.CreationDate.ToUniversalTime().Ticks.ToString()}}) | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, encoding="utf-8-sig", errors="replace", timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW, check=True,
    )
    rows = json.loads(result.stdout.strip() or "[]") or []
    if isinstance(rows, dict):
        rows = [rows]
    return {
        int(row["ProcessId"]): QQProcess(
            pid=int(row["ProcessId"]), parent_pid=int(row["ParentProcessId"]),
            executable=str(row.get("ExecutablePath") or ""),
            command_line=str(row.get("CommandLine") or ""),
            created_at=str(row.get("CreatedAt") or ""),
        )
        for row in rows
    }


def resolve_qq_client(pid: int, processes: dict[int, QQProcess]) -> QQProcess | None:
    """沿相同安装路径的父链找主 QQ；不能确认归属时不扩大结束范围。"""
    current = processes.get(pid)
    visited: set[int] = set()
    while current and current.pid not in visited:
        visited.add(current.pid)
        if ntpath.basename(current.executable).lower() != "qq.exe":
            return None
        parent = processes.get(current.parent_pid)
        if parent and ntpath.normcase(parent.executable) == ntpath.normcase(current.executable):
            current = parent
            continue
        command = current.command_line.lower()
        if not command or not current.created_at:
            return None
        if any(flag in command for flag in ("--type=", "--loadapp=", "--exapp=", "--pcqq-platform-channel-handle=")):
            return None
        return current
    return None


class QQClientRecovery:
    """只恢复弹窗所属的 QQ 进程树；同一安装路径五分钟内最多尝试一次。"""

    _last_attempts: dict[str, float] = {}
    COOLDOWN_SECONDS = 300.0

    @staticmethod
    def _login_restarted_client(pid: int, stopped: Callable[[], bool]) -> bool:
        """只操作刚重启的 QQ 所属登录窗口，等同一进程主界面出现后才继续。"""
        import cv2
        import numpy as np
        import pyautogui
        import pygetwindow as gw
        from core.screen_capture import ScreenCapture
        from utils.ocr_utils import OCRTool

        deadline = time.monotonic() + 90
        capture = ScreenCapture()
        clicked = False
        while time.monotonic() < deadline and not stopped():
            windows = [
                window for window in gw.getWindowsWithTitle("QQ")
                if window.title == "QQ" and get_window_pid(int(window._hWnd)) == pid
            ]
            login_windows = [
                window for window in windows
                if 250 <= window.width <= 500 and 350 <= window.height <= 650
            ]
            main_windows = [
                window for window in windows
                if window.width > 600 and window.height > 500
            ]
            if main_windows and not login_windows:
                logger.info(f"QQ 主界面已出现，登录完成: pid={pid}")
                return True
            for window in login_windows:
                img = capture.capture((0, 0, 0, 0), hwnd=int(window._hWnd))
                if img is None:
                    continue
                items = OCRTool.get_instance().detect(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
                buttons = [item for item in items if item.text.strip() == "登录"]
                if not buttons or clicked:
                    continue
                button = buttons[-1]
                x = int(window.left + sum(p[0] for p in button.box) / 4)
                y = int(window.top + sum(p[1] for p in button.box) / 4)
                if stopped():
                    return False
                ctypes.windll.user32.ShowWindow(int(window._hWnd), 9)
                ctypes.windll.user32.SetForegroundWindow(int(window._hWnd))
                pyautogui.click(x, y)
                clicked = True
                logger.info(f"已点击重启 QQ 的登录按钮: pid={pid}")
            time.sleep(1)
        logger.warning("QQ 登录未完成，停止本轮农场启动")
        return False

    @staticmethod
    def find_resource_prompt(existing_hwnds: set[int]) -> int | None:
        """扫描 QQ 小窗口；资源弹窗可能复用已有句柄，必须以 OCR 文本为准。"""
        import pygetwindow as gw
        import cv2
        import numpy as np
        from core.screen_capture import ScreenCapture
        from utils.ocr_utils import OCRTool

        capture = ScreenCapture()
        for window in gw.getAllWindows():
            hwnd = int(getattr(window, "_hWnd", 0) or 0)
            if not hwnd:
                continue
            if not (240 <= window.width <= 1000 and 100 <= window.height <= 650):
                continue
            if ntpath.basename(get_process_image(get_window_pid(hwnd))).lower() != "qq.exe":
                continue
            screenshot = capture.capture((0, 0, 0, 0), hwnd=hwnd)
            if screenshot is None:
                continue
            bgr = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
            text = "".join(item.text for item in OCRTool.get_instance().detect(bgr))
            if is_resource_download_prompt(text):
                logger.warning(f"检测到 QQ 小程序工具资源下载弹窗: hwnd={hwnd}")
                return hwnd
        return None

    @classmethod
    def restart_for_prompt(
        cls, hwnd: int, stopped: Callable[[], bool], notify: Callable[[str], None],
    ) -> int | None:
        """核实弹窗所属 QQ 后重开原启动命令，返回新主进程 PID。"""
        if stopped():
            return None
        try:
            pid = get_window_pid(hwnd)
            client = resolve_qq_client(pid, list_qq_processes())
            if client is None or not os.path.isfile(client.executable):
                notify("✗ 无法确认资源弹窗所属的 QQ 主进程，请手动重启对应 QQ")
                return None
            key = ntpath.normcase(client.executable)
            last_attempt = cls._last_attempts.get(key)
            if last_attempt is not None and time.monotonic() - last_attempt < cls.COOLDOWN_SECONDS:
                notify("✗ QQ 资源恢复处于 5 分钟冷却期，请检查 QQ 登录状态")
                return None
            # 保持进程对象句柄，防止核验后 PID 被系统复用于其他进程。
            process_handle = win32api.OpenProcess(0x1000, False, client.pid)
            try:
                current = resolve_qq_client(get_window_pid(hwnd), list_qq_processes())
                if current != client or stopped():
                    notify("✗ QQ 进程已变化或任务已停止，取消本次资源恢复")
                    return None
                cls._last_attempts[key] = time.monotonic()
                notify(f"检测到资源下载异常，正在重启对应 QQ 客户端（PID {client.pid}）...")
                subprocess.run(
                    ["taskkill.exe", "/PID", str(client.pid), "/T", "/F"],
                    capture_output=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW, check=True,
                )
            finally:
                process_handle.Close()
            # 结束后必须先重开 QQ，即使这时用户停止了农场任务。
            restarted = subprocess.Popen(
                client.command_line, executable=client.executable,
                cwd=ntpath.dirname(client.executable),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            notify("QQ 客户端已重新启动，正在完成对应账号登录")
            if not stopped() and not cls._login_restarted_client(restarted.pid, stopped):
                notify("✗ QQ 登录未完成，未继续启动农场")
                return None
            return restarted.pid
        except Exception as exc:
            logger.error(f"QQ 资源异常恢复失败: {type(exc).__name__}")
            notify("✗ QQ 自动恢复失败，请手动重启并确认原账号已登录")
            return None

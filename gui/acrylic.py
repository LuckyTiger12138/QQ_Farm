"""Windows Acrylic / Mica 毛玻璃效果 — ctypes 封装

支持:
  - Windows 11 22H2+: Mica 效果 (DwmSetWindowAttribute)
  - Windows 10 1803+:  Acrylic 半透明模糊 (SetWindowCompositionAttribute)
  - Fallback: 纯 CSS 半透明背景（无原生模糊）
"""
import sys
import logging
from ctypes import (
    c_int, wintypes, Structure, byref, sizeof,
    POINTER, windll, pointer,
)

logger = logging.getLogger(__name__)


# ── Windows 结构体定义 ──────────────────────────────────────

class ACCENT_POLICY(Structure):
    _fields_ = [
        ("AccentState", c_int),
        ("AccentFlags", c_int),
        ("GradientColor", wintypes.DWORD),
        ("AnimationId", wintypes.DWORD),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(Structure):
    _fields_ = [
        ("Attribute", c_int),
        ("Data", POINTER(ACCENT_POLICY)),
        ("SizeOfData", wintypes.UINT),
    ]


class MARGINS(Structure):
    _fields_ = [
        ("cxLeftWidth", c_int),
        ("cxRightWidth", c_int),
        ("cyTopHeight", c_int),
        ("cyBottomHeight", c_int),
    ]


# 常量
WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
ACCENT_ENABLE_HOSTBACKDROP = 5  # Windows 11 21H2+
ACCENT_DISABLED = 0

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_MICA_EFFECT = 1029           # Win11 21H2
DWMWA_SYSTEMBACKDROP_TYPE = 38     # Win11 22H2+ (Mica=2, Acrylic=3, Tabbed=4)

DWMWA_CAPTION_COLOR = 35           # Win11: 标题栏颜色


def _is_windows() -> bool:
    return sys.platform == "win32"


def _build_number() -> int:
    if not _is_windows():
        return 0
    return sys.getwindowsversion().build


def enable_acrylic(hwnd: int, gradient_color: int = 0xD9000000) -> bool:
    """启用 Windows 10 1803+ Acrylic 模糊效果

    Args:
        hwnd: 窗口句柄
        gradient_color: AABBGGRR 格式的渐变色（默认 85% 黑色）

    Returns:
        是否成功
    """
    if not _is_windows():
        return False

    try:
        accent = ACCENT_POLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2  # ACCENT_FLAG_DRAW_ALL
        accent.GradientColor = gradient_color

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = pointer(accent)
        data.SizeOfData = sizeof(accent)

        result = windll.user32.SetWindowCompositionAttribute(hwnd, byref(data))
        extend_frame_into_client_area(hwnd, True)
        if result:
            logger.info("Acrylic 效果已启用")
        else:
            logger.warning("SetWindowCompositionAttribute 返回 False")
        return bool(result)
    except Exception as e:
        logger.warning(f"启用 Acrylic 失败: {e}")
        return False


def enable_mica(hwnd: int) -> bool:
    """启用 Windows 11 Mica 效果（优先尝试 22H2+ API）

    Returns:
        是否成功
    """
    if not _is_windows():
        return False

    build = _build_number()
    if build < 22000:
        return False

    try:
        dwm = windll.dwmapi
        extend_frame_into_client_area(hwnd, True)

        # 先设置暗色模式
        dark_mode = c_int(0)
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            byref(dark_mode), sizeof(dark_mode),
        )

        if build >= 22523:
            # Win11 22H2+: 使用 SystemBackdropType
            backdrop_type = c_int(2)  # Mica
            result = dwm.DwmSetWindowAttribute(
                hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                byref(backdrop_type), sizeof(backdrop_type),
            )
        else:
            # Win11 21H2: 使用旧 Mica 属性
            mica = c_int(1)
            result = dwm.DwmSetWindowAttribute(
                hwnd, DWMWA_MICA_EFFECT,
                byref(mica), sizeof(mica),
            )

        if result == 0:
            logger.info("Mica 效果已启用")
            return True
        else:
            logger.warning(f"DwmSetWindowAttribute 返回 {result}")
            return False
    except Exception as e:
        logger.warning(f"启用 Mica 失败: {e}")
        return False


def enable_blur(hwnd: int) -> bool:
    """自动选择最佳毛玻璃效果

    优先级: Mica (Win11 22H2+) > Acrylic (Win10 1803+)
    """
    build = _build_number()

    if build >= 22000:
        if enable_mica(hwnd):
            return True
        logger.info("Mica 不可用，尝试 Acrylic")

    return enable_acrylic(hwnd, 0x99F7F5F5)


def extend_frame_into_client_area(hwnd: int, enabled: bool) -> bool:
    """让 DWM 背景延伸到客户区，Mica/Acrylic 才能在 Qt 内容背后透出。"""
    if not _is_windows():
        return False
    try:
        margins = MARGINS(-1, -1, -1, -1) if enabled else MARGINS(0, 0, 0, 0)
        result = windll.dwmapi.DwmExtendFrameIntoClientArea(
            wintypes.HWND(hwnd),
            byref(margins),
        )
        if result != 0:
            logger.debug(f"DwmExtendFrameIntoClientArea 返回 {result}")
        return result == 0
    except Exception as e:
        logger.debug(f"DwmExtendFrameIntoClientArea 失败: {e}")
        return False


def disable_blur(hwnd: int) -> bool:
    """关闭 Mica/Acrylic 效果，恢复普通窗口背景。"""
    if not _is_windows():
        return False

    ok = True
    ok = extend_frame_into_client_area(hwnd, False) and ok
    try:
        build = _build_number()
        if build >= 22000:
            dwm = windll.dwmapi
            if build >= 22523:
                backdrop_type = c_int(0)
                result = dwm.DwmSetWindowAttribute(
                    hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                    byref(backdrop_type), sizeof(backdrop_type),
                )
                ok = ok and result == 0
            mica = c_int(0)
            result = dwm.DwmSetWindowAttribute(
                hwnd, DWMWA_MICA_EFFECT,
                byref(mica), sizeof(mica),
            )
            ok = ok and result == 0
    except Exception as e:
        logger.debug(f"关闭 Mica 失败: {e}")
        ok = False

    try:
        accent = ACCENT_POLICY()
        accent.AccentState = ACCENT_DISABLED
        accent.AccentFlags = 0
        accent.GradientColor = 0
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = pointer(accent)
        data.SizeOfData = sizeof(accent)
        result = windll.user32.SetWindowCompositionAttribute(hwnd, byref(data))
        ok = ok and bool(result)
    except Exception as e:
        logger.debug(f"关闭 Acrylic 失败: {e}")
        ok = False

    if ok:
        logger.info("窗口材质效果已关闭")
    return ok

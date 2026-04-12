"""屏幕捕获模块 — 支持前台 mss 和后台 PrintWindow"""
import ctypes
import ctypes.wintypes
import os
import threading
import time
from loguru import logger
from PIL import Image
import mss

from utils.image_utils import save_screenshot


class ScreenCapture:
    def __init__(self, save_dir: str = "screenshots"):
        self._save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)
        # 每个实例独立的截图锁，防止同一实例内部并发
        self._lock = threading.Lock()

    def capture_region(self, rect: tuple[int, int, int, int]) -> Image.Image | None:
        """前台截取指定区域 (left, top, width, height)"""
        left, top, width, height = rect
        monitor = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }
        try:
            with mss.mss() as sct:
                screenshot = sct.grab(monitor)
                image = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            return image
        except Exception as e:
            logger.error(f"截屏失败: {e}")
            return None

    def capture_window_print(self, hwnd: int) -> Image.Image | None:
        """后台截图：使用 PrintWindow 读取窗口位图，窗口无需在前台"""
        if not hwnd:
            return None

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # 获取窗口尺寸
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return None

        # 方案 1：使用 PrintWindow（PW_RENDERFULLCONTENT=2 支持硬件加速）
        # 每个实例独立的 DC 和位图对象，确保隔离性
        hwnd_dc = user32.GetWindowDC(ctypes.wintypes.HWND(hwnd))
        if not hwnd_dc:
            return None

        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        if not mem_dc:
            user32.ReleaseDC(ctypes.wintypes.HWND(hwnd), hwnd_dc)
            return None

        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
        if not bitmap:
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(ctypes.wintypes.HWND(hwnd), hwnd_dc)
            return None

        old_obj = gdi32.SelectObject(mem_dc, bitmap)
        try:
            # PW_RENDERFULLCONTENT = 2，支持 DirectX/硬件加速内容
            PW_RENDERFULLCONTENT = 0x00000002
            ok = user32.PrintWindow(ctypes.wintypes.HWND(hwnd), mem_dc, PW_RENDERFULLCONTENT)
            if not ok:
                ok = user32.PrintWindow(ctypes.wintypes.HWND(hwnd), mem_dc, 0)
            
            # 如果 PrintWindow 失败，回退到 BitBlt（需要窗口可见）
            if not ok:
                logger.debug("PrintWindow 失败，回退到 BitBlt")
                gdi32.SelectObject(mem_dc, old_obj)
                gdi32.DeleteObject(bitmap)
                return self._capture_window_bitblt(hwnd, width, height)

            # 读取位图像素数据
            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.wintypes.DWORD),
                    ("biWidth", ctypes.wintypes.LONG),
                    ("biHeight", ctypes.wintypes.LONG),
                    ("biPlanes", ctypes.wintypes.WORD),
                    ("biBitCount", ctypes.wintypes.WORD),
                    ("biCompression", ctypes.wintypes.DWORD),
                    ("biSizeImage", ctypes.wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.wintypes.LONG),
                    ("biYPelsPerMeter", ctypes.wintypes.LONG),
                    ("biClrUsed", ctypes.wintypes.DWORD),
                    ("biClrImportant", ctypes.wintypes.DWORD),
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height  # 负值表示自顶向下
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0  # BI_RGB

            buf_len = width * height * 4
            buffer = ctypes.create_string_buffer(buf_len)
            rows = gdi32.GetDIBits(
                mem_dc, bitmap, 0, height,
                buffer, ctypes.byref(bmi), 0,  # DIB_RGB_COLORS
            )
            if rows != height:
                logger.debug(f"GetDIBits 行数异常 ({rows}/{height})")
                return None

            return Image.frombytes("RGB", (width, height), buffer.raw, "raw", "BGRX")
        except Exception as e:
            logger.error(f"PrintWindow 截图失败: {e}")
            return None
        finally:
            if old_obj:
                gdi32.SelectObject(mem_dc, old_obj)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(ctypes.wintypes.HWND(hwnd), hwnd_dc)

    def _capture_window_bitblt(self, hwnd: int, width: int, height: int) -> Image.Image | None:
        """使用 BitBlt 截取窗口（需要窗口可见，非最小化）"""
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        hwnd_dc = user32.GetWindowDC(ctypes.wintypes.HWND(hwnd))
        if not hwnd_dc:
            return None

        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        if not mem_dc:
            user32.ReleaseDC(ctypes.wintypes.HWND(hwnd), hwnd_dc)
            return None

        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
        if not bitmap:
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(ctypes.wintypes.HWND(hwnd), hwnd_dc)
            return None

        old_obj = gdi32.SelectObject(mem_dc, bitmap)
        try:
            # SRCCOPY = 0x00CC0020
            SRCCOPY = 0x00CC0020
            ok = gdi32.BitBlt(mem_dc, 0, 0, width, height, hwnd_dc, 0, 0, SRCCOPY)
            if not ok:
                logger.debug("BitBlt 失败")
                return None

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.wintypes.DWORD),
                    ("biWidth", ctypes.wintypes.LONG),
                    ("biHeight", ctypes.wintypes.LONG),
                    ("biPlanes", ctypes.wintypes.WORD),
                    ("biBitCount", ctypes.wintypes.WORD),
                    ("biCompression", ctypes.wintypes.DWORD),
                    ("biSizeImage", ctypes.wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.wintypes.LONG),
                    ("biYPelsPerMeter", ctypes.wintypes.LONG),
                    ("biClrUsed", ctypes.wintypes.DWORD),
                    ("biClrImportant", ctypes.wintypes.DWORD),
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0

            buf_len = width * height * 4
            buffer = ctypes.create_string_buffer(buf_len)
            rows = gdi32.GetDIBits(
                mem_dc, bitmap, 0, height,
                buffer, ctypes.byref(bmi), 0,
            )
            if rows != height:
                return None

            return Image.frombytes("RGB", (width, height), buffer.raw, "raw", "BGRX")
        except Exception as e:
            logger.error(f"BitBlt 截图失败: {e}")
            return None
        finally:
            if old_obj:
                gdi32.SelectObject(mem_dc, old_obj)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(ctypes.wintypes.HWND(hwnd), hwnd_dc)

    def capture(self, rect: tuple[int, int, int, int],
                hwnd: int | None = None) -> Image.Image | None:
        """智能截图：有 hwnd 时优先后台截图，否则前台截图"""
        if hwnd:
            image = self.capture_window_print(hwnd)
            if image is not None:
                return image
            # 后台失败时回退前台截图（使用 hwnd 重新获取窗口位置）
            logger.warning("PrintWindow 后台截图失败，尝试 BitBlt 回退")
            image = self._capture_window_bitblt_from_hwnd(hwnd)
            if image is not None:
                return image
            # 如果 BitBlt 也失败，最后回退到前台截图
            logger.warning("BitBlt 也失败，回退前台截图（可能截取到其他窗口）")
        return self.capture_region(rect)

    def _capture_window_bitblt_from_hwnd(self, hwnd: int) -> Image.Image | None:
        """从 hwnd 获取窗口尺寸并使用 BitBlt 截图"""
        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return None
        return self._capture_window_bitblt(hwnd, width, height)

    def capture_and_save(self, rect: tuple[int, int, int, int],
                         prefix: str = "farm",
                         hwnd: int | None = None) -> tuple[Image.Image | None, str]:
        """截屏并保存到文件，返回(图片, 文件路径)"""
        image = self.capture(rect, hwnd=hwnd)
        if image is None:
            return None, ""
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{ts}.png"
        filepath = os.path.join(self._save_dir, filename)
        save_screenshot(image, filepath)
        return image, filepath

    def cleanup_old_screenshots(self, max_count: int = 50):
        """清理旧截图，保留最新的max_count张"""
        try:
            files = sorted(
                [os.path.join(self._save_dir, f) for f in os.listdir(self._save_dir)
                 if f.endswith(".png")],
                key=os.path.getmtime
            )
            if len(files) > max_count:
                for f in files[:len(files) - max_count]:
                    os.remove(f)
        except Exception as e:
            logger.warning(f"清理截图失败: {e}")

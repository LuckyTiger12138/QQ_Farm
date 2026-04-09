"""操作执行器 - 支持前台 pyautogui 和后台 PostMessageW"""
import ctypes
import ctypes.wintypes
import random
import time
from loguru import logger

import pyautogui
from models.config import RunMode
from models.farm_state import Action, OperationResult

# Win32 鼠标消息常量
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

user32 = ctypes.windll.user32

# 禁用 pyautogui 的安全暂停（我们自己控制延迟）
pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = True  # 鼠标移到左上角可紧急停止


class ActionExecutor:
    def __init__(self, window_rect: tuple[int, int, int, int],
                 hwnd: int | None = None,
                 run_mode: RunMode = RunMode.FOREGROUND,
                 delay_min: float = 0.5, delay_max: float = 2.0,
                 click_offset: int = 5):
        self._window_left = window_rect[0]
        self._window_top = window_rect[1]
        self._window_width = window_rect[2]
        self._window_height = window_rect[3]
        self._hwnd = hwnd
        self._run_mode = run_mode
        self._delay_min = delay_min
        self._delay_max = delay_max
        self._click_offset = click_offset

    def update_window_rect(self, rect: tuple[int, int, int, int]):
        self._window_left, self._window_top = rect[0], rect[1]
        self._window_width, self._window_height = rect[2], rect[3]

    def update_window_handle(self, hwnd: int | None):
        self._hwnd = hwnd

    @property
    def is_background(self) -> bool:
        return self._run_mode == RunMode.BACKGROUND and self._hwnd is not None

    def relative_to_absolute(self, rel_x: int, rel_y: int) -> tuple[int, int]:
        """将相对于窗口的坐标转为屏幕绝对坐标"""
        abs_x = self._window_left + rel_x
        abs_y = self._window_top + rel_y
        return abs_x, abs_y

    def _random_offset(self) -> tuple[int, int]:
        ox = random.randint(-self._click_offset, self._click_offset)
        oy = random.randint(-self._click_offset, self._click_offset)
        return ox, oy

    def _random_delay(self):
        time.sleep(0.3)

    @staticmethod
    def _make_lparam(x: int, y: int) -> int:
        """构造鼠标消息的 lparam（低16位x，高16位y）"""
        return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)

    def _screen_to_client(self, abs_x: int, abs_y: int) -> tuple[int, int] | None:
        """屏幕坐标转窗口客户区坐标"""
        if not self._hwnd:
            return None
        point = ctypes.wintypes.POINT(int(abs_x), int(abs_y))
        ok = user32.ScreenToClient(ctypes.wintypes.HWND(self._hwnd), ctypes.byref(point))
        if not ok:
            return None
        return int(point.x), int(point.y)

    def _click_background(self, abs_x: int, abs_y: int) -> bool:
        """后台消息点击：通过 PostMessageW 发送鼠标消息"""
        if not self._hwnd:
            return False
        client = self._screen_to_client(abs_x, abs_y)
        if not client:
            return False
        cx, cy = client
        lparam = self._make_lparam(cx, cy)
        hwnd = ctypes.wintypes.HWND(self._hwnd)
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)
        user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(0.03)
        user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
        return True

    def _click_foreground(self, abs_x: int, abs_y: int) -> bool:
        """前台鼠标点击"""
        pyautogui.moveTo(int(abs_x), int(abs_y), duration=0.02)
        time.sleep(0.05)
        pyautogui.click(int(abs_x), int(abs_y))
        return True

    def drag(self, x: int, y: int, dx: int, dy: int,
             duration: float = 0.3, steps: int = 10) -> bool:
        """从 (x,y) 拖拽到 (x+dx, y+dy)

        后台模式通过 PostMessage 发送 MOUSEMOVE 序列模拟拖拽。
        """
        try:
            ox, oy = self._random_offset()
            sx, sy = x + ox, y + oy
            ex, ey = sx + dx, sy + dy

            if self.is_background:
                return self._drag_background(sx, sy, ex, ey, steps)
            else:
                pyautogui.moveTo(int(sx), int(sy), duration=0.02)
                pyautogui.drag(int(dx), int(dy), duration=duration)
                return True
        except Exception as e:
            logger.error(f"拖拽失败: {e}")
            return False

    def _drag_background(self, sx: int, sy: int,
                         ex: int, ey: int, steps: int = 10) -> bool:
        """后台模式拖拽：发送 MOUSEMOVE 序列"""
        if not self._hwnd:
            return False
        hwnd = ctypes.wintypes.HWND(self._hwnd)

        start = self._screen_to_client(sx, sy)
        end = self._screen_to_client(ex, ey)
        if not start or not end:
            return False

        # 按下
        lparam = self._make_lparam(*start)
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)
        user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(0.02)

        # 移动
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(start[0] + (end[0] - start[0]) * t)
            cy = int(start[1] + (end[1] - start[1]) * t)
            lparam = self._make_lparam(cx, cy)
            user32.PostMessageW(hwnd, WM_MOUSEMOVE, MK_LBUTTON, lparam)
            time.sleep(0.02)

        # 释放
        lparam = self._make_lparam(*end)
        user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
        return True

    def drag_multi_points(self, start_x: int, start_y: int,
                          points: list[tuple[int, int]],
                          check_stopped=None,
                          steps_per_point: int = 10) -> bool:
        """按住起点，依次拖过多个目标点后释放。

        后台模式使用 PostMessageW，前台模式使用 pyautogui。
        每步检查 check_stopped 回调，返回 True 表示应中断。

        Args:
            start_x, start_y: 起点（屏幕绝对坐标）
            points: 目标点列表 [(x, y), ...]（屏幕绝对坐标）
            check_stopped: 无参回调，返回 True 时中断拖拽
            steps_per_point: 每个目标点的插值步数
        Returns:
            True 完成, False 被中断或失败
        """
        if self.is_background:
            return self._drag_multi_points_background(
                start_x, start_y, points, check_stopped, steps_per_point)

        # ── 前台模式 ──
        try:
            pyautogui.moveTo(int(start_x), int(start_y), duration=0.05)
            for _ in range(5):
                if check_stopped and check_stopped():
                    return False
                time.sleep(0.05)
            pyautogui.mouseDown()
            for _ in range(2):
                if check_stopped and check_stopped():
                    pyautogui.mouseUp()
                    return False
                time.sleep(0.05)

            for px, py_ in points:
                if check_stopped and check_stopped():
                    pyautogui.mouseUp()
                    return False
                for _ in range(steps_per_point):
                    if check_stopped and check_stopped():
                        pyautogui.mouseUp()
                        return False
                    pyautogui.moveTo(int(px), int(py_), duration=0.01)

            pyautogui.mouseUp()
            return True
        except Exception as e:
            logger.error(f"前台拖拽多点失败: {e}")
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            return False

    def _drag_multi_points_background(self, start_x: int, start_y: int,
                                       points: list[tuple[int, int]],
                                       check_stopped=None,
                                       steps_per_point: int = 10) -> bool:
        """后台模式：按住起点 → 依次拖过多个目标点 → 释放"""
        if not self._hwnd:
            return False
        hwnd = ctypes.wintypes.HWND(self._hwnd)

        start_client = self._screen_to_client(start_x, start_y)
        if not start_client:
            return False

        # 按下
        lparam = self._make_lparam(*start_client)
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)
        user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(0.05)

        # 依次拖到每个目标点
        for px, py_ in points:
            if check_stopped and check_stopped():
                lparam = self._make_lparam(*start_client)
                user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
                return False
            end_client = self._screen_to_client(px, py_)
            if not end_client:
                continue
            for i in range(1, steps_per_point + 1):
                if check_stopped and check_stopped():
                    lparam = self._make_lparam(*end_client)
                    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
                    return False
                t = i / steps_per_point
                cx = int(start_client[0] + (end_client[0] - start_client[0]) * t)
                cy = int(start_client[1] + (end_client[1] - start_client[1]) * t)
                lparam = self._make_lparam(cx, cy)
                user32.PostMessageW(hwnd, WM_MOUSEMOVE, MK_LBUTTON, lparam)
                time.sleep(0.01)
            # 更新起点为当前点，下次从此处开始插值
            start_client = end_client

        # 释放
        if start_client:
            lparam = self._make_lparam(*start_client)
            user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
        return True

    def click(self, x: int, y: int) -> bool:
        """点击指定坐标，自动选择后台/前台模式"""
        try:
            ox, oy = self._random_offset()
            target_x = x + ox
            target_y = y + oy

            if self.is_background:
                ok = self._click_background(target_x, target_y)
            else:
                ok = self._click_foreground(target_x, target_y)

            if ok:
                logger.debug(f"点击 ({target_x}, {target_y}) [{'后台' if self.is_background else '前台'}]")
            return ok
        except Exception as e:
            logger.error(f"点击失败: {e}")
            return False

    def execute_action(self, action: Action) -> OperationResult:
        """执行单个操作"""
        pos = action.click_position
        if not pos or "x" not in pos or "y" not in pos:
            return OperationResult(
                action=action, success=False,
                message="缺少点击坐标", timestamp=time.time()
            )

        # 转换坐标
        abs_x, abs_y = self.relative_to_absolute(int(pos["x"]), int(pos["y"]))

        # 检查坐标是否在窗口范围内
        if not (self._window_left <= abs_x <= self._window_left + self._window_width and
                self._window_top <= abs_y <= self._window_top + self._window_height):
            return OperationResult(
                action=action, success=False,
                message=f"坐标 ({abs_x},{abs_y}) 超出窗口范围",
                timestamp=time.time()
            )

        success = self.click(abs_x, abs_y)
        self._random_delay()

        return OperationResult(
            action=action, success=success,
            message=action.description if success else "点击失败",
            timestamp=time.time()
        )

    def execute_actions(self, actions: list[Action],
                        max_count: int = 20) -> list[OperationResult]:
        """按优先级执行操作序列"""
        results = []
        executed = 0

        for action in actions:
            if executed >= max_count:
                logger.info(f"已达到单轮最大操作数 {max_count}，停止执行")
                break

            logger.info(f"执行: {action.description} (优先级:{action.priority})")
            result = self.execute_action(action)
            results.append(result)

            if result.success:
                executed += 1
                logger.info(f"✓ {action.description}")
            else:
                logger.warning(f"✗ {action.description}: {result.message}")

        return results

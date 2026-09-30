"""QQ 资源恢复离线回归检查；所有启动、结束进程和窗口点击均使用替身。"""

import sys
import threading
import unittest
from contextlib import ExitStack
from dataclasses import replace
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.qq_client_recovery import QQClientRecovery, QQProcess, is_resource_download_prompt, resolve_qq_client
from core.window_manager import WindowManager
from core.bot_engine import BotEngine
from models.config import AppConfig, TaskScheduleItemConfig


class ResourceRecoveryChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.root = QQProcess(101, 1, r"C:\QQ\QQ.exe", r'"C:\QQ\QQ.exe" /background', "100")
        self.child = QQProcess(102, 101, self.root.executable, "QQ.exe --type=renderer", "101")
        self.other = replace(self.root, pid=201, created_at="90")
        self.processes = {p.pid: p for p in (self.root, self.child, self.other)}
        self.notices: list[str] = []
        logger.disable("core.window_manager")
        logger.disable("core.bot_engine")
        QQClientRecovery._last_attempts.clear()
        WindowManager._all_claimed_hwnds.clear()

    def test_exact_resource_phrase_and_negative_downloads(self) -> None:
        self.assertTrue(is_resource_download_prompt("首次使用小程序工具资源，确定要下载吗？"))
        self.assertTrue(is_resource_download_prompt("首次使用\n小程序工具资源 确定要下载吗"))
        for text in ("确定要下载吗？", "首次使用小程序，选择账号", "QQ更新，确定下载资源吗？"):
            self.assertFalse(is_resource_download_prompt(text))

    def test_only_owner_client_is_resolved(self) -> None:
        self.assertEqual(resolve_qq_client(102, self.processes), self.root)
        self.assertIsNone(resolve_qq_client(999, self.processes))

    def test_orphan_helper_cycle_and_missing_identity_are_refused(self) -> None:
        self.assertIsNone(resolve_qq_client(102, {102: self.child}))
        self.assertIsNone(resolve_qq_client(101, {101: replace(self.root, parent_pid=101)}))
        self.assertIsNone(resolve_qq_client(101, {101: replace(self.root, created_at="")}))
        self.assertIsNone(resolve_qq_client(101, {101: replace(self.root, executable=r"C:\Other\app.exe")}))

    def recovery_patches(self, snapshots: list | None = None) -> tuple[ExitStack, Mock, Mock]:
        stack = ExitStack()
        stack.enter_context(patch("core.qq_client_recovery.get_window_pid", return_value=102))
        stack.enter_context(patch("core.qq_client_recovery.list_qq_processes", side_effect=snapshots or [self.processes, self.processes]))
        stack.enter_context(patch("core.qq_client_recovery.os.path.isfile", return_value=True))
        stack.enter_context(patch("core.qq_client_recovery.win32api.OpenProcess"))
        kill = stack.enter_context(patch("core.qq_client_recovery.subprocess.run"))
        launch = stack.enter_context(patch("core.qq_client_recovery.subprocess.Popen"))
        stack.enter_context(patch.object(QQClientRecovery, "_login_restarted_client", return_value=True))
        return stack, kill, launch

    def test_restart_targets_one_pid_and_preserves_command(self) -> None:
        stack, kill, launch = self.recovery_patches()
        with stack:
            self.assertEqual(QQClientRecovery.restart_for_prompt(700, lambda: False, self.notices.append), launch.return_value.pid)
        self.assertEqual(kill.call_args.args[0], ["taskkill.exe", "/PID", "101", "/T", "/F"])
        self.assertEqual(launch.call_args.args[0], self.root.command_line)
        self.assertEqual(launch.call_args.kwargs["executable"], self.root.executable)

    def test_changed_identity_is_never_terminated(self) -> None:
        changed = {**self.processes, 101: replace(self.root, created_at="200")}
        stack, kill, launch = self.recovery_patches([self.processes, changed])
        with stack:
            self.assertFalse(QQClientRecovery.restart_for_prompt(700, lambda: False, self.notices.append))
        kill.assert_not_called()
        launch.assert_not_called()

    def test_cooldown_spans_instances(self) -> None:
        stack, kill, launch = self.recovery_patches([self.processes] * 3)
        with stack:
            self.assertTrue(QQClientRecovery.restart_for_prompt(700, lambda: False, self.notices.append))
            self.assertFalse(QQClientRecovery.restart_for_prompt(700, lambda: False, self.notices.append))
        self.assertEqual(kill.call_count, 1)
        self.assertEqual(launch.call_count, 1)

    def test_stop_prevents_client_restart(self) -> None:
        stack, kill, launch = self.recovery_patches()
        with stack:
            self.assertFalse(QQClientRecovery.restart_for_prompt(700, lambda: True, self.notices.append))
        kill.assert_not_called()
        launch.assert_not_called()

    def test_login_click_waits_for_same_client_main_window(self) -> None:
        import numpy as np
        login = SimpleNamespace(_hWnd=700, title="QQ", width=400, height=575, left=100, top=200)
        main = SimpleNamespace(_hWnd=701, title="QQ", width=900, height=700)
        other = SimpleNamespace(_hWnd=702, title="QQ", width=900, height=700)
        item = SimpleNamespace(text="登录", box=[(200, 500)] * 4)
        with ExitStack() as stack:
            windows = stack.enter_context(patch("pygetwindow.getWindowsWithTitle", side_effect=[[login, other], [other], [main, other]]))
            stack.enter_context(patch("core.qq_client_recovery.get_window_pid", side_effect=lambda hwnd: 301 if hwnd in (700, 701) else 201))
            stack.enter_context(patch("core.screen_capture.ScreenCapture.capture", return_value=np.zeros((575, 400, 3), dtype=np.uint8)))
            ocr = stack.enter_context(patch("utils.ocr_utils.OCRTool.get_instance"))
            ocr.return_value.detect.return_value = [item]
            click = stack.enter_context(patch("pyautogui.click"))
            stack.enter_context(patch("core.qq_client_recovery.ctypes.windll.user32.ShowWindow"))
            stack.enter_context(patch("core.qq_client_recovery.ctypes.windll.user32.SetForegroundWindow"))
            stack.enter_context(patch("core.qq_client_recovery.time.sleep"))
            self.assertTrue(QQClientRecovery._login_restarted_client(301, lambda: False))
        click.assert_called_once_with(300, 700)
        self.assertEqual(windows.call_count, 3)

    def test_login_failure_prevents_relaunch(self) -> None:
        stack, _, _ = self.recovery_patches()
        with stack, patch.object(QQClientRecovery, "_login_restarted_client", return_value=False):
            self.assertIsNone(QQClientRecovery.restart_for_prompt(700, lambda: False, self.notices.append))

    def test_force_restart_launches_instead_of_claiming_existing_window(self) -> None:
        manager = WindowManager()
        game = SimpleNamespace(_hWnd=800, title="farm", left=0, top=0, width=600, height=1000)
        with patch.object(manager, "_launch_and_wait", return_value=[game]) as launch, patch.object(manager, "_find_window_locked") as find:
            self.assertEqual(manager.find_window("farm", True, "farm.lnk", select_account_keyword="123456", force_restart=True).hwnd, 800)
        launch.assert_called_once_with("farm", "farm.lnk", "123456")
        find.assert_not_called()

    def launch_patches(self, prompts: list, recovered: bool = True) -> tuple[ExitStack, WindowManager, Mock, Mock]:
        manager = WindowManager()
        manager.notify = self.notices.append
        manager.qq_recovery_allowed = lambda: True
        stack = ExitStack()
        stack.enter_context(patch("core.window_manager.gw.getAllWindows", return_value=[]))
        stack.enter_context(patch.object(manager, "_wait_for_launch", return_value=True))
        stack.enter_context(patch.object(manager, "_list_all_windows", return_value=[]))
        launch = stack.enter_context(patch.object(manager, "launch_game", return_value=True))
        stack.enter_context(patch.object(QQClientRecovery, "find_resource_prompt", side_effect=prompts))
        restart = stack.enter_context(patch.object(QQClientRecovery, "restart_for_prompt", return_value=recovered))
        return stack, manager, launch, restart

    def test_resource_recovery_relaunches_same_account(self) -> None:
        stack, manager, launch, restart = self.launch_patches([None, 700, None])
        restart.return_value = 301
        game = SimpleNamespace(_hWnd=800)
        with stack, patch.object(manager, "_handle_select_account", side_effect=[True, False, False]) as select, patch("core.window_manager.time.monotonic", side_effect=count(0, 1).__next__), patch("core.window_manager.get_window_pid", return_value=302), patch("core.window_manager.list_qq_processes", return_value={301: replace(self.root, pid=301), 302: replace(self.child, pid=302, parent_pid=301)}):
            manager._list_all_windows.side_effect = [[], [], [], [], [game]]
            self.assertEqual(manager._launch_and_wait("farm", "farm.lnk", "123456"), [game])
        self.assertEqual(launch.call_count, 2)
        restart.assert_called_once()
        self.assertGreaterEqual(select.call_count, 1)

    def test_repeat_prompt_does_not_restart_qq_twice(self) -> None:
        stack, manager, launch, restart = self.launch_patches([None, 700, 701])
        restart.return_value = 301
        with stack, patch.object(manager, "_handle_select_account", return_value=True), patch("core.window_manager.time.monotonic", side_effect=count(0, 1).__next__):
            self.assertEqual(manager._launch_and_wait("farm", "farm.lnk", "123456"), [])
        self.assertEqual(launch.call_count, 2)
        restart.assert_called_once()

    def test_prompt_before_account_confirmation_cannot_restart_qq(self) -> None:
        stack, manager, launch, restart = self.launch_patches([700])
        with stack:
            self.assertEqual(manager._launch_and_wait("farm", "farm.lnk", "123456"), [])
        launch.assert_called_once()
        restart.assert_not_called()

    def test_recovery_rejects_game_from_other_qq(self) -> None:
        stack, manager, _, restart = self.launch_patches([None, 700, None])
        restart.return_value = 301
        game = SimpleNamespace(_hWnd=800)
        with stack, patch.object(manager, "_handle_select_account", return_value=True), patch("core.window_manager.time.monotonic", side_effect=count(0, 1).__next__), patch("core.window_manager.get_window_pid", return_value=201), patch("core.window_manager.list_qq_processes", return_value={201: self.other}):
            calls = count()
            manager._list_all_windows.side_effect = lambda _: [] if next(calls) < 4 else [game]
            self.assertEqual(manager._launch_and_wait("farm", "farm.lnk", "123456"), [])
        restart.assert_called_once()

    def test_recovery_disabled_never_restarts_qq(self) -> None:
        stack, manager, launch, restart = self.launch_patches([700])
        manager.qq_recovery_allowed = lambda: False
        with stack:
            self.assertEqual(manager._launch_and_wait("farm", "farm.lnk", "123456"), [])
        restart.assert_not_called()
        self.assertEqual(launch.call_count, 1)

    def test_normal_launch_never_restarts_qq(self) -> None:
        stack, manager, launch, restart = self.launch_patches([None])
        game = SimpleNamespace(_hWnd=800)
        with stack, patch.object(manager, "_handle_select_account", return_value=True):
            manager._list_all_windows.side_effect = [[], [game]]
            self.assertEqual(manager._launch_and_wait("farm", "farm.lnk", "123456"), [game])
        restart.assert_not_called()
        launch.assert_called_once()

    def test_unconfirmed_account_cannot_claim_game_window(self) -> None:
        stack, manager, _, _ = self.launch_patches([None])
        game = SimpleNamespace(_hWnd=800)
        with stack, patch.object(manager, "_handle_select_account", return_value=False):
            manager._list_all_windows.side_effect = [[], [game]]
            with patch("core.window_manager.time.monotonic", side_effect=[0, 0, 0, 31, 31, 31]):
                self.assertEqual(manager._launch_and_wait("farm", "farm.lnk", "123456"), [])

    def test_existing_window_handle_is_checked_when_prompt_text_appears(self) -> None:
        import numpy as np
        manager = WindowManager()
        window = SimpleNamespace(_hWnd=700, width=380, height=160, left=0, top=0, title="QQ")
        with ExitStack() as stack:
            stack.enter_context(patch("pygetwindow.getAllWindows", return_value=[window]))
            stack.enter_context(patch("core.qq_client_recovery.get_process_image", return_value=r"C:\QQ\QQ.exe"))
            stack.enter_context(patch("core.qq_client_recovery.get_window_pid", return_value=102))
            stack.enter_context(patch("core.screen_capture.ScreenCapture.capture", return_value=np.zeros((160, 380, 3), dtype=np.uint8)))
            ocr = stack.enter_context(patch("utils.ocr_utils.OCRTool.get_instance"))
            ocr.return_value.detect.return_value = [SimpleNamespace(text="首次使用小程序工具资源，确定要下载吗？")]
            self.assertEqual(QQClientRecovery.find_resource_prompt({700}), 700)

    def test_stop_during_wait_does_not_restart_qq(self) -> None:
        stack, manager, launch, restart = self.launch_patches([])
        with stack:
            manager._wait_for_launch.return_value = False
            self.assertEqual(manager._launch_and_wait("farm", "farm.lnk", "123456"), [])
        restart.assert_not_called()

    def test_other_instance_cannot_launch_or_close_during_recovery(self) -> None:
        first, second = WindowManager(), WindowManager()
        entered, release = threading.Event(), threading.Event()

        def wait_inside_launch(*args: object) -> None:
            entered.set()
            release.wait(3)

        with patch.object(first, "_find_window_locked", side_effect=wait_inside_launch), patch.object(second, "_find_window_locked") as find, patch("core.window_manager.ctypes.windll.user32.PostMessageW") as close:
            worker = threading.Thread(target=first.find_window)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(WindowManager.launch_in_progress())
                self.assertIsNone(second.find_window(force_restart=True))
                find.assert_not_called()
                close.assert_not_called()
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(WindowManager.launch_in_progress())

    def test_exception_releases_launch_lock(self) -> None:
        manager = WindowManager()
        with patch.object(manager, "_find_window_locked", side_effect=RuntimeError("simulated")):
            self.assertIsNone(manager.find_window())
        self.assertFalse(WindowManager.launch_in_progress())

    def test_existing_switches_control_resource_recovery(self) -> None:
        config = AppConfig()
        engine = SimpleNamespace(config=config, _bot_stop_requested=False)
        config.tasks["restart"] = TaskScheduleItemConfig(enabled=True)
        self.assertTrue(BotEngine._qq_resource_recovery_allowed(engine))
        config.tasks["restart"].enabled = False
        self.assertFalse(BotEngine._qq_resource_recovery_allowed(engine))
        config.tasks["restart"].enabled = True
        for name, disabled in (("enabled", False), ("auto_restart_on_window_lost", False), ("max_restart_attempts", 0)):
            with self.subTest(setting=name):
                original = getattr(config.recovery, name)
                setattr(config.recovery, name, disabled)
                self.assertFalse(BotEngine._qq_resource_recovery_allowed(engine))
                setattr(config.recovery, name, original)
        engine._bot_stop_requested = True
        self.assertFalse(BotEngine._qq_resource_recovery_allowed(engine))

    def test_monitor_does_not_start_another_recovery(self) -> None:
        with patch.object(WindowManager, "launch_in_progress", return_value=True):
            self.assertTrue(BotEngine._is_window_alive(SimpleNamespace()))
            BotEngine._on_window_lost(SimpleNamespace())
            BotEngine._queue_recovery_after_failure(SimpleNamespace(), "main", "窗口未找到")

    def test_restart_always_restores_window_monitor(self) -> None:
        engine = SimpleNamespace(
            config=AppConfig(), _bot_stop_requested=False,
            scheduler=SimpleNamespace(_window_monitor_paused=False),
            log_message=Mock(), window_manager=Mock(),
        )
        engine.window_manager.find_window.side_effect = RuntimeError("simulated")
        with self.assertRaises(RuntimeError):
            BotEngine._run_task_restart(engine, None)
        self.assertFalse(engine.scheduler._window_monitor_paused)

    def test_account_ocr_miss_is_retried(self) -> None:
        import numpy as np
        manager = WindowManager()
        window = SimpleNamespace(_hWnd=700, width=400, height=400, left=0, top=0)
        item = SimpleNamespace(text="123456", box=[(40, 100)] * 4)
        with ExitStack() as stack:
            stack.enter_context(patch("core.window_manager.gw.getWindowsWithTitle", return_value=[window]))
            stack.enter_context(patch("core.screen_capture.ScreenCapture.capture", return_value=np.zeros((400, 400, 3), dtype=np.uint8)))
            ocr = stack.enter_context(patch("utils.ocr_utils.OCRTool.get_instance"))
            ocr.return_value.detect.side_effect = [[], [item]]
            stack.enter_context(patch("core.window_manager.ctypes.windll.user32.ShowWindow"))
            stack.enter_context(patch("core.window_manager.ctypes.windll.user32.SetForegroundWindow"))
            stack.enter_context(patch.object(manager, "_wait_for_launch", return_value=True))
            click = stack.enter_context(patch.object(manager, "_click_screen"))
            confirm = stack.enter_context(patch.object(manager, "_click_confirm", return_value=True))
            self.assertFalse(manager._handle_select_account(set(), "123456"))
            self.assertNotIn(700, manager._select_account_handled)
            manager._select_account_attempted_at.clear()
            self.assertTrue(manager._handle_select_account(set(), "123456"))
            self.assertIn(700, manager._select_account_handled)
            click.assert_called_once()
            confirm.assert_called_once()

    def test_client_reopens_if_stop_arrives_after_termination(self) -> None:
        stopped = False

        def terminate(*args: object, **kwargs: object) -> None:
            nonlocal stopped
            stopped = True

        stack, kill, launch = self.recovery_patches()
        with stack:
            kill.side_effect = terminate
            self.assertTrue(QQClientRecovery.restart_for_prompt(700, lambda: stopped, self.notices.append))
        launch.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

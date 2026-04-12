"""QQ农场自动化助手 - 程序入口"""
import sys
import os
import time
import threading

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication

from models.config import AppConfig
from gui.main_window import MainWindow
from utils.logger import setup_logger
from loguru import logger
from core.instance_manager import InstanceManager


_global_web_server = None  # 全局 Web 服务实例


def main():
    # 初始化日志
    setup_logger()

    # 路径解析：
    #   sys._MEIPASS — PyInstaller 解压的临时目录（打包的 templates 等）
    #   EXE 所在目录 — 用户文件（config.json、logs、screenshots）
    if getattr(sys, 'frozen', False):
        _internal = sys._MEIPASS  # 打包资源
        app_dir = os.path.dirname(sys.executable)  # 用户文件
    else:
        _internal = os.path.dirname(os.path.abspath(__file__))
        app_dir = _internal

    # 切换工作目录到打包资源目录，确保 templates/ 相对路径正确
    os.chdir(_internal)

    # 初始化实例管理器
    instance_manager = InstanceManager()
    instance_manager.load()
    active_session = instance_manager.get_active()
    
    # 如果没有活动实例，使用默认配置路径（向后兼容）
    if active_session:
        config = active_session.config
        logger.info(f"加载实例配置: {active_session.instance_id} ({active_session.name})")
    else:
        config_path = os.path.join(app_dir, "config.json")
        config = AppConfig.load(config_path)
        logger.info("使用默认配置")

    # 启动GUI — 禁用系统暗色主题检测，强制使用 Fusion 浅色
    QApplication.setDesktopSettingsAware(False)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 强制设置 Fusion 调色板为浅色，覆盖 Windows 暗色主题
    from PyQt6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f5f5f7"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1d1d1f"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#f5f5f7"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#1d1d1f"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1d1d1f"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1d1d1f"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#1d1d1f"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#007AFF"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#007AFF"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#aeaeb2"))
    app.setPalette(palette)

    # 给所有 QDialog/QMessageBox/QInputDialog 设置浅色背景（覆盖系统暗色主题）
    from PyQt6.QtWidgets import QDialog
    from gui.styles import Colors
    _dialog_css = f"""
        QDialog, QMessageBox, QInputDialog {{
            background-color: {Colors.CARD_BG}; color: {Colors.TEXT};
        }}
        QInputDialog * {{
            background-color: {Colors.CARD_BG}; color: {Colors.TEXT};
        }}
        QInputDialog QFrame {{
            background-color: {Colors.CARD_BG}; border: none;
        }}
        QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel {{
            color: {Colors.TEXT}; background: transparent;
        }}
        QDialog QLineEdit, QInputDialog QLineEdit {{
            background-color: {Colors.WINDOW_BG}; color: {Colors.TEXT};
            border: 1px solid rgba(0,0,0,25); border-radius: 6px;
            padding: 6px 10px;
        }}
        QDialog QPushButton, QMessageBox QPushButton, QInputDialog QPushButton {{
            background-color: {Colors.CARD_BG}; color: {Colors.TEXT};
            border: 1px solid rgba(0,0,0,25); border-radius: 6px;
            padding: 6px 20px; min-width: 80px;
        }}
        QDialog QPushButton:hover, QMessageBox QPushButton:hover {{
            background-color: rgba(0,0,0,6);
        }}
        QDialog QDialogButtonBox, QMessageBox QDialogButtonBox,
        QInputDialog QDialogButtonBox {{
            background-color: {Colors.CARD_BG};
        }}
        QDialog QScrollArea, QMessageBox QScrollArea,
        QInputDialog QScrollArea {{
            background: transparent;
        }}
    """
    app.setStyleSheet(app.styleSheet() + _dialog_css)

    window = MainWindow(config, instance_manager=instance_manager)
    window.show()

    # 注册全局热键 (F9 暂停/恢复, F10 停止)
    window.register_hotkeys()

    # Web 服务默认不启动，需用户手动启动
    if config.web.enabled:
        logger.info("Web 服务配置已启用，但需手动启动（GUI 设置面板或 /web start 命令）")

    ret = app.exec()

    # 停止 Web 服务
    _stop_web_server()

    # 清理热键
    window.unregister_hotkeys()

    sys.exit(ret)


def _start_web_server(config: AppConfig, window: MainWindow):
    """根据配置启动 Web 服务"""
    global _global_web_server

    # 如果已经在运行，先停止再重新启动
    if _global_web_server and _global_web_server._running:
        _global_web_server.stop()
        import time
        time.sleep(0.5)

    # 注意：这里不再检查 config.web.enabled，因为 GUI 按钮可能手动启动服务
    # 如果配置中未启用，仍然允许通过 GUI 按钮启动

    try:
        from web.server import WebServer

        web = WebServer(host=config.web.host, port=config.web.port, engine=window.engine)

        # 注入回调
        def get_bot_state():
            engine = window.engine
            if not engine:
                return "stopped"
            from core.task_scheduler import BotState
            state_map = {
                BotState.RUNNING: "running",
                BotState.PAUSED: "paused",
                BotState.IDLE: "stopped",
                BotState.ERROR: "stopped",
            }
            return state_map.get(engine.scheduler.state, "stopped")

        def get_stats():
            return window.engine.scheduler.get_stats() if window.engine else {}

        def get_screenshot():
            """获取实时截图（优先内存，其次磁盘）"""
            engine = window.engine
            if not engine:
                return None
            try:
                # 尝试实时截图
                wnd = engine.window_manager._cached_window
                if wnd:
                    rect = (wnd.left, wnd.top, wnd.width, wnd.height)
                    run_mode = engine.config.safety.run_mode
                    hwnd = wnd.hwnd if run_mode == "background" else None
                    img = engine.screen_capture.capture(rect, hwnd=hwnd)
                    if img:
                        import cv2
                        import numpy as np
                        arr = np.array(img)
                        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            except Exception:
                pass
            # 兜底：读取磁盘最新截图
            try:
                import glob
                shots = glob.glob("screenshots/farm_*.png")
                if not shots:
                    shots = glob.glob("screenshots/*.png")
                if shots:
                    shots.sort(key=os.path.getmtime, reverse=True)
                    import cv2
                    import numpy as np
                    img = cv2.imdecode(np.fromfile(shots[0], dtype=np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        return img
            except Exception:
                pass
            return None

        web.get_bot_state = get_bot_state
        web.get_stats = get_stats
        web.get_screenshot = get_screenshot
        web.start_bot = window.engine.start
        web.stop_bot = window.engine.stop
        web.pause_bot = window.engine.pause
        web.resume_bot = window.engine.resume

        # 将 web 实例保存到 window 对象和全局变量
        window.web_server = web
        _global_web_server = web
        
        logger.info(f"window.web_server 已设置为 web 实例: {window.web_server is not None}")
        logger.info(f"_global_web_server 已设置为 web 实例: {_global_web_server is not None}")
        logger.info(f"id(window.web_server): {id(window.web_server)}")
        logger.info(f"id(_global_web_server): {id(_global_web_server)}")
        
        web.start()

        logger.info(f"Web 服务已启动: http://{config.web.host}:{config.web.port}")
    except Exception as e:
        logger.warning(f"Web 服务启动失败: {e}")


def _stop_web_server():
    """停止 Web 服务"""
    global _global_web_server
    from loguru import logger
    
    logger.info(f"_stop_web_server 被调用")
    logger.info(f"_global_web_server 存在: {_global_web_server is not None}")
    logger.info(f"id(_global_web_server): {id(_global_web_server) if _global_web_server else 'None'}")
    
    if _global_web_server:
        logger.info(f"调用 _global_web_server.stop()")
        logger.info(f"_global_web_server._running: {_global_web_server._running}")
        logger.info(f"_global_web_server._server: {_global_web_server._server}")
        logger.info(f"_global_web_server._thread: {_global_web_server._thread}")
        
        _global_web_server.stop()
        
        logger.info("_global_web_server.stop() 返回")
        logger.info(f"停止后 _global_web_server._running: {_global_web_server._running}")
        
        _global_web_server = None
        logger.info("_global_web_server 已设置为 None")
    else:
        logger.warning("_global_web_server 为 None，无法停止 Web 服务")


if __name__ == "__main__":
    main()

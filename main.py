"""QQ农场自动化助手 - 程序入口"""
import sys
import os

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication

from models.config import AppConfig
from gui.main_window import MainWindow
from utils.logger import setup_logger


def main():
    # 初始化日志
    setup_logger()

    # 加载配置（PyInstaller 打包后在 EXE 所在目录查找）
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(app_dir, "config.json")
    config = AppConfig.load(config_path)

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

    window = MainWindow(config)
    window.show()

    # 注册全局热键 (F9 暂停/恢复, F10 停止)
    window.register_hotkeys()

    ret = app.exec()

    # 清理热键
    window.unregister_hotkeys()

    sys.exit(ret)


if __name__ == "__main__":
    main()

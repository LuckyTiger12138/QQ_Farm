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

    # 加载配置
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    config = AppConfig.load(config_path)

    # 启动GUI
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

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

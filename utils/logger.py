"""日志系统 - 同时输出到文件和GUI"""
import sys
from loguru import logger
from PyQt6.QtCore import QObject, pyqtSignal


class LogSignal(QObject):
    """用于将日志消息发送到GUI的信号"""
    new_log = pyqtSignal(str)


_log_signal = LogSignal()
_debug_sink_ids: dict[str, int] = {}


def get_log_signal() -> LogSignal:
    return _log_signal


def _gui_sink(message):
    """将日志发送到GUI"""
    text = message.strip()
    if text:
        _log_signal.new_log.emit(text)


def get_app_dir() -> str:
    """获取应用程序所在目录（兼容 PyInstaller 打包）"""
    import os
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def _normalize_instance_id(instance_id: str) -> str:
    iid = str(instance_id or "default").strip()
    return iid or "default"


def configure_instance_debug_log(
    instance_id: str,
    enabled: bool,
    log_dir: str | None = None,
) -> str:
    """为实例启用/关闭额外调试日志文件。"""
    import os

    iid = _normalize_instance_id(instance_id)
    old_sink_id = _debug_sink_ids.pop(iid, None)
    if old_sink_id is not None:
        try:
            logger.remove(old_sink_id)
        except Exception:
            pass

    if not enabled:
        return ""

    if log_dir is None:
        log_dir = os.path.join(get_app_dir(), "logs", iid)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "debug_{time:YYYY-MM-DD}.log")
    sink_id = logger.add(
        log_path,
        rotation="00:00",
        retention="14 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {thread.name} | {message}",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    _debug_sink_ids[iid] = sink_id
    return log_path


def setup_logger(log_dir: str | None = None):
    """初始化日志系统"""
    import os
    if log_dir is None:
        log_dir = os.path.join(get_app_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)

    logger.remove()
    # 控制台输出（PyInstaller console=False 时 sys.stderr 为 None，跳过）
    if sys.stderr is not None:
        logger.add(sys.stderr, level="DEBUG",
                   format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
    # 文件输出
    logger.add(os.path.join(log_dir, "bot_{time:YYYY-MM-DD}.log"),
               rotation="00:00", retention="7 days", level="DEBUG",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}",
               encoding="utf-8")
    # GUI输出
    logger.add(_gui_sink, level="INFO",
               format="{time:HH:mm:ss} | {level:<7} | {message}")

    return logger

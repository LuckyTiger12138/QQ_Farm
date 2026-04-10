"""静默时段检查器 - 在指定时间段内不执行任何操作"""
from datetime import datetime
from models.config import SilentHoursConfig


def _now_seconds() -> int:
    """获取当前时间的总秒数 (0 ~ 86399)"""
    now = datetime.now()
    return now.hour * 3600 + now.minute * 60 + now.second


def is_silent_time(config: SilentHoursConfig) -> bool:
    """检查当前时间是否在静默时段内
    
    支持跨午夜情况，例如 22:00 - 06:00
    精确到秒：如果设置结束时间为 22:44，则 22:44:00 及之后立即解除静默
    """
    if not config.enabled:
        return False
    
    current = _now_seconds()
    start = config.start_hour * 3600 + config.start_minute * 60
    end = config.end_hour * 3600 + config.end_minute * 60
    
    # 处理跨午夜情况（如 22:00 - 06:00）
    if start > end:
        return current >= start or current <= end
    else:
        # 不跨午夜：当前时间在 [start, end] 范围内
        # 注意：这里使用 <= end，意味着 end 时间点本身也算在静默期内
        # 但下一秒就会解除。例如 end=22:44:00，则 22:44:00 是静默的，22:44:01 不是。
        return start <= current <= end


def get_silent_remaining_seconds(config: SilentHoursConfig) -> int:
    """获取距离静默时段结束的剩余秒数
    
    返回:
        - 如果当前不在静默时段，返回 0
        - 如果当前在静默时段，返回距离结束的秒数
    """
    if not is_silent_time(config):
        return 0
    
    current = _now_seconds()
    start = config.start_hour * 3600 + config.start_minute * 60
    end = config.end_hour * 3600 + config.end_minute * 60
    
    if start > end:
        # 跨午夜情况
        if current >= start:
            # 当前在 [start, 23:59:59] 区间，需要跨越午夜
            remaining = (86400 - current) + end
        else:
            # 当前在 [00:00:00, end] 区间
            remaining = end - current
    else:
        remaining = end - current
    
    return max(0, remaining)

"""大小号通讯消息总线 — 跨实例偷菜协调

轻量级内存消息总线，所有 BotEngine 共享同一实例。
数据流：
  Instance A (大号) → land scan → 检测成熟倒计时 < 5min
  → CrossInstanceBus.post_alert()
  → Instance B (小号) → TaskExecutor.poll_alerts()
  → 动态创建 TargetedStealTask → 定点偷菜
"""
import queue
import threading
import time
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class StealAlert:
    """偷菜通知消息"""
    source_instance_id: str            # 发送方实例ID
    source_name: str                   # 发送方名称
    friend_name: str                   # 好友昵称（接收方用来OCR定位）
    target_instance_id: str = ""       # 接收方实例ID
    plot_ids: list[str] = field(default_factory=list)   # 即将成熟的地块ID列表
    earliest_maturity_seconds: int = 0 # 最近成熟时间（秒）
    timestamp: float = 0.0             # 发送时间戳

    def __post_init__(self):
        if self.timestamp <= 0:
            self.timestamp = time.time()


class CrossInstanceBus:
    """全局单例：跨实例消息总线

    线程安全，通过 queue.Queue + Lock 实现。
    """

    def __init__(self):
        self._alerts: queue.Queue[StealAlert] = queue.Queue()
        self._lock = threading.Lock()
        # 记录已发送的 alert key，防止同一配对短时间内重复发送
        self._sent_keys: dict[str, float] = {}  # key -> timestamp
        self._dedup_window: float = 240.0  # 4 分钟内同一配对不重复

    def post_alert(self, alert: StealAlert) -> bool:
        """发送偷菜通知

        Args:
            alert: 偷菜通知

        Returns:
            是否成功入队（False 表示去重过滤）
        """
        dedup_key = f"{alert.source_instance_id}->{alert.friend_name}"
        with self._lock:
            last_sent = self._sent_keys.get(dedup_key, 0.0)
            if time.time() - last_sent < self._dedup_window:
                logger.debug(
                    f"跨实例通知去重: {dedup_key} "
                    f"(上次发送 {time.time() - last_sent:.0f}s 前)"
                )
                return False
            self._sent_keys[dedup_key] = time.time()

        self._alerts.put(alert)
        logger.info(
            f"[大小号通讯📬] 通知入队: [{alert.source_name}] → [{alert.friend_name}] "
            f"| 地块: {','.join(alert.plot_ids)} "
            f"| 最近成熟: {alert.earliest_maturity_seconds}s"
        )
        return True

    def poll_alerts(self, instance_id: str) -> list[StealAlert]:
        """拉取发给指定实例的通知

        Args:
            instance_id: 接收方实例ID

        Returns:
            属于该实例的通知列表
        """
        results: list[StealAlert] = []
        remaining: list[StealAlert] = []

        while not self._alerts.empty():
            try:
                alert = self._alerts.get_nowait()
                if not alert.target_instance_id or alert.target_instance_id == instance_id:
                    results.append(alert)
                else:
                    remaining.append(alert)
            except queue.Empty:
                break

        for alert in remaining:
            self._alerts.put(alert)

        return results

    def clear_expired(self, max_age_seconds: int = 600) -> None:
        """清理过期的去重记录"""
        now = time.time()
        with self._lock:
            expired = [
                k for k, v in self._sent_keys.items()
                if now - v > max_age_seconds
            ]
            for k in expired:
                del self._sent_keys[k]

    def get_stats(self) -> dict:
        """获取总线统计信息"""
        with self._lock:
            return {
                "pending_alerts": self._alerts.qsize(),
                "tracked_pairs": len(self._sent_keys),
            }

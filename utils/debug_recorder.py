"""调试诊断事件记录器。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


_state_lock = threading.RLock()
_write_lock = threading.RLock()
_debug_dirs: dict[str, Path] = {}


def _normalize_instance_id(instance_id: str) -> str:
    iid = str(instance_id or "default").strip()
    return iid or "default"


def configure_debug_recorder(instance_id: str, enabled: bool, log_dir: str | Path) -> Path | None:
    """配置指定实例的结构化调试记录输出。"""
    iid = _normalize_instance_id(instance_id)
    with _state_lock:
        if not enabled:
            _debug_dirs.pop(iid, None)
            return None

        base = Path(log_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        _debug_dirs[iid] = base
        return base


def is_debug_recording_enabled(instance_id: str) -> bool:
    """返回指定实例是否开启结构化调试记录。"""
    iid = _normalize_instance_id(instance_id)
    with _state_lock:
        return iid in _debug_dirs


def record_debug_event(instance_id: str, event: str, **fields: Any) -> None:
    """写入一条 JSONL 调试事件。

    该文件面向排查调度问题：能看到任务是否待执行、为什么被跳过、是否被恢复链路触发。
    """
    iid = _normalize_instance_id(instance_id)
    with _state_lock:
        base = _debug_dirs.get(iid)
    if base is None:
        return

    now = datetime.now()
    payload = {
        "time": now.isoformat(timespec="seconds"),
        "instance_id": iid,
        "event": str(event or "event"),
    }
    payload.update(_json_safe(fields))

    path = base / f"task_trace_{now:%Y-%m-%d}.jsonl"
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with _write_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _json_safe(value: Any) -> Any:
    """将常见不可序列化对象转换为 JSON 友好结构。"""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)

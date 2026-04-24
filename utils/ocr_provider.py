"""OCR provider: lazy create and cache OCRTool by scope/key."""
from __future__ import annotations

from threading import Lock

try:
    from utils.ocr_utils import OCRTool
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

_ocr_cache: dict[tuple[str, str], any] = {}
_ocr_cache_lock = Lock()


def _normalize_scope(scope: str | None) -> str:
    return str(scope or '').strip().lower() or 'engine'


def _normalize_key(key: str | None) -> str:
    return str(key or '').strip() or 'default'


def get_ocr_tool(scope: str = 'engine', key: str | None = None):
    """按 (scope, key) 获取 OCRTool（懒加载并缓存）。"""
    if not HAS_OCR:
        return None
    cache_key = (_normalize_scope(scope), _normalize_key(key))
    with _ocr_cache_lock:
        tool = _ocr_cache.get(cache_key)
        if tool is not None:
            return tool
        tool = OCRTool()
        _ocr_cache[cache_key] = tool
        return tool


def clear_all_ocr_tools() -> None:
    with _ocr_cache_lock:
        _ocr_cache.clear()

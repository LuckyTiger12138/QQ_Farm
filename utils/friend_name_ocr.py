"""好友昵称 OCR 识别。"""
from __future__ import annotations

import numpy as np

try:
    from utils.ocr_provider import get_ocr_tool
    from utils.ocr_utils import OCRItem, OCRTool
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


class FriendNameOCR:
    """封装好友昵称识别。"""

    def __init__(self, ocr_tool=None, *, scope: str = 'engine', key: str | None = None):
        if HAS_OCR:
            self.ocr = ocr_tool or get_ocr_tool(scope=scope, key=key)
        else:
            self.ocr = None

    def detect_name(self, img_bgr, *, region=None) -> tuple[str, float]:
        """识别好友昵称并返回 (name, score)。"""
        if img_bgr is None or self.ocr is None:
            return '', 0.0
        text, score = self.ocr.detect_text(
            img_bgr, region=region, scale=1.4, alpha=1.15, beta=0.0, joiner='')
        return str(text or '').strip(), float(score or 0.0)

    def detect_items(self, img_bgr, *, region=None) -> list:
        """识别并返回结构化 OCR item 列表。"""
        if img_bgr is None or self.ocr is None:
            return []
        return self.ocr.detect(img_bgr, region=region, scale=1.4, alpha=1.15, beta=0.0)

"""OCR utility based on rapidocr_onnxruntime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError as exc:
    raise RuntimeError('Missing dependency `rapidocr_onnxruntime`. Please install requirements first.') from exc


@dataclass
class OCRItem:
    """Single OCR result item."""
    box: list[tuple[float, float]]  # 4 corner points
    text: str
    score: float


class OCRTool:
    """Reusable OCR helper.

    Uses rapidocr_onnxruntime internally.
    """

    _instance: OCRTool | None = None

    def __init__(self):
        self._ocr = RapidOCR()

    @classmethod
    def get_instance(cls) -> OCRTool:
        """Get or create a shared OCRTool instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _rescale_box(box: list[list[float]], scale: float) -> list[tuple[float, float]]:
        return [(p[0] / scale, p[1] / scale) for p in box]

    def detect(
        self,
        bgr: np.ndarray,
        *,
        region: tuple[int, int, int, int] | None = None,
        scale: float = 1.0,
        alpha: float = 1.0,
        beta: float = 0.0,
    ) -> list[OCRItem]:
        """Run OCR and return structured items.

        Args:
            bgr: OpenCV BGR image.
            region: optional ROI (x1, y1, x2, y2) in original coordinates.
            scale: resize factor before OCR.
            alpha/beta: brightness/contrast adjustment.

        Returns:
            List of OCRItem with boxes mapped back to original image coordinates.
        """
        offset_x, offset_y = 0, 0
        proc = bgr
        if region is not None:
            h, w = proc.shape[:2]
            x1, y1, x2, y2 = region
            x1 = max(0, min(x1, w - 1))
            y1 = max(0, min(y1, h - 1))
            x2 = max(x1 + 1, min(x2, w))
            y2 = max(y1 + 1, min(y2, h))
            proc = proc[y1:y2, x1:x2]
            offset_x, offset_y = x1, y1

        if scale != 1.0 or alpha != 1.0 or beta != 0.0:
            sh, sw = proc.shape[:2]
            proc = cv2.resize(proc, (int(sw * scale), int(sh * scale)),
                              interpolation=cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA)
            if alpha != 1.0 or beta != 0.0:
                proc = cv2.convertScaleAbs(proc, alpha=alpha, beta=beta)

        raw, _ = self._ocr(proc)
        if not raw:
            return []

        items: list[OCRItem] = []
        inv = 1.0 / scale if scale != 0 else 1.0
        for line in raw:
            box_raw = line[0]  # list of 4 points
            text = line[1]
            score = line[2]
            mapped_box = self._rescale_box(box_raw, scale)
            # offset back to original image coordinates
            mapped_box = [(p[0] + offset_x, p[1] + offset_y) for p in mapped_box]
            items.append(OCRItem(box=mapped_box, text=str(text), score=float(score)))
        return items

    def detect_text(
        self,
        bgr: np.ndarray,
        *,
        scale: float = 1.0,
        alpha: float = 1.0,
        beta: float = 0.0,
    ) -> tuple[str, float]:
        """Run OCR and return merged text and average confidence."""
        items = self.detect(bgr, scale=scale, alpha=alpha, beta=beta)
        if not items:
            return '', 0.0
        texts = [it.text for it in items]
        avg_score = sum(it.score for it in items) / len(items)
        return ' '.join(texts), avg_score

    @staticmethod
    def to_dict(items: list[OCRItem]) -> list[dict[str, Any]]:
        """Convert OCR items to plain dict list for logging/serialization."""
        return [{'box': it.box, 'text': it.text, 'score': it.score} for it in items]

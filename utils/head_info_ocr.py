"""主界面头部信息 OCR 识别 — 从 copilot 移植并适配本项目 OCR 接口。"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from utils.ocr_utils import OCRItem, OCRTool


# 主界面等级 OCR 顶部带高度（像素）。
# copilot 用 140px（540x960），当前窗口 581x1054 需要更大范围
HEAD_INFO_OCR_TOP_BAND_HEIGHT = 200


class HeadInfoOCR:
    """封装主界面头部信息识别逻辑（等级/金币/点券/经验）。"""

    def __init__(self, ocr_tool: OCRTool | None = None):
        self.ocr = ocr_tool

    def _ensure_ocr(self) -> OCRTool | None:
        if self.ocr is not None:
            return self.ocr
        try:
            from utils.ocr_provider import get_ocr_tool
            self.ocr = get_ocr_tool()
        except Exception:
            return None
        return self.ocr

    @staticmethod
    def _normalize_text(text: str) -> str:
        raw = str(text or '')
        raw = raw.replace('：', ':').replace('（', '(').replace('）', ')')
        return ''.join(raw.split())

    @staticmethod
    def _extract_level(text: str, *, min_level: int, max_level: int) -> tuple[int | None, int]:
        normalized = HeadInfoOCR._normalize_text(text)
        if not normalized:
            return None, 0
        patterns = [
            (re.compile(r'(?i)(?:lv|级别|等级)[:：]?(\d{1,3})'), 3),
            (re.compile(r'(\d{1,3})级'), 2),
            (re.compile(r'^(\d{1,3})$'), 1),
        ]
        for pattern, priority in patterns:
            matched = pattern.search(normalized)
            if matched is None:
                continue
            try:
                level = int(matched.group(1))
            except Exception:
                continue
            if min_level <= level <= max_level:
                return level, priority
        return None, 0

    @staticmethod
    def _sort_items(items: list[OCRItem]) -> list[OCRItem]:
        return sorted(items, key=lambda item: (
            min(point[1] for point in item.box),
            min(point[0] for point in item.box),
        ))

    @staticmethod
    def _item_center(item: OCRItem) -> tuple[float, float]:
        xs = [point[0] for point in item.box]
        ys = [point[1] for point in item.box]
        return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))

    @staticmethod
    def _item_bbox(item: OCRItem) -> tuple[float, float, float, float]:
        xs = [point[0] for point in item.box]
        ys = [point[1] for point in item.box]
        return float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))

    @staticmethod
    def _is_nickname_candidate(text: str) -> bool:
        normalized = HeadInfoOCR._normalize_text(text)
        if not normalized:
            return False
        if re.search(r'(?i)\b(?:id|version)[:：]?', normalized):
            return False
        if re.search(r'\d+(?:\.\d+)?(?:万|亿)', normalized):
            return False
        if not re.search(r'[\u4e00-\u9fff]', normalized):
            return False
        return 1 <= len(normalized) <= 16

    @staticmethod
    def _extract_other_info(raw_texts: list[str]) -> dict[str, list[str]]:
        """从 OCR 文本中提取附加调试信息。"""
        money_candidates: list[str] = []
        id_candidates: list[str] = []
        version_candidates: list[str] = []

        for text in raw_texts:
            normalized = HeadInfoOCR._normalize_text(text)
            if not normalized:
                continue
            if re.search(r'\d+(?:\.\d+)?(?:万|亿)', normalized):
                money_candidates.append(text)
            if re.search(r'(?i)\bID[:：]?\d+', normalized):
                id_candidates.append(text)
            if re.search(r'(?i)\bVersion[:：]?[0-9.]+', normalized):
                version_candidates.append(text)

        return {
            'money_candidates': money_candidates,
            'id_candidates': id_candidates,
            'version_candidates': version_candidates,
        }

    def _extract_structured_head_info(
        self,
        items: list[OCRItem],
        *,
        level: int | None,
        nickname_text: str,
        level_raw_text: str = '',
    ) -> dict[str, Any]:
        exp_pattern = re.compile(r'\d+(?:\.\d+)?(?:万|亿)?/\d+(?:\.\d+)?(?:万|亿)?')
        concat_exp_pattern = re.compile(r'(\d+(?:\.\d+)?(?:万|亿))(\d+(?:\.\d+)?(?:万|亿))')
        money_pattern = re.compile(r'\d+(?:\.\d+)?(?:万|亿)')
        pure_number_pattern = re.compile(r'\d{3,8}')

        gold = ''
        exp = ''
        coupon = ''
        exp_token_index: int | None = None
        coupon_candidates: list[tuple[float, float, str]] = []
        money_candidates: list[tuple[int, float, float, float, str, str]] = []
        nickname_pos: tuple[float, float] | None = None
        level_pos: tuple[float, float] | None = None
        normalized_level_raw = self._normalize_text(level_raw_text)

        # 调试：记录所有 token
        from loguru import logger
        token_debug = [(str(item.text), round(float(item.score), 3)) for item in items]
        logger.debug(f"经验提取: tokens={token_debug}")

        for idx, item in enumerate(items):
            text = str(item.text or '').strip()
            normalized = self._normalize_text(text)
            if not normalized:
                continue

            cx, cy = self._item_center(item)
            score = float(item.score)
            if not nickname_pos and text == str(nickname_text or ''):
                nickname_pos = (cx, cy)
            if level is not None and re.fullmatch(r'\d{1,3}', normalized):
                try:
                    if int(normalized) == int(level):
                        # 优先使用与最终等级 token 一致的文本。
                        if normalized_level_raw and normalized == normalized_level_raw:
                            level_pos = (cx, cy)
                        elif level_pos is None:
                            level_pos = (cx, cy)
                except Exception:
                    pass

            if exp_pattern.search(normalized):
                if exp_token_index is None:
                    exp = text
                    exp_token_index = idx
                    logger.debug(f"经验提取: 直接匹配 exp='{text}' idx={idx}")
                continue

            if money_pattern.search(normalized):
                money_candidates.append((idx, cx, cy, score, text, normalized))

            if pure_number_pattern.fullmatch(normalized):
                try:
                    num = int(normalized)
                except Exception:
                    continue
                if level is not None and num == int(level):
                    continue
                coupon_candidates.append((cx, cy, text))

        if not exp and money_candidates:
            logger.debug(f"经验提取: 未直接匹配，尝试兜底 | money_candidates={[(e[4], e[5]) for e in money_candidates]}")
            fallback_exp: list[tuple[int, float, float, float, str]] = []
            for idx, cx, cy, score, text, normalized in money_candidates:
                matched = concat_exp_pattern.fullmatch(normalized)
                if matched is None:
                    continue
                first, second = matched.group(1), matched.group(2)
                position_score = 0.0
                if level_pos is not None:
                    lx, ly = level_pos
                    if cx <= lx + 80:
                        position_score += 1.0
                    if abs(cy - ly) <= 20:
                        position_score += 1.0
                    position_score += 1.0 / (1.0 + abs(lx - cx))
                if nickname_pos is not None:
                    _nx, ny = nickname_pos
                    if cy >= ny - 5:
                        position_score += 0.3
                fallback_exp.append((idx, position_score, score, cy, f'{first}/{second}'))
            if fallback_exp:
                fallback_exp.sort(key=lambda item: (item[1], item[2], -item[3]), reverse=True)
                best = fallback_exp[0]
                exp_token_index = int(best[0])
                exp = str(best[4])

        if not gold and money_candidates:
            gold_pool = [e for e in money_candidates if e[0] != exp_token_index and '/' not in e[5]]
            if gold_pool:
                if nickname_pos is not None:
                    _nx, ny = nickname_pos
                    gold_pool.sort(key=lambda item: (abs(item[2] - ny), item[1]))
                else:
                    gold_pool.sort(key=lambda item: (item[2], item[1]))
                gold = gold_pool[0][4]

        if coupon_candidates:
            coupon_candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
            coupon = coupon_candidates[0][2]

        logger.debug(f"经验提取: 结果 | exp='{exp}' gold='{gold}' coupon='{coupon}' level_pos={level_pos}")

        return {
            'gold': gold,
            'nickname': str(nickname_text or ''),
            'exp': exp,
            'level': int(level) if level is not None else None,
            'coupon': coupon,
        }

    @staticmethod
    def get_level_ocr_region(frame_shape: tuple[int, ...]) -> tuple[int, int, int, int] | None:
        """计算统一等级 OCR 区域（顶部条带），裁剪到截图范围内。"""
        try:
            frame_h = int(frame_shape[0]) if len(frame_shape) >= 1 else 0
            frame_w = int(frame_shape[1]) if len(frame_shape) >= 2 else 0
        except Exception:
            return None

        if frame_w <= 1 or frame_h <= 1:
            return None

        x1, y1 = 0, 0
        x2 = frame_w
        y2 = min(frame_h, HEAD_INFO_OCR_TOP_BAND_HEIGHT)

        x2 = max(x1 + 1, min(x2, frame_w))
        y2 = max(y1 + 1, min(y2, frame_h))
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    def detect_head_info(
        self,
        img_bgr: np.ndarray,
        *,
        region: tuple[int, int, int, int] | None = None,
        min_level: int = 1,
        max_level: int = 999,
    ) -> tuple[int | None, float, str, dict[str, Any]]:
        """识别等级并返回 (level, score, raw_text, extra_info)。"""
        ocr = self._ensure_ocr()
        if img_bgr is None or ocr is None:
            return None, 0.0, '', {}

        lower = max(1, int(min_level))
        upper = max(lower, int(max_level))
        items = ocr.detect(img_bgr, region=region, scale=1.5, alpha=1.15, beta=0.0)
        if not items:
            return (
                None,
                0.0,
                '',
                {
                    'tokens': [],
                    'gold': '',
                    'nickname': '',
                    'exp': '',
                    'level': None,
                    'coupon': '',
                    **self._extract_other_info([]),
                },
            )

        ordered = self._sort_items(items)
        raw_texts = [str(item.text) for item in ordered]
        merged_text = ''.join(self._normalize_text(t) for t in raw_texts)

        # 昵称检测
        nickname_item: OCRItem | None = None
        nickname_score = -1.0
        nickname_y = float('inf')
        for item in ordered:
            if not self._is_nickname_candidate(item.text):
                continue
            _nx1, ny1, _nx2, _ny2 = self._item_bbox(item)
            score = float(item.score)
            if ny1 < nickname_y - 1e-6 or (abs(ny1 - nickname_y) <= 1e-6 and score > nickname_score):
                nickname_item = item
                nickname_y = ny1
                nickname_score = score

        if nickname_item is not None:
            nx1, _ny1, _nx2, ny2 = self._item_bbox(nickname_item)
            nickname_ref_x = float(nx1 - 6.0)
            nickname_ref_y = float(ny2 + 8.0)
        else:
            nickname_ref_x, nickname_ref_y = 145.0, 113.0
        legacy_ref_x, legacy_ref_y = 145.0, 113.0

        # 等级候选
        candidates: list[tuple[int, float, float, int, float, str]] = []
        for item in ordered:
            cx, cy = self._item_center(item)
            dist_nickname = ((cx - nickname_ref_x) ** 2 + (cy - nickname_ref_y) ** 2) ** 0.5
            proximity_nickname = 1.0 / (1.0 + dist_nickname)
            dist_legacy = ((cx - legacy_ref_x) ** 2 + (cy - legacy_ref_y) ** 2) ** 0.5
            proximity_legacy = 1.0 / (1.0 + dist_legacy)
            if nickname_item is not None:
                proximity = proximity_nickname * 0.7 + proximity_legacy * 0.3
            else:
                proximity = proximity_legacy
            item_score = float(item.score)
            normalized = self._normalize_text(item.text)

            if re.fullmatch(r'\d{1,3}', normalized):
                try:
                    num = int(normalized)
                except Exception:
                    continue
                if lower <= num <= upper:
                    candidates.append((30, proximity, item_score, num, item_score, str(item.text)))
                    continue

            level, priority = self._extract_level(item.text, min_level=lower, max_level=upper)
            if level is not None:
                candidates.append((10 + priority, proximity, item_score, int(level), item_score, str(item.text)))

        if not candidates:
            level, priority = self._extract_level(merged_text, min_level=lower, max_level=upper)
            if level is not None:
                candidates.append((5 + priority, 0.0, 0.0, int(level), 0.0, merged_text))

        if not candidates:
            extra_info = {
                'tokens': raw_texts,
                'nickname_candidate': (str(nickname_item.text) if nickname_item is not None else ''),
                'level_anchor': {
                    'nickname_ref': [round(nickname_ref_x, 2), round(nickname_ref_y, 2)],
                    'legacy_ref': [round(legacy_ref_x, 2), round(legacy_ref_y, 2)],
                },
                **self._extract_structured_head_info(
                    ordered,
                    level=None,
                    nickname_text=(str(nickname_item.text) if nickname_item is not None else ''),
                ),
                **self._extract_other_info(raw_texts),
            }
            return None, 0.0, merged_text, extra_info

        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        best = candidates[0]
        level_value = int(best[3])
        extra_info = {
            'tokens': raw_texts,
            'nickname_candidate': (str(nickname_item.text) if nickname_item is not None else ''),
            'level_anchor': {
                'nickname_ref': [round(nickname_ref_x, 2), round(nickname_ref_y, 2)],
                'legacy_ref': [round(legacy_ref_x, 2), round(legacy_ref_y, 2)],
            },
            **self._extract_structured_head_info(
                ordered,
                level=level_value,
                nickname_text=(str(nickname_item.text) if nickname_item is not None else ''),
                level_raw_text=str(best[5]),
            ),
            **self._extract_other_info(raw_texts),
        }
        return level_value, best[4], best[5], extra_info

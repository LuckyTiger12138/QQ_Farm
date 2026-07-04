"""仓库种子格扫描工具。"""

from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np
from loguru import logger


WAREHOUSE_SEED_GRID_COLS: int = 5
WAREHOUSE_SEED_GRID_ROWS: int = 3
WAREHOUSE_SEED_SLOT_COUNT: int = WAREHOUSE_SEED_GRID_COLS * WAREHOUSE_SEED_GRID_ROWS

# qq-farm-copilot 当前使用的 3x5 仓库种子区域。本项目窗口可能不同，
# 扫描时会同时尝试固定 ROI 和按截图尺寸缩放后的 ROI。
WAREHOUSE_SEED_GRID_ROI: tuple[int, int, int, int] = (20, 265, 520, 615)
WAREHOUSE_SEED_BASE_SIZE: tuple[int, int] = (581, 1054)

LOCK_TEMPLATE_NAMES: tuple[str, ...] = ("icon_item_locked", "icon_seed_locked")


@dataclass(slots=True)
class WarehouseSeedSlot:
    """单个仓库种子格。"""

    raw_index: int
    available_index: int
    bbox: tuple[int, int, int, int]
    center: tuple[int, int]
    locked: bool = False


@dataclass(slots=True)
class WarehouseSeedCandidate:
    """仓库种子模板命中结果。"""

    crop_name: str
    template_name: str
    confidence: float
    position: tuple[int, int]
    slot: WarehouseSeedSlot | None

    @property
    def raw_index(self) -> int | None:
        return self.slot.raw_index if self.slot else None

    @property
    def available_index(self) -> int | None:
        return self.slot.available_index if self.slot else None

    @property
    def locked(self) -> bool:
        return bool(self.slot.locked) if self.slot else False


@dataclass(slots=True)
class WarehouseSeedScanResult:
    """仓库当前页目标种子扫描结果。"""

    crop_name: str
    slots: list[WarehouseSeedSlot]
    candidates: list[WarehouseSeedCandidate]
    locked_indexes: set[int]
    used_fallback_grid: bool = False

    @property
    def best(self) -> WarehouseSeedCandidate | None:
        usable = [item for item in self.candidates if not item.locked]
        if usable:
            return max(usable, key=lambda item: item.confidence)
        if self.candidates:
            return max(self.candidates, key=lambda item: item.confidence)
        return None

    @property
    def has_seed(self) -> bool:
        return any(not item.locked for item in self.candidates)

    @property
    def has_locked_candidate(self) -> bool:
        return any(item.locked for item in self.candidates)


def clip_bbox(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """将 bbox 夹紧到图像范围。"""
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(int(x1), max(0, int(width) - 1)))
    y1 = max(0, min(int(y1), max(0, int(height) - 1)))
    x2 = max(x1 + 1, min(int(x2), int(width)))
    y2 = max(y1 + 1, min(int(y2), int(height)))
    return x1, y1, x2, y2


def _scale_bbox(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    base_w, base_h = WAREHOUSE_SEED_BASE_SIZE
    sx = float(width) / float(base_w)
    sy = float(height) / float(base_h)
    x1, y1, x2, y2 = bbox
    return (
        int(round(x1 * sx)),
        int(round(y1 * sy)),
        int(round(x2 * sx)),
        int(round(y2 * sy)),
    )


def _candidate_rois(width: int, height: int) -> list[tuple[int, int, int, int]]:
    fixed = clip_bbox(WAREHOUSE_SEED_GRID_ROI, width=width, height=height)
    scaled = clip_bbox(_scale_bbox(WAREHOUSE_SEED_GRID_ROI, width=width, height=height), width=width, height=height)
    broad = clip_bbox(
        (
            max(0, min(fixed[0], scaled[0]) - 20),
            max(0, min(fixed[1], scaled[1]) - 35),
            min(width, max(fixed[2], scaled[2]) + 20),
            min(height, max(fixed[3], scaled[3]) + 35),
        ),
        width=width,
        height=height,
    )
    rois: list[tuple[int, int, int, int]] = []
    for roi in (fixed, scaled, broad):
        if roi not in rois:
            rois.append(roi)
    return rois


def cluster_axis_values(values: list[float], *, threshold: float) -> list[float]:
    """将同轴坐标聚类为行/列中心。"""
    if not values:
        return []
    clusters: list[list[float]] = []
    for value in sorted(float(v) for v in values):
        if not clusters or abs(value - float(np.median(clusters[-1]))) > float(threshold):
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [float(np.median(cluster)) for cluster in clusters]


def _contour_slot_candidates(
    screenshot: np.ndarray,
    roi: tuple[int, int, int, int],
) -> tuple[list[tuple[int, int, int, int]], int]:
    rx1, ry1, rx2, ry2 = roi
    roi_img = screenshot[ry1:ry2, rx1:rx2]
    if roi_img.size == 0:
        return [], 0
    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 100)
    proc = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(proc, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = float(cv2.contourArea(contour))
        if 55 <= w <= 125 and 65 <= h <= 135 and area >= 1200:
            candidates.append((rx1 + int(x), ry1 + int(y), rx1 + int(x + w), ry1 + int(y + h)))
    return candidates, len(contours)


def _boxes_from_candidates(
    candidates: list[tuple[int, int, int, int]],
    *,
    width: int,
    height: int,
) -> list[tuple[int, int, int, int]]:
    if len(candidates) < WAREHOUSE_SEED_GRID_COLS:
        return []

    centers_x = [(box[0] + box[2]) / 2.0 for box in candidates]
    centers_y = [(box[1] + box[3]) / 2.0 for box in candidates]
    col_centers = cluster_axis_values(centers_x, threshold=35.0)
    row_centers = cluster_axis_values(centers_y, threshold=40.0)
    if len(col_centers) < WAREHOUSE_SEED_GRID_COLS or not row_centers:
        return sorted(candidates, key=lambda box: (box[1], box[0]))[:WAREHOUSE_SEED_SLOT_COUNT]

    col_centers = sorted(col_centers)[:WAREHOUSE_SEED_GRID_COLS]
    row_centers = sorted(row_centers)
    if len(row_centers) < WAREHOUSE_SEED_GRID_ROWS:
        row_step = float(np.median(np.diff(row_centers))) if len(row_centers) >= 2 else 110.0
        while len(row_centers) < WAREHOUSE_SEED_GRID_ROWS:
            row_centers.append(row_centers[-1] + row_step)
    row_centers = row_centers[:WAREHOUSE_SEED_GRID_ROWS]

    widths = [box[2] - box[0] for box in candidates]
    heights = [box[3] - box[1] for box in candidates]
    cell_w = int(round(float(np.median(widths)))) if widths else 95
    cell_h = int(round(float(np.median(heights)))) if heights else 105
    cell_w = max(70, min(115, cell_w))
    cell_h = max(80, min(125, cell_h))

    boxes: list[tuple[int, int, int, int]] = []
    for cy in row_centers:
        for cx in col_centers:
            x1 = int(round(cx - cell_w / 2.0))
            y1 = int(round(cy - cell_h / 2.0))
            boxes.append(clip_bbox((x1, y1, x1 + cell_w, y1 + cell_h), width=width, height=height))
    return boxes[:WAREHOUSE_SEED_SLOT_COUNT]


def _fallback_grid_boxes(
    roi: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
) -> list[tuple[int, int, int, int]]:
    rx1, ry1, rx2, ry2 = roi
    roi_w = max(1, rx2 - rx1)
    roi_h = max(1, ry2 - ry1)
    gap_x = max(4, int(round(roi_w * 0.015)))
    gap_y = max(6, int(round(roi_h * 0.035)))
    cell_w = int(round((roi_w - gap_x * (WAREHOUSE_SEED_GRID_COLS - 1)) / WAREHOUSE_SEED_GRID_COLS))
    cell_h = int(round((roi_h - gap_y * (WAREHOUSE_SEED_GRID_ROWS - 1)) / WAREHOUSE_SEED_GRID_ROWS))
    boxes: list[tuple[int, int, int, int]] = []
    for row in range(WAREHOUSE_SEED_GRID_ROWS):
        for col in range(WAREHOUSE_SEED_GRID_COLS):
            x1 = rx1 + col * (cell_w + gap_x)
            y1 = ry1 + row * (cell_h + gap_y)
            boxes.append(clip_bbox((x1, y1, x1 + cell_w, y1 + cell_h), width=width, height=height))
    return boxes


def detect_warehouse_seed_slot_boxes(screenshot: np.ndarray) -> tuple[list[tuple[int, int, int, int]], bool]:
    """按当前截图推断仓库种子页 3x5 可见格子。"""
    if screenshot is None or screenshot.size == 0:
        return [], False

    sh, sw = screenshot.shape[:2]
    best_boxes: list[tuple[int, int, int, int]] = []
    best_score = -1
    best_raw_contours = 0
    for roi in _candidate_rois(sw, sh):
        candidates, raw_contours = _contour_slot_candidates(screenshot, roi)
        boxes = _boxes_from_candidates(candidates, width=sw, height=sh)
        score = min(len(boxes), WAREHOUSE_SEED_SLOT_COUNT) * 10 + min(len(candidates), WAREHOUSE_SEED_SLOT_COUNT)
        if score > best_score:
            best_boxes = boxes
            best_score = score
            best_raw_contours = raw_contours

    if best_boxes:
        if len(best_boxes) != WAREHOUSE_SEED_SLOT_COUNT:
            logger.debug(
                "仓库种子格扫描: 轮廓推断格数={} 原始轮廓={}",
                len(best_boxes),
                best_raw_contours,
            )
        return best_boxes[:WAREHOUSE_SEED_SLOT_COUNT], False

    fallback_roi = _candidate_rois(sw, sh)[0]
    fallback = _fallback_grid_boxes(fallback_roi, width=sw, height=sh)
    logger.warning("仓库种子格扫描: 轮廓分割失败，使用固定网格兜底 | roi={}", fallback_roi)
    return fallback, True


def build_warehouse_seed_slots(
    boxes: list[tuple[int, int, int, int]],
    locked_indexes: set[int] | None = None,
) -> list[WarehouseSeedSlot]:
    """构建带可用序号映射的仓库格列表。"""
    locked = set(locked_indexes or set())
    slots: list[WarehouseSeedSlot] = []
    available_index = 0
    for raw_index, box in enumerate(boxes, 1):
        is_locked = raw_index in locked
        if not is_locked:
            available_index += 1
        x1, y1, x2, y2 = box
        slots.append(
            WarehouseSeedSlot(
                raw_index=raw_index,
                available_index=available_index if not is_locked else 0,
                bbox=(int(x1), int(y1), int(x2), int(y2)),
                center=((int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2),
                locked=is_locked,
            )
        )
    return slots


def warehouse_seed_locked_icon_roi(slot_bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """返回仓库槽位右上角锁图标识别 ROI。"""
    x1, y1, x2, y2 = slot_bbox
    width = max(1, int(x2) - int(x1))
    height = max(1, int(y2) - int(y1))
    roi_w = max(18, min(34, width // 2))
    roi_h = max(16, min(28, height // 3))
    return int(x2) - roi_w, int(y1), int(x2), int(y1) + roi_h


def _point_in_bbox(point: tuple[int, int], bbox: tuple[int, int, int, int]) -> bool:
    x, y = point
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


def _nearest_slot(
    point: tuple[int, int],
    slots: list[WarehouseSeedSlot],
) -> WarehouseSeedSlot | None:
    if not slots:
        return None
    px, py = point
    best: tuple[float, WarehouseSeedSlot] | None = None
    for slot in slots:
        cx, cy = slot.center
        distance = math.hypot(float(px - cx), float(py - cy))
        if best is None or distance < best[0]:
            best = (distance, slot)
    if best is None:
        return None
    slot = best[1]
    x1, y1, x2, y2 = slot.bbox
    max_distance = max(x2 - x1, y2 - y1) * 0.65
    return slot if best[0] <= max_distance else None


def _assign_hit_to_slot(
    point: tuple[int, int],
    slots: list[WarehouseSeedSlot],
) -> WarehouseSeedSlot | None:
    for slot in slots:
        if _point_in_bbox(point, slot.bbox):
            return slot
    return _nearest_slot(point, slots)


def detect_locked_warehouse_slot_indexes(
    screenshot: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
    cv_detector,
) -> set[int]:
    """识别带锁仓库槽位。缺少锁模板时返回空集合。"""
    if screenshot is None or screenshot.size == 0 or not boxes:
        return set()
    if not hasattr(cv_detector, "detect_targeted"):
        return set()
    if not getattr(cv_detector, "_loaded", False):
        cv_detector.load_templates()

    templates_by_name = getattr(cv_detector, "_templates_by_name", {})
    names = [name for name in LOCK_TEMPLATE_NAMES if name in templates_by_name]
    if not names:
        return set()

    thresholds = {name: max(0.78, float(cv_detector.get_template_threshold(name))) for name in names}
    hits = cv_detector.detect_targeted(screenshot, names, thresholds=thresholds, scales=[0.9, 1.0, 1.1])
    if not hits:
        return set()

    locked_indexes: set[int] = set()
    hit_centers = [(int(hit.x), int(hit.y)) for hit in hits]
    for index, bbox in enumerate(boxes, 1):
        rx1, ry1, rx2, ry2 = warehouse_seed_locked_icon_roi(bbox)
        for hx, hy in hit_centers:
            if rx1 <= hx <= rx2 and ry1 <= hy <= ry2:
                locked_indexes.add(index)
                break
    return locked_indexes


def scan_warehouse_seed_page(
    screenshot: np.ndarray,
    crop_name: str,
    cv_detector,
) -> WarehouseSeedScanResult:
    """扫描当前仓库种子页，返回目标种子在格子中的命中情况。"""
    boxes, used_fallback_grid = detect_warehouse_seed_slot_boxes(screenshot)
    locked_indexes = detect_locked_warehouse_slot_indexes(screenshot, boxes, cv_detector)
    slots = build_warehouse_seed_slots(boxes, locked_indexes)

    template_name = f"ws_{crop_name}"
    candidates: list[WarehouseSeedCandidate] = []
    if not crop_name or not hasattr(cv_detector, "detect_targeted"):
        return WarehouseSeedScanResult(str(crop_name or ""), slots, candidates, locked_indexes, used_fallback_grid)
    if not getattr(cv_detector, "_loaded", False):
        cv_detector.load_templates()

    templates_by_name = getattr(cv_detector, "_templates_by_name", {})
    if template_name not in templates_by_name:
        logger.warning("仓库种子扫描: 缺少模板 {}", template_name)
        return WarehouseSeedScanResult(str(crop_name), slots, candidates, locked_indexes, used_fallback_grid)

    if boxes:
        x1 = max(0, min(box[0] for box in boxes) - 20)
        y1 = max(0, min(box[1] for box in boxes) - 20)
        x2 = min(screenshot.shape[1], max(box[2] for box in boxes) + 20)
        y2 = min(screenshot.shape[0], max(box[3] for box in boxes) + 20)
        roi_map = {template_name: (x1, y1, x2, y2)}
    else:
        roi_map = None

    threshold = float(cv_detector.get_template_threshold(template_name))
    hits = cv_detector.detect_targeted(
        screenshot,
        [template_name],
        thresholds={template_name: threshold},
        scales=[0.85, 0.9, 1.0, 1.1, 1.2],
        roi_map=roi_map,
    )
    for hit in hits:
        slot = _assign_hit_to_slot((int(hit.x), int(hit.y)), slots)
        candidates.append(
            WarehouseSeedCandidate(
                crop_name=str(crop_name),
                template_name=template_name,
                confidence=float(hit.confidence),
                position=(int(hit.x), int(hit.y)),
                slot=slot,
            )
        )
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return WarehouseSeedScanResult(str(crop_name), slots, candidates, locked_indexes, used_fallback_grid)

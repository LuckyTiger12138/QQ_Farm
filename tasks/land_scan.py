"""地块巡查 — 两阶段滑动扫描 + 逐块点击 OCR 采集，从 copilot 移植。"""

from __future__ import annotations

import re
import time

from loguru import logger

from core.cv_detector import DetectResult
from utils.land_grid import LandCell, get_lands_from_land_anchor
from utils.ocr_utils import OCRItem, OCRTool

# ── 常量 ──────────────────────────────────────────────────────────────
# 画面横向滑动点位（截图坐标系，相对窗口左上角）
LAND_SCAN_SWIPE_H_P1 = (350, 190)
LAND_SCAN_SWIPE_H_P2 = (200, 190)

# 地块网格
LAND_SCAN_ROWS = 4
LAND_SCAN_COLS = 6

# 固定截图尺寸（用于 ROI 边界裁剪）
LAND_SCAN_FRAME_WIDTH = 581
LAND_SCAN_FRAME_HEIGHT = 1054

# 物理列总数（1,2,3,4,4,4,3,2,1）
LAND_SCAN_PHYSICAL_COLS = 9
# 左滑阶段扫描列数
LAND_SCAN_LEFT_STAGE_COL_COUNT = 5
# 右滑阶段扫描列数
LAND_SCAN_RIGHT_STAGE_COL_COUNT = 4

# 成熟时间 OCR 大区域：相对 btn_crop_maturity_time_suffix 中心 (dx1, dy1, dx2, dy2)
LAND_SCAN_OCR_REGION_OFFSET = (-200, -50, 100, 50)
# 成熟时间二次筛选窗口：相对锚点中心偏移
LAND_SCAN_TIME_PICK_X1 = -100
LAND_SCAN_TIME_PICK_X2 = -40
LAND_SCAN_TIME_PICK_Y1 = -20
LAND_SCAN_TIME_PICK_Y2 = 20

# 空地弹窗等级 OCR 区域：相对 btn_land_pop_empty 中心
LAND_SCAN_LEVEL_REGION_OFFSET = (-60, -50, 40, 50)

# 成熟时间文本正则
LAND_SCAN_MATURITY_TIME_PATTERN = re.compile(r'(\d{2})[：:](\d{2})[：:](\d{2})')
# 地块等级文本正则
LAND_SCAN_LEVEL_PATTERN = re.compile(r'(未扩建|普通|红|黑|金)')

# 地块等级中文→英文映射
LAND_SCAN_LEVEL_LABELS: dict[str, str] = {
    'unbuilt': '未扩建', 'normal': '普通土地', 'red': '红土地',
    'black': '黑土地', 'gold': '金土地',
}

# 已播种地块等级颜色采样点：相对 btn_crop_maturity_time_suffix 中心 (dx, dy)
LAND_SCAN_PLOTTED_LEVEL_COLOR_OFFSET = (85, -10)
# 颜色采样窗口半径
LAND_SCAN_PLOTTED_LEVEL_COLOR_SAMPLE_RADIUS = 1
# 已播种地块等级颜色采样（RGB）
LAND_SCAN_PLOTTED_LEVEL_COLORS_RGB: dict[str, tuple[int, int, int]] = {
    'normal': (178, 131, 74), 'red': (223, 87, 55),
    'black': (92, 67, 42), 'gold': (249, 203, 50),
}
LAND_SCAN_PLOTTED_LEVEL_COLOR_DISTANCE_THRESHOLD = 42.0

# 弹窗等待最大重试次数
LAND_SCAN_POPUP_WAIT_RETRIES = 10
# 弹窗等待每次间隔（秒）
LAND_SCAN_POPUP_WAIT_INTERVAL = 0.2
# 点击未命中时偏移重试列表 (dx, dy)
LAND_SCAN_CLICK_RETRY_OFFSETS = [(0, -15), (0, 15), (-15, 0), (15, 0), (-10, -10), (10, 10)]

# 关闭弹窗坐标 — 点击弹窗外上方空白处（实测 y=100 有效关闭弹窗）
LAND_SCAN_GOTO_MAIN_X = 290
LAND_SCAN_GOTO_MAIN_Y = 100


class LandScanTask:
    """地块巡查：两阶段滑动扫描 → 锚点网格 → 逐块点击 OCR。

    扫描流程：
    1. 左滑 2 次 → 截图识别锚点 → 扫描右侧物理列 (1-5)
    2. 右滑 2 次 → 截图识别锚点 → 扫描左侧物理列 (6-9)
    3. 每块点击后 while 循环等待弹窗（空地/已种植）
    4. 已种植弹窗用 btn_crop_maturity_time_suffix 精确锚点做 OCR
    5. 逐块增量写入 config.land.plots
    """

    def __init__(self, *, ocr_tool: OCRTool | None = None):
        self.ocr_tool = ocr_tool

    def _ensure_ocr(self) -> OCRTool | None:
        if self.ocr_tool is not None:
            return self.ocr_tool
        try:
            from utils.ocr_provider import get_ocr_tool
            self.ocr_tool = get_ocr_tool()
        except Exception:
            return None
        return self.ocr_tool

    # ================================================================
    # 主入口
    # ================================================================

    def run(self, bot_engine) -> bool:
        """执行地块巡查，更新 config.land.plots。

        Returns:
            True 表示至少成功扫描了 1 块地块。
        """
        logger.info('地块巡查: 开始')
        rect = bot_engine._prepare_window()
        if not rect:
            logger.warning('地块巡查: 窗口未找到')
            return False

        # 确保回到主界面
        self._go_to_main(bot_engine, rect)

        scanned_count = 0
        try:
            # ── 阶段 1：左滑 → 扫描右侧物理列 ──
            for _ in range(2):
                if self._stopped(bot_engine):
                    return False
                self._swipe(bot_engine, LAND_SCAN_SWIPE_H_P1, LAND_SCAN_SWIPE_H_P2)
                time.sleep(0.5)

            cells_left = self._collect_land_cells(bot_engine, rect)
            if not cells_left:
                logger.warning('地块巡查: 左滑后未识别到地块网格')
            else:
                cells_left = self._exclude_expand_brand_cells(bot_engine, rect, cells_left)
                scanned_count += self._scan_by_physical_columns(
                    bot_engine, rect, cells_left,
                    from_side='right',
                    column_count=LAND_SCAN_LEFT_STAGE_COL_COUNT,
                )

            # ── 阶段 2：右滑 → 扫描左侧物理列 ──
            for _ in range(2):
                if self._stopped(bot_engine):
                    return False
                self._swipe(bot_engine, LAND_SCAN_SWIPE_H_P2, LAND_SCAN_SWIPE_H_P1)
                time.sleep(0.5)

            if self._stopped(bot_engine):
                return False
            cells_right = self._collect_land_cells(bot_engine, rect)
            if not cells_right:
                logger.warning('地块巡查: 右滑后未识别到地块网格')
            else:
                right_scan_cols = self._resolve_scan_columns(
                    cells_right, from_side='left',
                    column_count=LAND_SCAN_RIGHT_STAGE_COL_COUNT,
                )
                cells_right = self._exclude_expand_brand_cells(bot_engine, rect, cells_right)
                scanned_count += self._scan_by_physical_columns(
                    bot_engine, rect, cells_right,
                    from_side='left',
                    column_count=LAND_SCAN_RIGHT_STAGE_COL_COUNT,
                    fixed_cols=right_scan_cols,
                )
        finally:
            self._go_to_main(bot_engine, rect)

        if scanned_count > 0:
            bot_engine.config.save()

        logger.info(f'地块巡查: 结束，共扫描 {scanned_count} 块')
        return scanned_count > 0

    # ================================================================
    # 停止检查 + 回到主页
    # ================================================================

    @staticmethod
    def _stopped(bot_engine) -> bool:
        """检查是否请求停止。"""
        if getattr(bot_engine, '_bot_stop_requested', False):
            return True
        return False

    @staticmethod
    def _go_to_main(bot_engine, rect: tuple) -> None:
        """关闭地块弹窗：对齐 copilot，点固定坐标即可，不做检测循环。"""
        abs_x, abs_y = bot_engine.action_executor.relative_to_absolute(
            LAND_SCAN_GOTO_MAIN_X, LAND_SCAN_GOTO_MAIN_Y,
        )
        logger.trace(
            f'地块巡查: 关闭弹窗 客户区=({LAND_SCAN_GOTO_MAIN_X},{LAND_SCAN_GOTO_MAIN_Y})'
        )
        bot_engine.action_executor.click(abs_x, abs_y)
        time.sleep(0.3)

    # ================================================================
    # 滑动
    # ================================================================

    @staticmethod
    def _swipe(bot_engine, p1: tuple[int, int], p2: tuple[int, int]) -> None:
        """在截图坐标系中执行滑动（p1 → p2）。"""
        ax1, ay1 = bot_engine.action_executor.relative_to_absolute(p1[0], p1[1])
        ax2, ay2 = bot_engine.action_executor.relative_to_absolute(p2[0], p2[1])
        dx, dy = ax2 - ax1, ay2 - ay1
        bot_engine.action_executor.drag(ax1, ay1, dx, dy, duration=0.3, steps=10)

    # ================================================================
    # 锚点识别 + 网格构建
    # ================================================================

    @staticmethod
    def _collect_land_cells(bot_engine, rect: tuple) -> list[LandCell]:
        """识别左右锚点并推算地块网格。"""
        cv_img = bot_engine._capture_only(rect)
        if cv_img is None:
            return []
        # 只检测锚点模板
        detections = bot_engine.cv_detector.detect_targeted(
            cv_img, ['btn_land_right', 'btn_land_left', 'btn_expand_brand'],
            scales=[1.0, 0.9, 1.1],
        )

        right_anchor = None
        left_anchor = None
        for det in detections:
            if det.name == 'btn_land_right':
                right_anchor = (int(det.x), int(det.y))
            elif det.name == 'btn_land_left':
                left_anchor = (int(det.x), int(det.y))
                logger.trace(f'地块巡查: 左锚点检测 | pos=({det.x:.0f},{det.y:.0f}) conf={det.confidence:.2f}')
        if right_anchor:
            logger.trace(f'地块巡查: 右锚点检测 | pos={right_anchor}')
        else:
            logger.trace('地块巡查: 右锚点未检测到，将根据左锚点推算')

        cells = get_lands_from_land_anchor(
            right_anchor, left_anchor,
            rows=LAND_SCAN_ROWS, cols=LAND_SCAN_COLS,
            start_anchor='right',
        )
        logger.info(
            f'地块巡查: 网格识别 | 右锚点={right_anchor} '
            f'左锚点={left_anchor} 地块={len(cells)}'
        )
        return cells

    # ================================================================
    # 未扩建地块排除
    # ================================================================

    @staticmethod
    def _exclude_expand_brand_cells(
        bot_engine, rect: tuple, cells: list[LandCell],
    ) -> list[LandCell]:
        """检测扩建标志牌，排除不可统计的地块。"""
        cv_img = bot_engine._capture_only(rect)
        if cv_img is None:
            return cells
        detections = bot_engine.cv_detector.detect_targeted(
            cv_img, ['btn_expand_brand'], scales=[1.0, 0.9, 1.1],
        )
        if cv_img is None:
            return cells

        brand_det = None
        for det in detections:
            if det.name == 'btn_expand_brand':
                brand_det = det
                break
        if brand_det is None:
            return cells

        brand_pos = (int(brand_det.x), int(brand_det.y))
        target_cell = _pick_nearest_cell(cells, brand_pos)
        if target_cell is None:
            return cells

        excluded_labels = _build_expand_brand_excluded_labels(target_cell)
        filtered = [c for c in cells if c.label not in excluded_labels]
        logger.info(
            f'地块巡查: 排除未扩建地块 | 排除={sorted(excluded_labels)} '
            f'剩余={len(filtered)}/{len(cells)}'
        )
        return filtered

    # ================================================================
    # 物理列扫描
    # ================================================================

    def _scan_by_physical_columns(
        self,
        bot_engine,
        rect: tuple,
        cells: list[LandCell],
        *,
        from_side: str,
        column_count: int,
        fixed_cols: list[int] | None = None,
    ) -> int:
        """按画面物理列扫描地块（列内从上到下）。"""
        col_map: dict[int, list[LandCell]] = {}
        for cell in cells:
            pc = _physical_col_rtl(cell)
            col_map.setdefault(pc, []).append(cell)

        if fixed_cols is not None:
            scan_cols = list(fixed_cols)
        else:
            scan_cols = self._resolve_scan_columns(
                cells, from_side=from_side, column_count=column_count,
            )

        logger.info(f'地块巡查: 物理列={scan_cols}')
        scanned = 0
        hit_unbuilt = False
        for physical_col in scan_cols:
            if self._stopped(bot_engine):
                return scanned
            if hit_unbuilt:
                logger.info(f'地块巡查: 跳过物理列 {physical_col}（之前检测到未扩建）')
                continue
            col_cells = list(col_map.get(physical_col, []))
            col_cells.sort(key=lambda c: (c.center[1], c.center[0]))
            for cell in col_cells:
                if self._stopped(bot_engine):
                    return scanned
                updated, is_unbuilt = self._click_and_ocr_cell(
                    bot_engine, rect, cell, from_side=from_side,
                )
                if updated:
                    scanned += 1
                if is_unbuilt:
                    hit_unbuilt = True
                    break
                # 回到主界面，等待弹窗关闭
                self._go_to_main(bot_engine, rect)
                time.sleep(0.3)
            if not hit_unbuilt:
                # 回到主界面
                self._go_to_main(bot_engine, rect)
                time.sleep(0.3)
        return scanned

    def _resolve_scan_columns(
        self, cells: list[LandCell], *, from_side: str, column_count: int,
    ) -> list[int]:
        """根据当前网格确定本轮应扫描的物理列。"""
        col_map: dict[int, list[LandCell]] = {}
        for cell in cells:
            col_map.setdefault(_physical_col_rtl(cell), []).append(cell)
        rtl_cols = sorted(col_map.keys())
        if from_side.strip().lower() == 'left':
            return list(reversed(rtl_cols))[:max(0, column_count)]
        return rtl_cols[:max(0, column_count)]

    # ================================================================
    # 滑动复位
    # ================================================================

    def _recenter_swipe(self, bot_engine, from_side: str) -> None:
        """根据扫描方向滑动一次，将页面复位到正确的查看位置。"""
        if from_side.strip().lower() == 'left':
            # 扫描左侧列 → 需要右滑让左侧可见
            self._swipe(bot_engine, LAND_SCAN_SWIPE_H_P2, LAND_SCAN_SWIPE_H_P1)
        else:
            # 扫描右侧列 → 需要左滑让右侧可见
            self._swipe(bot_engine, LAND_SCAN_SWIPE_H_P1, LAND_SCAN_SWIPE_H_P2)

    # ================================================================
    # 弹窗等待
    # ================================================================

    def _wait_for_popup(
        self, bot_engine, rect: tuple,
    ) -> tuple | None:
        """等待地块弹窗出现，返回 (cv_img, detections, found_planted, empty_det) 或 None。"""
        # 弹窗等待只需检测这 3 个模板
        _POPUP_TEMPLATES = ['btn_crop_removal', 'btn_crop_maturity_time_suffix',
                            'btn_land_pop_empty']

        cv_img = None
        detections: list[DetectResult] = []
        found_planted = False
        empty_det = None

        for attempt in range(LAND_SCAN_POPUP_WAIT_RETRIES):
            if self._stopped(bot_engine):
                return None
            time.sleep(LAND_SCAN_POPUP_WAIT_INTERVAL)
            cv_img = bot_engine._capture_only(rect)
            if cv_img is None:
                continue
            detections = bot_engine.cv_detector.detect_targeted(
                cv_img, _POPUP_TEMPLATES, scales=[1.0, 0.9, 1.1],
            )

            # 检查空地弹窗
            for det in detections:
                if det.name == 'btn_land_pop_empty':
                    empty_det = det
                    break
            if empty_det:
                return (cv_img, detections, False, empty_det)

            # 检查已种植弹窗
            has_removal = any(d.name == 'btn_crop_removal' for d in detections)
            has_time_suffix = any(d.name == 'btn_crop_maturity_time_suffix' for d in detections)
            if has_removal and has_time_suffix:
                return (cv_img, detections, True, None)

        return None

    # ================================================================
    # 单块点击 + OCR（copilot 对齐版）
    # ================================================================

    def _click_and_ocr_cell(
        self, bot_engine, rect: tuple, cell: LandCell,
        *, from_side: str = 'right',
    ) -> tuple[bool, bool]:
        """点击单个地块并采集信息。

        Returns:
            (updated, is_unbuilt): updated 表示数据是否有更新，
            is_unbuilt 表示是否检测到未扩建地块（后续列应跳过）。

        流程：
        1. 点击地块中心，等待弹窗
        2. 弹窗未出现 → 滑动复位页面 + 刷新坐标后重试
        3. 仍未出现 → 用偏移位置兜底重试
        """
        target = cell

        # 第 1 次：原始坐标
        logger.trace(
            f'地块巡查: 点击地块 | 序号={cell.label} '
            f'客户区=({target.center[0]},{target.center[1]})'
        )
        abs_x, abs_y = bot_engine.action_executor.relative_to_absolute(
            target.center[0], target.center[1],
        )
        bot_engine.action_executor.click(abs_x, abs_y)
        result = self._wait_for_popup(bot_engine, rect)

        # 第 2 次：滑动复位 + 刷新坐标后重试（对齐 copilot 的滑动定位）
        if result is None:
            logger.trace(f'地块巡查: 弹窗未出现，滑动复位 | 序号={cell.label}')
            self._recenter_swipe(bot_engine, from_side)
            time.sleep(0.3)
            # 刷新坐标
            fresh_cells = self._collect_land_cells(bot_engine, rect)
            if fresh_cells:
                for fc in fresh_cells:
                    if fc.label == cell.label:
                        target = fc
                        break
            logger.trace(
                f'地块巡查: 复位后重试 | 序号={cell.label} '
                f'客户区=({target.center[0]},{target.center[1]})'
            )
            abs_x, abs_y = bot_engine.action_executor.relative_to_absolute(
                target.center[0], target.center[1],
            )
            bot_engine.action_executor.click(abs_x, abs_y)
            result = self._wait_for_popup(bot_engine, rect)

        # 第 3 次：偏移兜底
        if result is None:
            for dx, dy in LAND_SCAN_CLICK_RETRY_OFFSETS:
                if self._stopped(bot_engine):
                    return False, False
                cx, cy = target.center[0] + dx, target.center[1] + dy
                logger.trace(
                    f'地块巡查: 偏移重试 | 序号={cell.label} '
                    f'偏移=({dx},{dy}) 客户区=({cx},{cy})'
                )
                abs_x, abs_y = bot_engine.action_executor.relative_to_absolute(cx, cy)
                bot_engine.action_executor.click(abs_x, abs_y)
                result = self._wait_for_popup(bot_engine, rect)
                if result is not None:
                    break

        if result is None:
            logger.warning(f'地块巡查: 弹窗未出现（含滑动复位+偏移重试） | 序号={cell.label}')
            return False, False

        cv_img, detections, found_planted, empty_det = result

        # ── 空地弹窗 ──
        if empty_det:
            level = self._ocr_land_level(cv_img, empty_det)
            is_unbuilt = (level == 'unbuilt')
            if is_unbuilt:
                logger.info(f'地块巡查: 检测到未扩建地块 {cell.label}，后续列将跳过')
            updated = self._update_plot(
                bot_engine, cell,
                level=level or 'normal', countdown='',
                need_upgrade=False, need_planting=True,
            )
            return updated, is_unbuilt

        # ── 已种植弹窗 ──
        # 找到 maturity_time_suffix 作为精确锚点
        time_suffix_det = None
        for det in detections:
            if det.name == 'btn_crop_maturity_time_suffix':
                time_suffix_det = det
                break

        anchor = None
        if time_suffix_det:
            anchor = (int(time_suffix_det.x), int(time_suffix_det.y))
        else:
            logger.warning(
                f'地块巡查: 未识别到成熟时间锚点，使用地块中心 | 序号={cell.label}'
            )

        # 颜色等级检测（基于锚点偏移）
        level = self._detect_level_by_color(cv_img, anchor or cell.center)

        # 成熟时间 OCR（基于锚点精确偏移）
        countdown = ''
        if anchor:
            countdown = self._ocr_maturity_time(cv_img, anchor)
        elif level == 'normal':
            # 回退：用地块中心做粗略 OCR
            countdown = self._ocr_maturity_time_fallback(cv_img, cell.center)

        need_planting = level == 'normal' and not countdown
        updated = self._update_plot(
            bot_engine, cell,
            level=level or 'normal', countdown=countdown,
            need_upgrade=False, need_planting=need_planting,
        )
        return updated, False

    # ================================================================
    # OCR 辅助
    # ================================================================

    def _ocr_land_level(
        self, cv_img, anchor_det: DetectResult,
    ) -> str | None:
        """空地弹窗中 OCR 识别地块等级。"""
        ocr = self._ensure_ocr()
        if ocr is None:
            return None
        roi = _build_roi(anchor_det.x, anchor_det.y, LAND_SCAN_LEVEL_REGION_OFFSET)
        items = ocr.detect(cv_img, region=roi, scale=1.2, alpha=1.1, beta=0.0)
        text = _merge_ocr_items_text(items)
        level = _extract_land_level(text)
        logger.info(
            f'地块巡查: 空地等级OCR | text={_short_text(text)} 等级={level}'
        )
        return level

    def _ocr_maturity_time(
        self, cv_img, anchor: tuple[int, int],
    ) -> str:
        """基于 btn_crop_maturity_time_suffix 锚点精确 OCR 成熟倒计时。

        先在大区域 OCR，再在锚点附近小窗口二次筛选 token（对齐 copilot）。
        """
        ocr = self._ensure_ocr()
        if ocr is None:
            return ''
        roi = _build_roi(anchor[0], anchor[1], LAND_SCAN_OCR_REGION_OFFSET)
        items = ocr.detect(cv_img, region=roi, scale=1.2, alpha=1.1, beta=0.0)

        # 二次筛选：在锚点偏移小窗口内过滤 token
        text, score, tokens = _pick_time_tokens_near_suffix(items, anchor)
        countdown = ''
        match = LAND_SCAN_MATURITY_TIME_PATTERN.search(text)
        if match:
            countdown = f'{match.group(1)}:{match.group(2)}:{match.group(3)}'

        display = countdown or text
        logger.trace(
            f'地块巡查: OCR筛选 | region={roi} '
            f'pick_offset=({LAND_SCAN_TIME_PICK_X1},{LAND_SCAN_TIME_PICK_Y1},'
            f'{LAND_SCAN_TIME_PICK_X2},{LAND_SCAN_TIME_PICK_Y2}) '
            f'tokens={tokens} text={display or "<empty>"}'
        )
        if display:
            logger.info(
                f'地块巡查: 成熟时间OCR | text={_short_text(display)} score={score:.3f}'
            )
        return countdown

    def _ocr_maturity_time_fallback(
        self, cv_img, center: tuple[int, int],
    ) -> str:
        """回退：用地块中心固定偏移做 OCR（无锚点时使用）。"""
        ocr = self._ensure_ocr()
        if ocr is None:
            return ''
        roi = _build_roi(center[0], center[1], (-100, -40, 60, 40))
        items = ocr.detect(cv_img, region=roi, scale=1.2, alpha=1.1, beta=0.0)
        text = _merge_ocr_items_text(items)
        match = LAND_SCAN_MATURITY_TIME_PATTERN.search(text)
        if match:
            return f'{match.group(1)}:{match.group(2)}:{match.group(3)}'
        return ''

    @staticmethod
    def _detect_level_by_color(
        cv_img, anchor: tuple[int, int],
    ) -> str | None:
        """通过颜色采样判断已播种地块的等级（基于锚点偏移）。"""
        if cv_img is None:
            return None
        h, w = cv_img.shape[:2]
        ox, oy = LAND_SCAN_PLOTTED_LEVEL_COLOR_OFFSET
        cx = max(0, min(int(anchor[0]) + ox, w - 1))
        cy = max(0, min(int(anchor[1]) + oy, h - 1))
        r = LAND_SCAN_PLOTTED_LEVEL_COLOR_SAMPLE_RADIUS
        x1, y1 = max(0, cx - r), max(0, cy - r)
        x2, y2 = min(w, cx + r + 1), min(h, cy + r + 1)
        patch = cv_img[y1:y2, x1:x2]
        if patch.size <= 0:
            return None
        mean_bgr = patch.reshape(-1, 3).mean(axis=0)
        rgb = (int(mean_bgr[2]), int(mean_bgr[1]), int(mean_bgr[0]))

        best_level = ''
        best_distance = float('inf')
        for level, color_rgb in LAND_SCAN_PLOTTED_LEVEL_COLORS_RGB.items():
            dr = float(rgb[0] - color_rgb[0])
            dg = float(rgb[1] - color_rgb[1])
            db = float(rgb[2] - color_rgb[2])
            distance = float((dr * dr + dg * dg + db * db) ** 0.5)
            if distance < best_distance:
                best_distance = distance
                best_level = level
        if best_distance > LAND_SCAN_PLOTTED_LEVEL_COLOR_DISTANCE_THRESHOLD:
            return None
        return best_level

    # ================================================================
    # 数据更新
    # ================================================================

    @staticmethod
    def _update_plot(
        bot_engine, cell: LandCell, *,
        level: str, countdown: str,
        need_upgrade: bool, need_planting: bool,
    ) -> bool:
        """增量更新单个地块字段到 config.land.plots，并通知 UI 刷新。"""
        plots = bot_engine.config.land.plots
        if not isinstance(plots, list):
            plots = []
            bot_engine.config.land.plots = plots

        target = cell.label
        changed = False
        for item in plots:
            if not isinstance(item, dict):
                continue
            if str(item.get('plot_id', '')).strip() != target:
                continue
            # 增量更新变化字段
            if level and str(item.get('level', '')).strip().lower() != level:
                item['level'] = level
                changed = True
            if str(item.get('maturity_countdown', '')).strip() != countdown:
                item['maturity_countdown'] = countdown
                changed = True
            if bool(item.get('need_upgrade', False)) != need_upgrade:
                item['need_upgrade'] = need_upgrade
                changed = True
            if bool(item.get('need_planting', False)) != need_planting:
                item['need_planting'] = need_planting
                changed = True
            if changed:
                logger.info(
                    f'地块巡查: 更新 {target} | '
                    f'等级={_level_label(level)} 倒计时={countdown} '
                    f'升级={need_upgrade} 播种={need_planting}'
                )
                _emit_land_update(bot_engine)
            return changed

        # 新地块，追加
        plots.append({
            'plot_id': target,
            'level': level,
            'maturity_countdown': countdown,
            'need_upgrade': need_upgrade,
            'need_planting': need_planting,
        })
        logger.info(
            f'地块巡查: 新增 {target} | '
            f'等级={_level_label(level)} 倒计时={countdown}'
        )
        _emit_land_update(bot_engine)
        return True


# ── 纯函数工具 ────────────────────────────────────────────────────────

def _emit_land_update(bot_engine) -> None:
    """通知 UI 刷新地块数据（跨线程安全）。"""
    try:
        bot_engine.config_updated.emit(bot_engine.config)
    except Exception:
        pass

def _physical_col_rtl(cell: LandCell) -> int:
    """将地块映射为物理列索引（右到左，范围 1..9）。"""
    idx = (LAND_SCAN_ROWS - cell.row) + (cell.col - 1) + 1
    return max(1, min(LAND_SCAN_PHYSICAL_COLS, idx))


def _pick_nearest_cell(
    cells: list[LandCell], point: tuple[int, int],
) -> LandCell | None:
    if not cells:
        return None
    px, py = point
    return min(cells, key=lambda c: (c.center[0] - px) ** 2 + (c.center[1] - py) ** 2)


def _build_expand_brand_excluded_labels(cell: LandCell) -> set[str]:
    """构造需排除的序号集合：扩建按列顺序递增，当前列未扩建则当前列及之后所有列都不存在。

    规则：如果 4-1 没扩建，那 4-1~4-4、5-1~5-4、6-1~6-4 全都不存在。
    即从命中的 (col, row) 开始，当前列从 row 到底部 + col+1 到最后一列的全部地块。
    """
    col, row = cell.col, cell.row
    labels: set[str] = set()
    # 当前列：从命中行到最下方
    for r in range(row, LAND_SCAN_ROWS + 1):
        labels.add(f'{col}-{r}')
    # 右侧所有列：整列排除
    for c in range(col + 1, LAND_SCAN_COLS + 1):
        for r in range(1, LAND_SCAN_ROWS + 1):
            labels.add(f'{c}-{r}')
    return labels


def _build_roi(
    cx: int | float, cy: int | float,
    offset: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """按中心 + 偏移构造 ROI，自动裁剪到截图边界。"""
    cx, cy = int(cx), int(cy)
    dx1, dy1, dx2, dy2 = offset
    x1, y1 = cx + dx1, cy + dy1
    x2, y2 = cx + dx2, cy + dy2
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    x1 = max(0, min(x1, LAND_SCAN_FRAME_WIDTH - 1))
    y1 = max(0, min(y1, LAND_SCAN_FRAME_HEIGHT - 1))
    x2 = max(x1 + 1, min(x2, LAND_SCAN_FRAME_WIDTH))
    y2 = max(y1 + 1, min(y2, LAND_SCAN_FRAME_HEIGHT))
    return x1, y1, x2, y2


def _merge_ocr_items_text(items: list[OCRItem]) -> str:
    if not items:
        return ''
    ordered = sorted(items, key=lambda it: min(float(p[0]) for p in it.box))
    return ''.join(str(it.text or '').strip() for it in ordered if str(it.text or '').strip()).strip()


def _pick_time_tokens_near_suffix(
    items: list[OCRItem],
    anchor: tuple[int, int],
) -> tuple[str, float, list[str]]:
    """从 OCR 明细中在锚点附近小窗口内二次筛选 token（对齐 copilot）。"""
    ax, ay = int(anchor[0]), int(anchor[1])
    x1 = float(ax + LAND_SCAN_TIME_PICK_X1)
    x2 = float(ax + LAND_SCAN_TIME_PICK_X2)
    y1 = float(ay + LAND_SCAN_TIME_PICK_Y1)
    y2 = float(ay + LAND_SCAN_TIME_PICK_Y2)
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1

    candidates: list[tuple[float, str, float]] = []
    for item in items:
        text = str(item.text or '').strip()
        if not text:
            continue
        xs = [float(p[0]) for p in item.box]
        ys = [float(p[1]) for p in item.box]
        min_x, max_x = float(min(xs)), float(max(xs))
        min_y, max_y = float(min(ys)), float(max(ys))
        if max_x <= x1 or min_x >= x2:
            continue
        if max_y <= y1 or min_y >= y2:
            continue
        candidates.append((min_x, text, float(item.score)))

    candidates.sort(key=lambda row: row[0])
    tokens = [row[1] for row in candidates]
    merged = ''.join(tokens).strip()
    if not candidates:
        return '', 0.0, []
    score = float(sum(row[2] for row in candidates) / len(candidates))
    return merged, score, tokens


def _extract_land_level(text: str) -> str | None:
    raw = str(text or '').strip().replace(' ', '')
    if not raw:
        return None
    match = LAND_SCAN_LEVEL_PATTERN.search(raw)
    if not match:
        return None
    token = match.group(1)
    return {
        '未扩建': 'unbuilt', '普通': 'normal',
        '红': 'red', '黑': 'black', '金': 'gold',
    }.get(token)


def _level_label(level: str | None) -> str:
    if not level:
        return '<empty>'
    return LAND_SCAN_LEVEL_LABELS.get(level, level)


def _short_text(text: str, limit: int = 36) -> str:
    clean = str(text or '').strip().replace('\n', ' ')
    if len(clean) <= limit:
        return clean or '<empty>'
    return f'{clean[:limit]}...'

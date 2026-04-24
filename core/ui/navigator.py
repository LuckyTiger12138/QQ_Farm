"""页面导航引擎：BFS 路径规划 + ui_ensure + 弹窗处理。"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable

from loguru import logger

from core.cv_detector import DetectResult
from core.ui.page import Page, page_main, ALL_PAGES


class Navigator:
    """基于 BFS 的页面导航器，适配纯模板匹配系统。"""

    def __init__(
        self,
        capture_fn: Callable[[tuple], tuple],
        click_fn: Callable[[int, int, str], None],
        stopped_fn: Callable[[], bool],
    ):
        """
        Args:
            capture_fn: (rect) -> (cv_img, detections, _)
            click_fn: (x, y, desc) -> None
            stopped_fn: () -> bool
        """
        self.capture = capture_fn
        self.click = click_fn
        self.is_stopped = stopped_fn

    # ── 检测工具 ──────────────────────────────────────────

    def _find(self, dets: list[DetectResult], templates: list[str]) -> DetectResult | None:
        """在检测结果中查找任一模板。"""
        for d in dets:
            if d.name in templates:
                return d
        return None

    # ── 页面识别 ──────────────────────────────────────────

    def get_current_page(self, rect: tuple) -> Page | None:
        """识别当前所在页面。"""
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return None
        det_names = [d.name for d in dets]
        for page in ALL_PAGES:
            if self._find(dets, page.check_templates):
                logger.debug(f"导航: 识别到页面={page.cn_name} (匹配: {page.check_templates})")
                return page
        logger.debug(f"导航: 未识别页面，检测到: {det_names}")
        return None

    # ── BFS 导航 ─────────────────────────────────────────

    @staticmethod
    def _bfs(start: Page, target: Page, pages: list[Page]) -> list[Page] | None:
        """BFS 找最短路径，返回页面列表。"""
        if start == target:
            return [start]
        queue = deque([start])
        visited = {start.name}
        parent: dict[str, Page] = {}
        while queue:
            current = queue.popleft()
            for dest in pages:
                if dest.name in visited:
                    continue
                if dest.name in current.links:
                    visited.add(dest.name)
                    parent[dest.name] = current
                    if dest == target:
                        # 回溯路径
                        path = [dest]
                        node = current
                        while node != start:
                            path.append(node)
                            node = parent[node.name]
                        path.append(start)
                        path.reverse()
                        return path
                    queue.append(dest)
        return None

    def navigate_to(
        self,
        target: Page,
        rect: tuple,
        timeout: float = 15.0,
    ) -> bool:
        """导航到目标页面。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_stopped():
                return False
            current = self.get_current_page(rect)
            if current == target:
                return True
            if current is None:
                logger.debug(f"导航: 未识别页面，尝试关闭弹窗")
                self.handle_close_popup(rect)
                time.sleep(0.5)
                continue
            path = self._bfs(current, target, ALL_PAGES)
            if not path or len(path) < 2:
                logger.debug(f"导航: {current.cn_name}→{target.cn_name} 无路径")
                self.handle_close_popup(rect)
                time.sleep(0.5)
                continue
            next_page = path[1]
            btn_names = current.links.get(next_page.name, [])
            cv_img, dets, _ = self.capture(rect)
            btn = self._find(dets, btn_names)
            if btn:
                logger.info(f"导航: {current.cn_name} → {next_page.cn_name}")
                self.click(btn.x + btn.w // 2, btn.y + btn.h // 2,
                           f"导航到{next_page.cn_name}")
                time.sleep(1.0)
            else:
                logger.debug(f"导航: 找不到按钮 {btn_names}，尝试关闭弹窗")
                self.handle_close_popup(rect)
                time.sleep(0.5)
        logger.warning(f"导航: 到达 {target.cn_name} 超时")
        return False

    def ui_ensure(
        self,
        target: Page,
        rect: tuple,
        timeout: float = 15.0,
    ) -> bool:
        """确保在目标页面，不在则自动导航。"""
        current = self.get_current_page(rect)
        if current == target:
            logger.debug(f"已在页面: {target.cn_name}")
            return True
        logger.info(f"跳转到页面: {target.cn_name}")
        return self.navigate_to(target, rect, timeout=timeout)

    # ── 弹窗处理 ─────────────────────────────────────────

    _POPUP_TEMPLATES = ["btn_close", "btn_info_close", "btn_click_to_close"]

    def handle_close_popup(self, rect: tuple) -> bool:
        """处理通用弹窗，找到关闭按钮则点击。"""
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return False
        btn = self._find(dets, self._POPUP_TEMPLATES)
        if btn:
            self.click(btn.x + btn.w // 2, btn.y + btn.h // 2, "关闭弹窗")
            time.sleep(0.3)
            return True
        return False

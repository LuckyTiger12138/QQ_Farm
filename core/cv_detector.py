"""OpenCV 视觉检测引擎 - 模板匹配识别游戏UI元素"""
import json
import os
import cv2
import numpy as np
from dataclasses import dataclass, field
from loguru import logger
from PIL import Image


@dataclass
class DetectResult:
    """单个检测结果"""
    name: str           # 模板名称，如 "btn_harvest", "icon_weed"
    category: str       # 类别，如 "button", "status_icon", "crop"
    x: int              # 匹配中心x（相对于截图）
    y: int              # 匹配中心y
    w: int              # 匹配区域宽
    h: int              # 匹配区域高
    confidence: float   # 匹配置信度 0~1
    extra: dict = field(default_factory=dict)

    @property
    def center(self) -> tuple[int, int]:
        return self.x, self.y

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """左上角和右下角 (x1, y1, x2, y2)"""
        return (self.x - self.w // 2, self.y - self.h // 2,
                self.x + self.w // 2, self.y + self.h // 2)


# 模板类别定义
TEMPLATE_CATEGORIES = {
    "btn": "button",
    "bth": "button",  # 施肥按钮等特殊按钮
    "icon": "status_icon",
    "crop": "crop",
    "ui": "ui_element",
    "land": "land",
    "seed": "seed",
    "shop": "shop",
    "friend": "ui_element",  # 好友列表页标识
    "ws": "warehouse_seed",  # 仓库种子（用于仓库界面检测）
}


class CVDetector:
    """基于OpenCV模板匹配的游戏UI检测器"""

    # 类别默认阈值
    CATEGORY_DEFAULTS: dict[str, float] = {
        "button": 0.8,
        "status_icon": 0.8,
        "crop": 0.8,
        "ui_element": 0.8,
        "land": 0.7,
        "seed": 0.8,
        "shop": 0.8,
        "warehouse_seed": 0.8,  # 新增：仓库种子
        "unknown": 0.8,
    }
    # 内置默认值（用于"恢复默认"）
    _BUILTIN_CATEGORY_DEFAULTS: dict[str, float] = {
        "button": 0.8,
        "status_icon": 0.8,
        "crop": 0.8,
        "ui_element": 0.8,
        "land": 0.7,
        "seed": 0.8,
        "shop": 0.8,
        "warehouse_seed": 0.8,  # 新增：仓库种子
        "unknown": 0.8,
    }

    def __init__(self, templates_dir: str = "templates"):
        self._templates_dir = templates_dir
        self._templates: dict[str, list[dict]] = {}  # category -> [{name, image, mask}]
        self._loaded = False
        self._disabled_names: set[str] = set()
        self._disabled_file = os.path.join(templates_dir, "disabled.json")
        self._thresholds: dict[str, float] = {}
        self._thresholds_file = os.path.join(templates_dir, "thresholds.json")
        self._category_overrides: dict[str, float] = {}  # 用户自定义的类别阈值
        self._load_disabled()
        self._load_thresholds()

    def _load_disabled(self):
        """从 disabled.json 加载已禁用的模板列表"""
        if os.path.exists(self._disabled_file):
            try:
                with open(self._disabled_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._disabled_names = set(data.get("disabled", []))
            except Exception as e:
                logger.warning(f"读取禁用模板配置失败: {e}")
                self._disabled_names = set()

    def _save_disabled(self):
        """保存禁用模板列表到 disabled.json"""
        try:
            os.makedirs(os.path.dirname(self._disabled_file), exist_ok=True)
            with open(self._disabled_file, "w", encoding="utf-8") as f:
                json.dump({"disabled": sorted(self._disabled_names)}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存禁用模板配置失败: {e}")

    def is_template_disabled(self, name: str) -> bool:
        return name in self._disabled_names

    def set_template_enabled(self, name: str, enabled: bool):
        """启用或禁用指定模板"""
        if enabled:
            self._disabled_names.discard(name)
        else:
            self._disabled_names.add(name)
        self._save_disabled()

    def get_disabled_templates(self) -> set[str]:
        return set(self._disabled_names)

    # ── 单模板阈值 ─────────────────────────────────────────

    def _load_thresholds(self):
        """从 thresholds.json 加载单模板阈值和类别阈值覆盖"""
        if os.path.exists(self._thresholds_file):
            try:
                with open(self._thresholds_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._thresholds = {k: float(v) for k, v in data.get("thresholds", {}).items()}
                self._category_overrides = {k: float(v) for k, v in data.get("category_overrides", {}).items()}
            except Exception as e:
                logger.warning(f"读取模板阈值配置失败: {e}")
                self._thresholds = {}
                self._category_overrides = {}

    def _save_thresholds(self):
        """保存单模板阈值和类别阈值覆盖到 thresholds.json"""
        try:
            os.makedirs(os.path.dirname(self._thresholds_file), exist_ok=True)
            with open(self._thresholds_file, "w", encoding="utf-8") as f:
                json.dump({
                    "thresholds": self._thresholds,
                    "category_overrides": self._category_overrides,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存模板阈值配置失败: {e}")

    def get_template_threshold(self, name: str) -> float:
        """获取模板阈值：单模板 > 类别覆盖 > 内置类别默认 > 全局默认 0.8"""
        if name in self._thresholds:
            return self._thresholds[name]
        prefix = name.split("_")[0]
        cat = TEMPLATE_CATEGORIES.get(prefix, "unknown")
        if cat in self._category_overrides:
            return self._category_overrides[cat]
        return self.CATEGORY_DEFAULTS.get(cat, 0.8)

    def set_template_threshold(self, name: str, value: float):
        """设置单模板阈值并持久化"""
        value = max(0.1, min(1.0, round(value, 2)))
        self._thresholds[name] = value
        self._save_thresholds()

    def get_all_thresholds(self) -> dict[str, float]:
        return dict(self._thresholds)

    def reset_template_threshold(self, name: str):
        """移除单模板自定义阈值，恢复为类别默认"""
        if name in self._thresholds:
            del self._thresholds[name]
            self._save_thresholds()

    # ── 类别默认阈值 ─────────────────────────────────────────

    def get_category_defaults(self) -> dict[str, float]:
        """获取当前生效的类别阈值（用户覆盖 > 内置默认）"""
        result = dict(self.CATEGORY_DEFAULTS)
        result.update(self._category_overrides)
        return result

    def set_category_default(self, category: str, value: float):
        """设置类别默认阈值覆盖"""
        value = max(0.1, min(1.0, round(value, 2)))
        self._category_overrides[category] = value
        self._save_thresholds()

    def reset_category_defaults(self):
        """重置所有类别阈值为内置默认值"""
        self._category_overrides.clear()
        self._save_thresholds()

    def get_all_template_names(self) -> list[str]:
        """返回 templates/ 目录下所有模板文件名（不含扩展名）"""
        names = []
        if not os.path.exists(self._templates_dir):
            return names
        for filename in os.listdir(self._templates_dir):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                names.append(os.path.splitext(filename)[0])
        return sorted(names)

    def load_templates(self):
        """加载所有模板图片"""
        self._templates = {}
        if not os.path.exists(self._templates_dir):
            os.makedirs(self._templates_dir, exist_ok=True)
            logger.warning(f"模板目录 {self._templates_dir} 为空，请先采集模板")
            return

        count = 0
        skipped = 0
        for filename in os.listdir(self._templates_dir):
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                continue

            name = os.path.splitext(filename)[0]

            # 跳过被禁用的模板
            if name in self._disabled_names:
                skipped += 1
                continue

            filepath = os.path.join(self._templates_dir, filename)
            # cv2.imread 不支持中文路径，用 numpy 中转
            template = cv2.imdecode(
                np.fromfile(filepath, dtype=np.uint8), cv2.IMREAD_UNCHANGED
            )
            if template is None:
                logger.warning(f"无法读取模板: {filename}")
                continue

            # 从文件名前缀判断类别: btn_harvest.png -> button
            prefix = name.split("_")[0]
            category = TEMPLATE_CATEGORIES.get(prefix, "unknown")

            # 处理带alpha通道的模板（用于mask匹配）
            mask = None
            if template.shape[2] == 4:
                mask = template[:, :, 3]
                template = template[:, :, :3]

            if category not in self._templates:
                self._templates[category] = []

            self._templates[category].append({
                "name": name,
                "image": template,
                "mask": mask,
                "category": category,
            })
            count += 1

        self._loaded = True
        msg = f"已加载 {count} 个模板，分 {len(self._templates)} 个类别"
        if skipped:
            msg += f"（跳过 {skipped} 个已禁用）"
        logger.info(msg)

    def detect_all(self, screenshot: np.ndarray,
                   threshold: float = 0.8) -> list[DetectResult]:
        """在截图中检测所有已加载的模板"""
        if not self._loaded:
            self.load_templates()

        results = []
        gray_screen = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

        for category, templates in self._templates.items():
            for tpl in templates:
                matches = self._match_template(
                    screenshot, gray_screen, tpl, threshold
                )
                results.extend(matches)

        # 过滤掉置信度异常的结果（inf, nan, >1.0）
        results = [r for r in results
                   if not (r.confidence != r.confidence or  # nan 检查
                           r.confidence == float('inf') or
                           r.confidence == float('-inf') or
                           r.confidence > 1.0)]

        # 去重：按类别分组 NMS，防止同一位置被多个同类模板重复匹配
        results = self._nms_by_category(results, iou_threshold=0.3)
        # 按置信度排序
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def detect_category(self, screenshot: np.ndarray,
                        category: str,
                        threshold: float = 0.8) -> list[DetectResult]:
        """只检测指定类别的模板"""
        if not self._loaded:
            self.load_templates()

        results = []
        gray_screen = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

        templates = self._templates.get(category, [])
        for tpl in templates:
            matches = self._match_template(
                screenshot, gray_screen, tpl, threshold
            )
            results.extend(matches)

        # 过滤掉置信度异常的结果（inf, nan, >1.0）
        results = [r for r in results
                   if not (r.confidence != r.confidence or  # nan 检查
                           r.confidence == float('inf') or
                           r.confidence == float('-inf') or
                           r.confidence > 1.0)]

        results = self._nms(results, iou_threshold=0.5)
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def detect_single_template(self, screenshot: np.ndarray,
                                name: str,
                                threshold: float = 0.7) -> list[DetectResult]:
        """只检测指定名称的单个模板"""
        if not self._loaded:
            self.load_templates()

        gray_screen = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

        for category, templates in self._templates.items():
            for tpl in templates:
                if tpl["name"] == name:
                    results = self._match_template(
                        screenshot, gray_screen, tpl, threshold
                    )
                    # 过滤掉置信度异常的结果（inf, nan, >1.0）
                    results = [r for r in results
                               if not (r.confidence != r.confidence or  # nan 检查
                                       r.confidence == float('inf') or
                                       r.confidence == float('-inf') or
                                       r.confidence > 1.0)]
                    results = self._nms(results, iou_threshold=0.5)
                    results.sort(key=lambda r: r.confidence, reverse=True)
                    return results
        return []

    def detect_quick(self, screenshot: np.ndarray,
                     name: str,
                     threshold: float = 0.8) -> DetectResult | None:
        """极速检测：单模板 + 仅 scale 1.0，返回首个匹配或 None"""
        if not self._loaded:
            self.load_templates()

        tpl = self._find_template(name)
        if tpl is None:
            return None

        tpl_img = tpl["image"]
        tpl_mask = tpl["mask"]
        th, tw = tpl_img.shape[:2]
        sh, sw = screenshot.shape[:2]
        if tw >= sw or th >= sh:
            return None

        use_color = tpl["category"] == "land"

        if use_color:
            confidences = []
            for c in range(3):
                screen_ch = screenshot[:, :, c]
                tpl_ch = tpl_img[:, :, c]
                if tpl_mask is not None:
                    match_result = cv2.matchTemplate(screen_ch, tpl_ch, cv2.TM_CCOEFF_NORMED, mask=tpl_mask)
                else:
                    match_result = cv2.matchTemplate(screen_ch, tpl_ch, cv2.TM_CCOEFF_NORMED)
                confidences.append(match_result)
            match_result = np.mean(confidences, axis=0)
        else:
            gray_screen = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
            gray_tpl = cv2.cvtColor(tpl_img, cv2.COLOR_BGR2GRAY)
            if tpl_mask is not None:
                match_result = cv2.matchTemplate(gray_screen, gray_tpl, cv2.TM_CCOEFF_NORMED, mask=tpl_mask)
            else:
                match_result = cv2.matchTemplate(gray_screen, gray_tpl, cv2.TM_CCOEFF_NORMED)

        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(match_result)
        if max_val < threshold:
            return None

        return DetectResult(
            name=name,
            category=tpl["category"],
            x=max_loc[0] + tw // 2,
            y=max_loc[1] + th // 2,
            w=tw,
            h=th,
            confidence=float(max_val),
        )

    def detect_targeted(self, screenshot: np.ndarray,
                        names: list[str],
                        thresholds: dict[str, float] | None = None,
                        scales: list[float] | None = None) -> list[DetectResult]:
        """快速检测：只扫描指定模板名称，使用精简尺度集合"""
        if not self._loaded:
            self.load_templates()

        name_set = set(names)
        # 精简尺度：只试 3 个尺度而非 13 个
        fast_scales = scales or [1.0, 0.9, 1.1]

        results = []
        for category, templates in self._templates.items():
            for tpl in templates:
                if tpl["name"] not in name_set:
                    continue
                thresh = (thresholds.get(tpl["name"], 0.8)
                          if thresholds else self.get_template_threshold(tpl["name"]))
                results.extend(
                    self._match_template_with_scales(
                        screenshot, tpl, thresh, fast_scales
                    )
                )

        results = [r for r in results
                   if not (r.confidence != r.confidence or
                           r.confidence == float('inf') or
                           r.confidence > 1.0)]
        return self._nms_by_category(results, iou_threshold=0.3)

    def _find_template(self, name: str) -> dict | None:
        """按名称查找模板数据"""
        for templates in self._templates.values():
            for tpl in templates:
                if tpl["name"] == name:
                    return tpl
        return None

    def _match_template_with_scales(self, screenshot: np.ndarray,
                                     tpl: dict,
                                     threshold: float,
                                     scales: list[float]) -> list[DetectResult]:
        """使用指定尺度集合进行模板匹配"""
        results = []
        tpl_img = tpl["image"]
        tpl_mask = tpl["mask"]
        th, tw = tpl_img.shape[:2]
        sh, sw = screenshot.shape[:2]
        use_color = tpl["category"] == "land"

        gray_screen = None if use_color else cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

        for scale in scales:
            new_w = int(tw * scale)
            new_h = int(th * scale)
            if new_w >= sw or new_h >= sh or new_w < 10 or new_h < 10:
                continue

            resized_tpl = cv2.resize(tpl_img, (new_w, new_h))
            resized_mask = None
            if tpl_mask is not None:
                # mask 必须用最近邻插值，保持 0/255 二值
                resized_mask = cv2.resize(tpl_mask, (new_w, new_h),
                                          interpolation=cv2.INTER_NEAREST)

            if use_color:
                confidences = []
                for c in range(3):
                    screen_ch = screenshot[:, :, c]
                    tpl_ch = resized_tpl[:, :, c]
                    if resized_mask is not None:
                        mr = cv2.matchTemplate(screen_ch, tpl_ch, cv2.TM_CCOEFF_NORMED, mask=resized_mask)
                    else:
                        mr = cv2.matchTemplate(screen_ch, tpl_ch, cv2.TM_CCOEFF_NORMED)
                    confidences.append(mr)
                match_result = np.mean(confidences, axis=0)
            else:
                gray_tpl = cv2.cvtColor(resized_tpl, cv2.COLOR_BGR2GRAY)
                if resized_mask is not None:
                    match_result = cv2.matchTemplate(gray_screen, gray_tpl, cv2.TM_CCOEFF_NORMED, mask=resized_mask)
                else:
                    match_result = cv2.matchTemplate(gray_screen, gray_tpl, cv2.TM_CCOEFF_NORMED)

            locations = np.where(match_result >= threshold)
            for pt_y, pt_x in zip(*locations):
                confidence = float(match_result[pt_y, pt_x])
                results.append(DetectResult(
                    name=tpl["name"],
                    category=tpl["category"],
                    x=pt_x + new_w // 2,
                    y=pt_y + new_h // 2,
                    w=new_w,
                    h=new_h,
                    confidence=confidence,
                ))

            if scale == 1.0 and any(r.confidence > 0.95 for r in results):
                break

        return results

    def _match_template(self, screenshot: np.ndarray,
                        gray_screen: np.ndarray,
                        tpl: dict,
                        threshold: float) -> list[DetectResult]:
        """对单个模板执行多尺度匹配（优化版：灰度缓存 + 尺度减少 + 候选点限制）"""
        results = []
        tpl_img = tpl["image"]
        tpl_mask = tpl["mask"]
        th, tw = tpl_img.shape[:2]
        sh, sw = screenshot.shape[:2]

        # ✅ 优化1：灰度模板缓存（参考 qq-farm-copilot）
        # 加载时预转灰度，避免每次匹配都调用 cv2.cvtColor
        if tpl.get("gray") is None and tpl["category"] != "land":
            tpl["gray"] = cv2.cvtColor(tpl_img, cv2.COLOR_BGR2GRAY)
        
        tpl_gray = tpl.get("gray")

        # ✅ 优化2：减少尺度数量（从 13 个降至 5 个）
        # 参考 qq-farm-copilot 的尺度集合
        scales = [1.0, 0.9, 0.8, 1.1, 1.2]

        # ✅ 优化3：候选点上限（参考 qq-farm-copilot）
        max_hits = 64 if tpl["category"] == "land" else 8

        # land 类别使用彩色匹配（保留金色等颜色特征）
        use_color = tpl["category"] == "land"

        for scale in scales:
            new_w = int(tw * scale)
            new_h = int(th * scale)
            if new_w >= sw or new_h >= sh or new_w < 10 or new_h < 10:
                continue

            if use_color:
                # 彩色匹配：对 BGR 三通道分别匹配，取平均值
                resized_tpl = cv2.resize(tpl_img, (new_w, new_h))
                resized_mask = None
                if tpl_mask is not None:
                    resized_mask = cv2.resize(tpl_mask, (new_w, new_h),
                                              interpolation=cv2.INTER_NEAREST)
                
                confidences = []
                for c in range(3):
                    screen_ch = screenshot[:, :, c]
                    tpl_ch = resized_tpl[:, :, c]
                    if resized_mask is not None:
                        match_result = cv2.matchTemplate(screen_ch, tpl_ch, cv2.TM_CCOEFF_NORMED, mask=resized_mask)
                    else:
                        match_result = cv2.matchTemplate(screen_ch, tpl_ch, cv2.TM_CCOEFF_NORMED)
                    confidences.append(match_result)
                match_result = np.mean(confidences, axis=0)
            else:
                # ✅ 灰度匹配（使用缓存的灰度模板）
                resized_tpl_gray = cv2.resize(tpl_gray, (new_w, new_h))
                resized_mask = None
                if tpl_mask is not None:
                    resized_mask = cv2.resize(tpl_mask, (new_w, new_h),
                                              interpolation=cv2.INTER_NEAREST)
                
                if resized_mask is not None:
                    match_result = cv2.matchTemplate(
                        gray_screen, resized_tpl_gray, cv2.TM_CCOEFF_NORMED, mask=resized_mask
                    )
                else:
                    match_result = cv2.matchTemplate(
                        gray_screen, resized_tpl_gray, cv2.TM_CCOEFF_NORMED
                    )

            # ✅ 优化3：使用 np.argpartition 截取 top-N 候选点（避免全部保留）
            locations = np.where(match_result >= threshold)
            if locations[0].size > max_hits:
                # 只保留 top-N 最高置信度的候选点
                scores = match_result[locations]
                top_idx = np.argpartition(scores, -max_hits)[-max_hits:]
                pt_ys = locations[0][top_idx]
                pt_xs = locations[1][top_idx]
            else:
                pt_ys = locations[0]
                pt_xs = locations[1]

            for pt_y, pt_x in zip(pt_ys, pt_xs):
                confidence = float(match_result[pt_y, pt_x])
                center_x = pt_x + new_w // 2
                center_y = pt_y + new_h // 2

                results.append(DetectResult(
                    name=tpl["name"],
                    category=tpl["category"],
                    x=center_x,
                    y=center_y,
                    w=new_w,
                    h=new_h,
                    confidence=confidence,
                ))

            # 如果在原始尺度找到了高置信度匹配，跳过其他尺度
            if scale == 1.0 and any(r.confidence > 0.95 for r in results):
                break

        return results

    @staticmethod
    def _nms(results: list[DetectResult],
             iou_threshold: float = 0.5) -> list[DetectResult]:
        """非极大值抑制，去除重叠检测"""
        if len(results) <= 1:
            return results

        # 按置信度降序排列
        results.sort(key=lambda r: r.confidence, reverse=True)
        keep = []

        while results:
            best = results.pop(0)
            keep.append(best)
            remaining = []
            for r in results:
                if _iou(best.bbox, r.bbox) < iou_threshold:
                    remaining.append(r)
            results = remaining

        return keep

    def _nms_by_category(self, results: list[DetectResult],
                         iou_threshold: float = 0.3) -> list[DetectResult]:
        """按类别分组做 NMS，防止同一块地被多个同类模板重复匹配
        使用中心点距离去重，阈值 25px
        """
        by_cat: dict[str, list[DetectResult]] = {}
        for r in results:
            by_cat.setdefault(r.category, []).append(r)

        final = []
        for cat, cat_results in by_cat.items():
            cat_results.sort(key=lambda r: r.confidence, reverse=True)
            kept = []
            for r in cat_results:
                is_duplicate = False
                for k in kept:
                    dist = ((r.x - k.x) ** 2 + (r.y - k.y) ** 2) ** 0.5
                    if dist < 25:
                        is_duplicate = True
                        break
                if not is_duplicate:
                    kept.append(r)
            final.extend(kept)

        final.sort(key=lambda r: r.confidence, reverse=True)
        return final

    @staticmethod
    def pil_to_cv2(image: Image.Image) -> np.ndarray:
        """PIL Image 转 OpenCV 格式"""
        rgb = np.array(image.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def draw_results(self, screenshot: np.ndarray,
                     results: list[DetectResult]) -> np.ndarray:
        """在截图上绘制检测结果（用于调试）"""
        output = screenshot.copy()
        overlay = output.copy()
        colors = {
            "button": (0, 200, 255),      # 亮橙
            "status_icon": (0, 100, 255),  # 亮蓝
            "crop": (0, 255, 100),         # 亮绿
            "ui_element": (255, 255, 0),   # 青
            "land": (180, 180, 180),       # 浅灰
            "seed": (255, 50, 255),        # 粉紫
            "shop": (0, 200, 200),         # 黄绿
            "unknown": (0, 0, 255),        # 红色
        }
        for r in results:
            color = colors.get(r.category, (0, 0, 255))
            x1, y1, x2, y2 = r.bbox
            # 半透明填充
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            # 粗边框
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 3)
        # 混合半透明叠加
        cv2.addWeighted(overlay, 0.25, output, 0.75, 0, output)
        # 标签绘制在叠加后，保证清晰
        for r in results:
            color = colors.get(r.category, (0, 0, 255))
            x1, y1, x2, y2 = r.bbox
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 3)
            label = f"{r.confidence:.2f}"
            # 标签背景
            font = cv2.FONT_HERSHEY_SIMPLEX
            scale = max(0.5, min(output.shape[0] / 800, 1.0))
            thickness = max(1, int(scale * 1.5))
            (tw, th), _ = cv2.getTextSize(label, font, scale, thickness)
            ly = max(y1 - 6, th + 4)
            cv2.rectangle(output, (x1, ly - th - 4), (x1 + tw + 8, ly + 4), color, -1)
            cv2.putText(output, label, (x1 + 4, ly),
                        font, scale, (255, 255, 255), thickness, cv2.LINE_AA)
        return output


def _iou(box1: tuple, box2: tuple) -> float:
    """计算两个框的IoU"""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0

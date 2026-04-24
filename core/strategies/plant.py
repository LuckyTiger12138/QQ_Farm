"""P2 生产 — 播种 + 购买种子 + 施肥"""
import time
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import CVDetector, DetectResult
from core.scene_detector import Scene, identify_scene
from core.strategies.base import BaseStrategy
from utils.land_grid import get_lands_from_land_anchor

# 尝试导入 OCR 模块（可选依赖）
try:
    from utils.shop_item_ocr import ShopItemOCR
    HAS_OCR = True
except ImportError:
    HAS_OCR = False
    logger.warning("OCR 模块不可用，商店买种将使用模板匹配。安装 rapidocr_onnxruntime 可启用 OCR 识别。")

# 注意：旧的硬编码阈值已废弃，现在统一从配置文件读取。
# 如果需要为特定作物设置特殊阈值，请在 GUI 的“模板管理”中修改并保存。
# CROP_THRESHOLDS = { ... } 

class PlantStrategy(BaseStrategy):
    def __init__(self, cv_detector: CVDetector):
        super().__init__(cv_detector)
        self.auto_buy_seed = False  # 是否自动购买种子
        self.auto_fertilize = False  # 是否自动施肥
        self._purchase_count = 0  # 本轮播种购买次数
        self._max_purchase_per_round = 1  # 每轮最多购买次数
        
        # 翻页按钮位置校验：记录第一次检测到的真实翻页按钮坐标，用于后续防伪
        self._last_page_btn_pos = None

    def _check_and_close_info_page(self, rect: tuple, exclude: list[str] = None) -> bool:
        """检测并关闭干扰页面（个人信息/任务/宠物/图鉴/仓库），返回是否成功关闭
        
        Args:
            rect: 窗口矩形
            exclude: 不需要关闭的页面模板名称列表，例如 ["btn_cangku"]
        """
        if self.stopped:
            return False
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return False

        if exclude is None:
            exclude = []

        # 定义所有需要检测的干扰页面
        page_templates = [
            "btn_info", "btn_rw", "btn_chongwu", "btn_tujian", "btn_cangku", "btn_haoyou",
            "ui_shangcheng",
        ]
        # 过滤掉不需要检测的页面
        targets = [p for p in page_templates if p not in exclude]

        # 检测是否有干扰页面打开
        is_interfering = False
        for name in targets:
            if self.cv_detector.detect_single_template(
                cv_img, name, threshold=self.cv_detector.get_template_threshold(name)
            ):
                is_interfering = True
                break

        if is_interfering:
            # 优先检测商城返回按钮
            mall_back = self.cv_detector.detect_single_template(
                cv_img, "btn_shangcehng_fanhui", threshold=self.cv_detector.get_template_threshold("btn_shangcehng_fanhui"))
            if mall_back:
                self.click(mall_back[0].x, mall_back[0].y, "关闭商城")
                for _ in range(3):
                    if self.stopped:
                        return False
                    time.sleep(0.1)
                return True

            # 通用关闭按钮
            close_btn = self.cv_detector.detect_single_template(
                cv_img, "btn_close", threshold=self.cv_detector.get_template_threshold("btn_close"))
            close_btn = self.cv_detector.detect_single_template(
                cv_img, "btn_close", threshold=self.cv_detector.get_template_threshold("btn_close"))
            if not close_btn:
                close_btn = self.cv_detector.detect_single_template(
                    cv_img, "btn_info_close", threshold=self.cv_detector.get_template_threshold("btn_info_close"))
            if close_btn:
                self.click(close_btn[0].x, close_btn[0].y, "关闭当前页面")
                for _ in range(3):
                    if self.stopped:
                        return False
                    time.sleep(0.1)
                return True

            # 没找到关闭按钮，点击空白处
            self.click_blank(rect)
            for _ in range(3):
                if self.stopped:
                    return False
                time.sleep(0.1)
            return True

        return False
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return False

        # 检测个人信息页面、任务菜单、宠物页、图鉴页特征
        btn_info = self.cv_detector.detect_single_template(
            cv_img, "btn_info", threshold=self.cv_detector.get_template_threshold("btn_info"))
        btn_rw = self.cv_detector.detect_single_template(
            cv_img, "btn_rw", threshold=self.cv_detector.get_template_threshold("btn_rw"))
        btn_chongwu = self.cv_detector.detect_single_template(
            cv_img, "btn_chongwu", threshold=self.cv_detector.get_template_threshold("btn_chongwu"))
        btn_tujian = self.cv_detector.detect_single_template(
            cv_img, "btn_tujian", threshold=self.cv_detector.get_template_threshold("btn_tujian"))

        if btn_info or btn_rw or btn_chongwu or btn_tujian:
            # 确认是上述页面之一，再找关闭按钮
            close_btn = self.cv_detector.detect_single_template(
                cv_img, "btn_close", threshold=self.cv_detector.get_template_threshold("btn_close"))
            if not close_btn:
                close_btn = self.cv_detector.detect_single_template(
                    cv_img, "btn_info_close", threshold=self.cv_detector.get_template_threshold("btn_info_close"))
            if close_btn:
                self.click(close_btn[0].x, close_btn[0].y, "关闭个人信息页面")
                for _ in range(3):
                    if self.stopped:
                        return False
                    time.sleep(0.1)
                return True

            # 有上述页面但没找到关闭按钮，点击空白处
            self.click_blank(rect)
            for _ in range(3):
                if self.stopped:
                    return False
                time.sleep(0.1)
            return True

        return False

    def _is_seed_panel_open(self, cv_img) -> bool:
        """检测种子选择面板是否已打开（通过检测翻页按钮）

        参考 qq-farm-copilot 的实现：使用 BTN_SEED_SELECT_POPUP_RIGHT 按钮
        该按钮位于种子选择面板右下角，坐标约 (511, 608, 524, 636)

        Returns:
            bool: True 表示面板已打开且还有下一页，False 表示面板未打开或已到末页
        """
        # 修复：使用配置的阈值，禁止硬编码
        page_threshold = self.cv_detector.get_template_threshold("btn_seed_select_right")
        page_btn = self.cv_detector.detect_single_template(
            cv_img, "btn_seed_select_right", threshold=page_threshold)
        return page_btn is not None

    def _find_seed_with_pagination(self, cv_img, crop_name: str, rect: tuple,
                                    max_attempts: int = 10) -> DetectResult | None:
        """动态翻页查找种子

        参考 qq-farm-copilot 的实现：
        - 不固定翻页次数，而是检测翻页按钮是否存在
        - 每次翻页后重新截屏检测
        - 翻页按钮消失 = 到达末页

        Args:
            cv_img: 当前截图
            crop_name: 作物名称
            rect: 窗口矩形
            max_attempts: 最大翻页次数（安全上限）

        Returns:
            DetectResult | None: 找到的种子检测结果，或 None
        """
        seed_threshold = self.cv_detector.get_template_threshold(f"seed_{crop_name}")
        current_img = cv_img

        for attempt in range(max_attempts):
            if self.stopped:
                return None

            # 检测目标种子
            seed_dets = self.cv_detector.detect_single_template(
                current_img, f"seed_{crop_name}", threshold=seed_threshold)

            if seed_dets:
                logger.info(f"播种流程：找到种子 '{crop_name}' (第{attempt + 1}页, 置信度: {seed_dets[0].confidence:.0%})")
                return seed_dets[0]

            # 未找到种子，检测翻页按钮
            # 修复：必须使用配置的阈值，而不是硬编码的 0.85
            page_threshold = self.cv_detector.get_template_threshold("btn_seed_select_right")
            page_btns = self.cv_detector.detect_single_template(
                current_img, "btn_seed_select_right", threshold=page_threshold)

            # 过滤：翻页按钮必须在屏幕右侧（防止误识别左侧列表图标）
            # 参考项目坐标 X=511，窗口宽 581，占比约 88%。这里我们放宽到 60%
            h_img, w_img = current_img.shape[:2]
            min_x = int(w_img * 0.6)
            
            valid_btns = []
            if page_btns:
                valid_btns = [btn for btn in page_btns if btn.x >= min_x]
                # 如果有多个，选置信度最高的
                if valid_btns:
                    page_btn = max(valid_btns, key=lambda b: b.confidence)
                    logger.debug(f"检测到翻页按钮 ({page_btn.x}, {page_btn.y}) 在有效区域 (>{min_x})")
            
            if valid_btns:
                # 还有下一页，点击翻页
                logger.info(f"播种流程：当前页未找到种子，点击翻页按钮 ({attempt + 1}/{max_attempts})")
                self.click(page_btn.x, page_btn.y, f"翻页查找种子")
                time.sleep(0.3)  # 等待翻页动画

                # 重新截屏
                current_img, _, _ = self.capture(rect)
                if current_img is None:
                    return None
            else:
                # 翻页按钮消失 = 到达末页或面板已关闭
                logger.warning("播种流程：未找到种子且未检测到翻页按钮（请检查是否存在 btn_seed_select_right.png 模板），视为到达末页")
                break

        return None

    def _swipe_seed_list(self, rect: tuple):
        """滑动种子列表以加载更多种子（翻页）- 已废弃，保留兼容

        @deprecated 使用 _find_seed_with_pagination 替代
        仅在模板 btn_seed_select_right 未采集时作为降级方案使用
        """
        if not self.action_executor:
            return

        logger.warning("播种流程：使用固定坐标滑动（降级方案），建议采集 btn_seed_select_right 模板")
        # 在窗口可见范围内滑动（缩短距离，避免超出窗口）
        sx = self.action_executor._window_left + self.action_executor._client_offset_x + 270
        sy = self.action_executor._window_top + self.action_executor._client_offset_y + 350
        ex = self.action_executor._window_left + self.action_executor._client_offset_x + 270
        ey = self.action_executor._window_top + self.action_executor._client_offset_y + 550
        dx, dy = ex - sx, ey - sy

        logger.debug(f"播种流程：滑动种子列表 屏幕起点=({sx}, {sy}), 终点=({ex}, {ey}), 偏移=({dx}, {dy})")
        self.action_executor.drag(sx, sy, dx, dy, duration=0.3, steps=6)

    def _plant_remaining_lands(self, rect: tuple, lands: list, crop_name: str,
                                total_lands: int = 0, skip_count: int = 0) -> list[str]:
        """播种剩余的空地（跳过第一块已验证不是空地的地块）"""
        if not lands or self.stopped:
            return []
        all_actions = []

        # 点击前先检测并关闭个人信息页面
        self._check_and_close_info_page(rect)
        if self.stopped:
            return all_actions

        # 每块地操作前先检查停止和一键收获按钮
        cv_img, dets, _ = self.capture(rect)
        if cv_img is not None:
            # 优先检查停止
            if self.stopped:
                return all_actions
            # 检查一键收获，优先收获
            harvest_btn = self.find_by_name(dets, "btn_harvest")
            if harvest_btn:
                logger.info("播种流程：检测到一键收获按钮，中断播种优先收获")
                return all_actions

        # 计算当前是第几块地
        current_num = skip_count + 1
        # 点击第一块剩余的空地
        self.click(lands[0].x, lands[0].y, f"点击空地 ({current_num}/{total_lands or len(lands)})")
        for _ in range(10):
            if self.stopped:
                return all_actions
            time.sleep(0.05)

        # 检测是否已播种（通过施肥按钮）
        cv_img, dets, _ = self.capture(rect)
        if cv_img is not None and self._is_already_planted(cv_img):
            logger.info(f"播种流程：检测到施肥按钮，这块地已播种，跳过")
            self.click_blank(rect)
            for _ in range(10):
                if self.stopped:
                    return all_actions
                time.sleep(0.05)
            # 从剩余的空地中继续播种（排除第一块）
            if len(lands) > 1:
                if self.stopped:
                    return all_actions
                return self._plant_remaining_lands(rect, lands[1:], crop_name, total_lands, skip_count + 1)
            return all_actions

        # 查找种子（使用动态翻页）
        cv_img, dets, _ = self.capture(rect)
        seed_det = self._find_seed_with_pagination(cv_img, crop_name, rect, max_attempts=10)

        if not seed_det:
            # 还是没有种子，这块地也可能不是空地
            logger.info(f"剩余地块中仍未找到种子，跳过 {lands[0]}")
            self.click_blank(rect)
            for _ in range(10):
                if self.stopped:
                    return all_actions
                time.sleep(0.05)
            if len(lands) > 1:
                if self.stopped:
                    return all_actions
                return self._plant_remaining_lands(rect, lands[1:], crop_name, total_lands, skip_count + 1)
            return all_actions

        # 找到种子，按住拖拽到所有剩余空地
        logger.info(f"播种流程：找到种子 '{crop_name}'，拖拽播种 {len(lands)} 块地")
        if not self.action_executor:
            return all_actions

        seed_abs_x, seed_abs_y = self.action_executor.relative_to_absolute(
            seed_det.x, seed_det.y)
        land_points = [
            self.action_executor.relative_to_absolute(land.x, land.y)
            for land in lands
        ]
        total_count = len(lands)
        done = self.action_executor.drag_multi_points(
            seed_abs_x, seed_abs_y, land_points,
            check_stopped=lambda: self.stopped)
        planted_count = total_count if done else 0
        if not done:
            logger.info("播种流程：拖拽中途停止")
            return all_actions
        logger.info(f"播种流程：拖拽播种完成，共 {planted_count} 块")
        all_actions.append(f"播种{crop_name}×{planted_count}")

        # 验证：检查是否弹出商店
        time.sleep(0.5)
        cv_check, _, _ = self.capture(rect)
        if cv_check is not None:
            shop_close = self.cv_detector.detect_single_template(
                cv_check, "btn_shop_close", threshold=self.cv_detector.get_template_threshold("btn_shop_close"))
            if shop_close:
                self._close_shop_and_buy(rect, crop_name, all_actions)

        return all_actions

    def _is_already_planted(self, cv_img) -> bool:
        """检查地块是否已播种（通过检测施肥按钮）"""
        # 检测施肥按钮，如果存在说明这块地已经播种了
        fertilize_templates = ["bth_feiliao_pt", "bth_feiliao2_yj", "btn_fertilize_popup"]
        for tpl_name in fertilize_templates:
            result = self.cv_detector.detect_single_template(cv_img, tpl_name, threshold=self.cv_detector.get_template_threshold(tpl_name))
            if result:
                # 过滤掉置信度异常的结果
                conf = result[0].confidence
                if conf != conf or conf == float('inf') or conf == float('-inf') or conf > 1.0:
                    continue  # 跳过异常值，尝试下一个模板
                logger.debug(f"检测到施肥按钮：{tpl_name} (置信度：{conf:.0%})")
                return True
        return False

    def plant_all(self, rect: tuple, crop_name: str, auto_fertilize: bool = False) -> list[str]:
        """快速播种所有空地：点击空地弹出种子列表 → 按住种子拖拽到所有空地

        Args:
            rect: 窗口区域
            crop_name: 作物名称
            auto_fertilize: 是否自动施肥

        Returns:
            操作列表，如果施肥则包含施肥操作
        """
        # 重置购买计数器（新一轮播种）
        self._purchase_count = 0

        all_actions = []

        if self.stopped:
            return all_actions

        # 第一步：截屏找所有空地
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return all_actions
        # 只选择真正的空地（所有 land_ 前缀的模板）
        lands = [d for d in dets if d.name.startswith("land_")]
        lands.sort(key=lambda d: d.confidence, reverse=True)  # 按置信度排序
        if not lands:
            return all_actions
        total_lands = len(lands)  # 保存总数用于进度显示
        logger.info(f"找到 {len(lands)} 块空地，最高置信度：{lands[0].confidence:.0%}")

        # 播种前检测并关闭干扰页面（排除仓库页，避免误关）
        self._check_and_close_info_page(rect, exclude=["btn_cangku"])
        if self.stopped:
            return all_actions

        # 第二步：点击第一块空地，弹出种子列表
        self.click(lands[0].x, lands[0].y, f"点击空地 ({1}/{total_lands})")
        for _ in range(5):
            if self.stopped:
                return all_actions
            time.sleep(0.05)

        # 第三步：检测是否已播种（通过施肥按钮）
        cv_img, dets, _ = self.capture(rect)
        if cv_img is not None and self._is_already_planted(cv_img):
            logger.info(f"播种流程：检测到施肥按钮，这块地已播种，跳过")
            self.click_blank(rect)
            for _ in range(5):
                if self.stopped:
                    return all_actions
                time.sleep(0.05)
            # 从剩余的空地中继续播种（排除第一块）
            if len(lands) > 1:
                if self.stopped:
                    return all_actions
                return self._plant_remaining_lands(rect, lands[1:], crop_name, total_lands, 1)
            return all_actions

        # 第四步：找到目标种子（使用动态翻页）
        logger.info(f"播种流程：开始查找种子 '{crop_name}'")
        seed_det = self._find_seed_with_pagination(cv_img, crop_name, rect, max_attempts=10)

        if not seed_det:
            # 没找到种子，先关闭种子弹窗
            logger.info(f"播种流程：未找到 '{crop_name}' 种子，关闭弹窗...")
            self.click_blank(rect)
            for _ in range(10):
                if self.stopped:
                    return all_actions
                time.sleep(0.05)
            if self.stopped:
                return all_actions

            # 只有开启自动买种时才检查仓库
            if self.auto_buy_seed:
                # 安全策略：检查是否超过最大购买次数
                if self._purchase_count >= self._max_purchase_per_round:
                    logger.warning(f"播种流程：已达到最大购买次数 ({self._max_purchase_per_round})，停止购买")
                    return all_actions

                warehouse_result = self.check_warehouse_seeds(rect, crop_name)
                if warehouse_result["has_seed"]:
                    # 仓库有种子但弹窗中没有，说明这块地不是真正的空地（已播种/成熟/杂草）
                    # 重新点击空地打开弹窗
                    logger.info(f"仓库有种子，重新点击空地打开弹窗")
                    self.click(lands[0].x, lands[0].y, f"点击空地 ({1}/{total_lands})")
                    for _ in range(5):
                        if self.stopped:
                            return all_actions
                        time.sleep(0.05)
                else:
                    logger.info(f"仓库中没有 '{crop_name}' 种子，去商店购买 (第{self._purchase_count + 1}次)")
                    buy_result = self._buy_seeds(rect, crop_name, skip_warehouse_check=True)
                    if buy_result:
                        self._purchase_count += 1  # 增加购买计数
                        all_actions.append(buy_result)
                        # 买完后重新尝试播种
                        logger.info(f"播种流程：购买完成，重新尝试播种 (已购买{self._purchase_count}次)")
                        return all_actions + self.plant_all(rect, crop_name)
            else:
                logger.info("自动买种未开启，跳过种植")
            return all_actions

        # 第四步：按住种子，拖拽到每块空地
        logger.info(f"播种流程：找到种子 '{crop_name}'，开始拖拽播种 {len(lands)} 块空地")
        if not self.action_executor:
            return all_actions

        seed_abs_x, seed_abs_y = self.action_executor.relative_to_absolute(
            seed_det.x, seed_det.y)
        land_points = [
            self.action_executor.relative_to_absolute(land.x, land.y)
            for land in lands
        ]
        total_count = len(lands)
        done = self.action_executor.drag_multi_points(
            seed_abs_x, seed_abs_y, land_points,
            check_stopped=lambda: self.stopped)
        planted_count = total_count if done else 0
        if not done:
            logger.info("播种流程：拖拽中途停止")
            return all_actions
        logger.info(f"播种流程：拖拽播种完成，共 {planted_count} 块")
        all_actions.append(f"播种{crop_name}×{planted_count}")
        logger.info(f"播种流程：准备验证弹窗，planted_count={planted_count}")

        # 验证：检查是否弹出商店（种子用完）或施肥弹窗
        for _ in range(10):
            if self.stopped:
                return all_actions
            time.sleep(0.05)
        cv_check, _, _ = self.capture(rect)
        if cv_check is not None:
            shop_close = self.cv_detector.detect_single_template(
                cv_check, "btn_shop_close", threshold=self.cv_detector.get_template_threshold("btn_shop_close"))
            if shop_close:
                logger.info("播种流程：种子用完，进入购买流程")
                self._close_shop_and_buy(rect, crop_name, all_actions)
                return all_actions

            fert = self.cv_detector.detect_single_template(
                cv_check, "btn_fertilize_popup", threshold=self.cv_detector.get_template_threshold("btn_fertilize_popup"))
            if fert:
                logger.info("播种流程：检测到施肥弹窗，关闭")
                w, h = rect[2], rect[3]
                self.click(w // 2, int(h * 0.15), "关闭施肥弹窗")
                time.sleep(0.5)  # 等待点击后页面恢复
                # 验证是否成功关闭，检查是否误开个人信息页面
                cv_check2, dets2, _ = self.capture(rect)
                if cv_check2 is not None:
                    info_close = self.cv_detector.detect_single_template(
                        cv_check2, "btn_info_close", threshold=self.cv_detector.get_template_threshold("btn_info_close"))
                    if info_close:
                        logger.info("播种流程：误开个人信息页面，关闭")
                        self.click(info_close[0].x, info_close[0].y, "关闭个人信息页面")
                        time.sleep(0.3)

        logger.info(f"播种流程：验证完成，准备检查施肥")
        # 播种完成后，如果开启了自动施肥，立即对所有土地施肥
        logger.info(f"播种完成检查施肥：auto_fertilize={auto_fertilize}, self.auto_fertilize={self.auto_fertilize}, planted_count={planted_count}")
        if auto_fertilize and self.auto_fertilize and planted_count > 0:
            logger.info("播种完成，开始对所有土地施肥...")
            # 传入 is_test=True 让它检测所有土地并施肥
            fert_actions = self.fertilize_all(rect, lands=None, is_test=True)
            if fert_actions:
                all_actions.extend(fert_actions)
            else:
                logger.info("施肥流程未执行任何操作")
        else:
            logger.info("施肥条件不满足，跳过施肥")

        return all_actions

    def _plant_one(self, rect: tuple, land_det: DetectResult,
                   crop_name: str) -> list[str]:
        """播种单块空地"""
        actions_done = []
        self.click(land_det.x, land_det.y, "点击空地")

        for attempt in range(2):
            if self.stopped:
                return actions_done
            for _ in range(5):
                if self.stopped:
                    return actions_done
                time.sleep(0.05)

            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return actions_done

            seed_dets = self.cv_detector.detect_single_template(
                cv_img, f"seed_{crop_name}", threshold=self.cv_detector.get_template_threshold(f"seed_{crop_name}"))

            if seed_dets:
                seed = seed_dets[0]
                logger.info(f"播种流程：找到种子 '{crop_name}' ({seed.confidence:.0%})")
                self.click(seed.x, seed.y, f"播种{crop_name}", ActionType.PLANT)

                # 验证
                for _ in range(10):
                    if self.stopped:
                        return actions_done
                    time.sleep(0.05)
                cv_check, _, _ = self.capture(rect)
                if cv_check is not None:
                    shop_close = self.cv_detector.detect_single_template(
                        cv_check, "btn_shop_close", threshold=self.cv_detector.get_template_threshold("btn_shop_close"))
                    if shop_close:
                        logger.info("播种流程：种子已用完，进入购买流程")
                        self._close_shop_and_buy(rect, crop_name, actions_done)
                        return actions_done

                    fert = self.cv_detector.detect_single_template(
                        cv_check, "btn_fertilize_popup", threshold=self.cv_detector.get_template_threshold("btn_fertilize_popup"))
                    if fert:
                        w, h = rect[2], rect[3]
                        self.click(w // 2, int(h * 0.15), "关闭施肥弹窗")

                logger.info(f"播种流程：播种 '{crop_name}' 成功")
                actions_done.append(f"播种{crop_name}")
                return actions_done

            scene = identify_scene(dets, self.cv_detector, cv_img)
            logger.debug(f"播种流程：等待种子弹窗 ({attempt+1}/2) 场景={scene.value}")

            if scene == Scene.POPUP:
                from core.strategies.popup import PopupStrategy
                ps = PopupStrategy(self.cv_detector)
                ps.action_executor = self.action_executor
                ps.handle_popup(dets)
                continue

            if scene == Scene.SHOP_PAGE:
                logger.info("播种流程：检测到商店页面，种子已用完")
                self._close_shop_and_buy(rect, crop_name, actions_done)
                return actions_done

        else:
            logger.info(f"播种流程：未找到 '{crop_name}' 种子，去商店购买")
            self.click_blank(rect)
            for _ in range(6):
                if self.stopped:
                    return actions_done
                time.sleep(0.05)

        # 去商店买
        buy_result = self._buy_seeds(rect, crop_name)
        if buy_result:
            actions_done.append(buy_result)
            self._retry_plant_after_buy(rect, crop_name, actions_done)
        return actions_done


    def _close_shop_and_buy(self, rect, crop_name, actions_done):
        """关闭自动弹出的商店，再手动购买"""
        if self.stopped:
            return
        from core.strategies.popup import PopupStrategy
        ps = PopupStrategy(self.cv_detector)
        ps.action_executor = self.action_executor
        ps.set_capture_fn(self._capture_fn)
        ps.close_shop(rect)
        buy_result = self._buy_seeds(rect, crop_name)
        if buy_result:
            actions_done.append(buy_result)


    def check_warehouse_seeds(self, rect: tuple, crop_name: str) -> dict:
        """检查仓库中指定种子的数量

        流程：点击仓库按钮 → 点击种子页签 → 查找对应种子 → 获取数量
        返回：{"has_seed": bool, "quantity": int, "position": (x, y)}
        """
        if self.stopped:
            return {"has_seed": False, "quantity": 0, "position": None}

        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return {"has_seed": False, "quantity": 0, "position": None}

        # 点击仓库按钮
        warehouse_btn = self.find_by_name(dets, "btn_warehouse")
        if not warehouse_btn:
            logger.warning("检查仓库：未找到仓库按钮")
            return {"has_seed": False, "quantity": 0, "position": None}

        self.click(warehouse_btn.x, warehouse_btn.y, "打开仓库")
        for _ in range(5):
            if self.stopped:
                return {"has_seed": False, "quantity": 0, "position": None}
            time.sleep(0.05)

        # 查找种子页签并点击
        for attempt in range(3):
            if self.stopped:
                logger.info("检查仓库：收到停止信号，取消")
                self._close_warehouse(rect)
                return {"has_seed": False, "quantity": 0, "position": None}
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                self._close_warehouse(rect)
                return {"has_seed": False, "quantity": 0, "position": None}

            zhongzi_btn = self.find_by_name(dets, "btn_zhongzi")
            if zhongzi_btn:
                self.click(zhongzi_btn.x, zhongzi_btn.y, "切换到种子页签")
                for _ in range(5):
                    if self.stopped:
                        self._close_warehouse(rect)
                        return {"has_seed": False, "quantity": 0, "position": None}
                    time.sleep(0.05)
                break
            for _ in range(3):
                if self.stopped:
                    self._close_warehouse(rect)
                    return {"has_seed": False, "quantity": 0, "position": None}
                time.sleep(0.05)
        else:
            logger.warning("检查仓库：未找到种子页签")
            self._close_warehouse(rect)
            return {"has_seed": False, "quantity": 0, "position": None}

        # 在种子页签中查找目标种子（带滑动）
        max_swipe_attempts = 5
        swipe_count = 0

        while swipe_count <= max_swipe_attempts:
            if self.stopped:
                logger.info("检查仓库：收到停止信号，取消")
                self._close_warehouse(rect)
                return {"has_seed": False, "quantity": 0, "position": None}
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                self._close_warehouse(rect)
                return {"has_seed": False, "quantity": 0, "position": None}

            # 查找 ws_作物名 模板（使用配置的原始阈值）
            base_threshold = self.cv_detector.get_template_threshold(f"ws_{crop_name}")
            seed_det = self.cv_detector.detect_single_template(
                cv_img, f"ws_{crop_name}", threshold=base_threshold)

            if seed_det:
                conf = min(seed_det[0].confidence, 1.0)  # 限制最大值用于显示
                logger.info(f"仓库中找到种子：{crop_name} (置信度：{conf:.0%})")
                self._close_warehouse(rect)
                return {
                    "has_seed": True,
                    "quantity": -1,
                    "position": (seed_det[0].x, seed_det[0].y)
                }
            else:
                # 未找到，尝试滑动列表（方向与商店相反：从下往上滑）
                if swipe_count < max_swipe_attempts:
                    logger.info(f"检查仓库：当前页未找到种子，滑动列表 ({swipe_count + 1}/{max_swipe_attempts})")
                    if self.action_executor:
                        # 仓库滑动方向：从 Y=550 滑到 Y=350（从下往上）
                        sx = self.action_executor._window_left + self.action_executor._client_offset_x + 270
                        sy = self.action_executor._window_top + self.action_executor._client_offset_y + 550
                        ex = self.action_executor._window_left + self.action_executor._client_offset_x + 270
                        ey = self.action_executor._window_top + self.action_executor._client_offset_y + 350
                        dx, dy = ex - sx, ey - sy
                        logger.debug(f"检查仓库：执行滑动 屏幕起点=({sx}, {sy}), 终点=({ex}, {ey}), 偏移=({dx}, {dy})")
                        drag_result = self.action_executor.drag(sx, sy, dx, dy, duration=0.3, steps=6)
                        if not drag_result:
                            logger.warning("检查仓库：滑动失败！")
                    else:
                        logger.warning("检查仓库：action_executor 未初始化，无法滑动")
                    time.sleep(0.8)
                    swipe_count += 1
                else:
                    logger.warning(f"检查仓库：滑动 {max_swipe_attempts} 次后仍未找到 '{crop_name}'")
                    self._close_warehouse(rect)
                    return {"has_seed": False, "quantity": 0, "position": None}

        self._close_warehouse(rect)
        return {"has_seed": False, "quantity": 0, "position": None}

    def _close_warehouse(self, rect: tuple):
        """关闭仓库页面：通过识别关闭按钮点击"""
        if self.stopped:
            return
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return
        # 查找关闭按钮
        close_btn = self.find_any(dets, ["btn_shop_close", "btn_rw_close", "btn_info_close", "btn_close"])
        if close_btn:
            self.click(close_btn.x, close_btn.y, "关闭仓库")
        else:
            logger.warning("关闭仓库：未找到关闭按钮")
        # 增加停止检查频率
        for _ in range(10):
            if self.stopped:
                return
            time.sleep(0.05)

    def _retry_plant_after_buy(self, rect, crop_name, actions_done):
        """购买完成后重新点空地播种"""
        if self.stopped:
            return
        for _ in range(6):
            if self.stopped:
                return
            time.sleep(0.05)
        if self.stopped:
            return
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return
        # 只选择真正的空地（所有 land_ 前缀的模板）
        lands = [d for d in dets if d.name.startswith("land_")]
        if not lands:
            return
        # 按置信度排序，选择最可靠的空地
        lands.sort(key=lambda d: d.confidence, reverse=True)
        land = lands[0]
        logger.info(f"播种流程：购买完成，重新点击空地 (置信度：{land.confidence:.0%})")
        self.click(land.x, land.y, "点击空地")
        for _ in range(10):
            if self.stopped:
                return
            time.sleep(0.05)
        cv_img2, _, _ = self.capture(rect)
        if cv_img2 is None:
            return
        seed_dets = self.cv_detector.detect_single_template(
            cv_img2, f"seed_{crop_name}", threshold=min(self.cv_detector.get_template_threshold(f"seed_{crop_name}") * 1.05, 1.0))  # 购买后阈值提高 5%
        if seed_dets:
            self.click(seed_dets[0].x, seed_dets[0].y,
                       f"播种{crop_name}", ActionType.PLANT)
            actions_done.append(f"播种{crop_name}")

    def _buy_seeds(self, rect: tuple, crop_name: str, skip_warehouse_check: bool = False) -> str | None:
        """购买种子流程：打开商店 → OCR/模板匹配找种子 → 点击 → 确认购买

        Args:
            rect: 窗口矩形
            crop_name: 作物名称
            skip_warehouse_check: 是否跳过仓库检查（默认False）。
                如果调用方（如 plant_all）已经检查过仓库，可设为 True 避免重复检查。

        优先使用 OCR 识别（如果可用），否则回退到模板匹配
        """
        if self.stopped:
            return None

        # 安全策略：购买前检查仓库（除非调用方已检查过）
        if not skip_warehouse_check:
            warehouse_result = self.check_warehouse_seeds(rect, crop_name)
            if warehouse_result["has_seed"]:
                logger.info(f"购买流程：仓库已有 '{crop_name}' 种子，跳过购买")
                return None

        logger.info("购买流程：打开商店")

        # 打开商店前先检测并关闭个人信息页面
        self._check_and_close_info_page(rect)

        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return None

        shop_btn = self.find_by_name(dets, "btn_shop")
        if not shop_btn:
            logger.warning("购买流程：未找到商店按钮")
            return None
        self.click(shop_btn.x, shop_btn.y, "打开商店")
        for _ in range(20):
            if self.stopped:
                return None
            time.sleep(0.05)

        # 等待商店打开
        for attempt in range(5):
            if self.stopped:
                return None
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return None

            shop_close = self.cv_detector.detect_single_template(
                cv_img, "btn_shop_close", threshold=self.cv_detector.get_template_threshold("btn_shop_close"))
            if not shop_close:
                logger.info(f"购买流程：等待商店加载 ({attempt+1}/5)")
                for _ in range(10):
                    if self.stopped:
                        return None
                    time.sleep(0.05)
                continue

            logger.info("购买流程：商店已打开，查找种子")
            
            # ✅ 尝试使用 OCR 识别（如果可用）
            if HAS_OCR:
                result = self._buy_seeds_with_ocr(rect, crop_name)
                if result is not None:
                    return result
                # OCR 失败，回退到模板匹配
                logger.info("购买流程：OCR 识别失败，回退到模板匹配")
            
            # ✅ 模板匹配方式（回退方案）
            result = self._buy_seeds_with_template(rect, crop_name)
            return result
        else:
            logger.warning("购买流程：商店加载超时")
            self._close_shop(rect)
            return None

    def _buy_seeds_with_ocr(self, rect: tuple, crop_name: str) -> str | None:
        """使用 OCR 识别商店种子并购买"""
        try:
            shop_ocr = ShopItemOCR()
        except Exception as e:
            logger.warning(f"购买流程：OCR 初始化失败: {e}")
            return None
        
        max_swipe_attempts = 5
        swipe_count = 0
        
        while swipe_count <= max_swipe_attempts:
            if self.stopped:
                self._close_shop(rect)
                return None

            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                time.sleep(0.3)
                swipe_count += 1
                continue

            # 使用 OCR 查找目标作物
            match = shop_ocr.find_item(cv_img, crop_name, min_similarity=0.70)
            
            if match.target:
                # 找到了
                logger.info(f"购买流程：OCR 找到 '{crop_name}' (中心点: {match.target.center_x}, {match.target.center_y})")
                if self.stopped:
                    logger.info("购买流程：收到停止信号，取消购买")
                    self._close_shop(rect)
                    return None
                    
                self.click(match.target.center_x, match.target.center_y, f"选择{crop_name}")
                for _ in range(20):
                    if self.stopped:
                        logger.info("购买流程：等待弹窗时收到停止信号，取消")
                        self._close_shop(rect)
                        return None
                    time.sleep(0.05)
                return self._confirm_purchase(rect, crop_name)
            else:
                # 未找到，尝试滑动列表
                if swipe_count < max_swipe_attempts:
                    logger.info(f"购买流程：OCR 当前页未找到种子，滑动列表 ({swipe_count + 1}/{max_swipe_attempts})")
                    if self.action_executor:
                        # 在窗口可见范围内滑动（缩短距离，避免超出窗口）
                        # 从 Y=350 滑到 Y=550，距离 200 像素
                        sx = self.action_executor._window_left + self.action_executor._client_offset_x + 270
                        sy = self.action_executor._window_top + self.action_executor._client_offset_y + 350
                        ex = self.action_executor._window_left + self.action_executor._client_offset_x + 270
                        ey = self.action_executor._window_top + self.action_executor._client_offset_y + 550
                        dx, dy = ex - sx, ey - sy
                        logger.debug(f"购买流程：执行滑动 屏幕起点=({sx}, {sy}), 终点=({ex}, {ey}), 偏移=({dx}, {dy})")
                        drag_result = self.action_executor.drag(sx, sy, dx, dy, duration=0.3, steps=6)
                        if not drag_result:
                            logger.warning("购买流程：滑动失败！")
                    else:
                        logger.warning("购买流程：action_executor 未初始化，无法滑动")
                    time.sleep(0.8)
                    swipe_count += 1
                else:
                    logger.warning(f"购买流程：OCR 滑动 {max_swipe_attempts} 次后仍未找到 '{crop_name}'")
                    self._close_shop(rect)
                    return None
        
        self._close_shop(rect)
        return None

    def _buy_seeds_with_template(self, rect: tuple, crop_name: str) -> str | None:
        """使用模板匹配查找商店种子并购买（回退方案）"""
        max_swipe_attempts = 5
        swipe_count = 0
        
        while swipe_count <= max_swipe_attempts:
            if self.stopped:
                self._close_shop(rect)
                return None

            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                time.sleep(0.3)
                swipe_count += 1
                continue

            shop_close = self.cv_detector.detect_single_template(
                cv_img, "btn_shop_close", threshold=self.cv_detector.get_template_threshold("btn_shop_close"))
            if not shop_close:
                logger.warning(f"购买流程：商店关闭按钮消失，可能已意外关闭")
                self._close_shop(rect)
                return None

            # 尝试匹配种子模板
            seed_dets = self.cv_detector.detect_single_template(
                cv_img, f"shop_{crop_name}", threshold=self.cv_detector.get_template_threshold(f"shop_{crop_name}"))

            if seed_dets:
                det = seed_dets[0]
                logger.info(f"购买流程：模板匹配找到 '{crop_name}' ({det.confidence:.0%})")
                if self.stopped:
                    logger.info("购买流程：收到停止信号，取消购买")
                    self._close_shop(rect)
                    return None
                self.click(det.x, det.y, f"选择{crop_name}")
                for _ in range(20):
                    if self.stopped:
                        logger.info("购买流程：等待弹窗时收到停止信号，取消")
                        self._close_shop(rect)
                        return None
                    time.sleep(0.05)
                return self._confirm_purchase(rect, crop_name)
            else:
                # 未找到，尝试滑动列表
                if swipe_count < max_swipe_attempts:
                    logger.info(f"购买流程：模板匹配当前页未找到种子，滑动列表 ({swipe_count + 1}/{max_swipe_attempts})")
                    if self.action_executor:
                        # 在窗口可见范围内滑动（缩短距离，避免超出窗口）
                        sx = self.action_executor._window_left + self.action_executor._client_offset_x + 270
                        sy = self.action_executor._window_top + self.action_executor._client_offset_y + 350
                        ex = self.action_executor._window_left + self.action_executor._client_offset_x + 270
                        ey = self.action_executor._window_top + self.action_executor._client_offset_y + 550
                        dx, dy = ex - sx, ey - sy
                        logger.debug(f"购买流程：执行滑动 屏幕起点=({sx}, {sy}), 终点=({ex}, {ey}), 偏移=({dx}, {dy})")
                        drag_result = self.action_executor.drag(sx, sy, dx, dy, duration=0.3, steps=6)
                        if not drag_result:
                            logger.warning("购买流程：滑动失败！")
                    else:
                        logger.warning("购买流程：action_executor 未初始化，无法滑动")
                    time.sleep(0.8)
                    swipe_count += 1
                else:
                    logger.warning(f"购买流程：模板匹配滑动 {max_swipe_attempts} 次后仍未找到 'shop_{crop_name}'")
                    self._close_shop(rect)
                    return None
        
        self._close_shop(rect)
        return None

    def _confirm_purchase(self, rect: tuple, crop_name: str) -> str | None:
        """购买确认：直接点击确定（游戏自动填充最大数量）"""
        for attempt in range(5):
            if self.stopped:
                return None
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return None

            scene = identify_scene(dets, self.cv_detector, cv_img)

            # 场景检测失败时，尝试直接检测 btn_buy_confirm
            if scene != Scene.BUY_CONFIRM:
                buy_confirm_det = self.find_by_name(dets, "btn_buy_confirm")
                if buy_confirm_det:
                    scene = Scene.BUY_CONFIRM
                    logger.debug("直接检测到 btn_buy_confirm")

            if scene == Scene.BUY_CONFIRM:
                confirm = self.find_by_name(dets, "btn_buy_confirm")
                if confirm:
                    if self.stopped:
                        logger.info("购买流程：点击确认前收到停止信号，取消")
                        self._close_shop(rect)
                        return None
                    self.click(confirm.x, confirm.y, f"确定购买{crop_name}")
                    for _ in range(10):
                        if self.stopped:
                            logger.info("购买流程：等待购买完成时收到停止信号")
                            break
                        time.sleep(0.05)
                    self._close_shop(rect)
                    return f"购买{crop_name}"

            elif scene == Scene.POPUP:
                from core.strategies.popup import PopupStrategy
                ps = PopupStrategy(self.cv_detector)
                ps.action_executor = self.action_executor
                ps.handle_popup(dets)
                for _ in range(6):
                    if self.stopped:
                        return None
                    time.sleep(0.05)
                continue

            logger.info(f"购买流程：等待购买弹窗 ({attempt+1}/5)")
            for _ in range(6):
                if self.stopped:
                    return None
                time.sleep(0.05)

        logger.warning("购买流程：购买弹窗超时")
        self._close_shop(rect)
        return None

    def _close_shop(self, rect):
        if self.stopped:
            return
        from core.strategies.popup import PopupStrategy
        ps = PopupStrategy(self.cv_detector)
        ps.action_executor = self.action_executor
        ps.set_capture_fn(self._capture_fn)
        ps.close_shop(rect)

    def _detect_lands_by_anchor(self, cv_img) -> list[DetectResult]:
        """通过锚点网格推算所有地块位置（降级方案）

        检测 btn_land_right / btn_land_left 锚点按钮，
        然后用 get_lands_from_land_anchor() 数学推算 24 格坐标。
        返回 DetectResult 兼容格式列表。
        """
        anchors = self.cv_detector.detect_targeted(
            cv_img, ['btn_land_right', 'btn_land_left'],
            scales=[1.0, 0.9, 1.1],
        )
        right_anchor = None
        left_anchor = None
        for det in anchors:
            if det.name == 'btn_land_right':
                right_anchor = (int(det.x), int(det.y))
            elif det.name == 'btn_land_left':
                left_anchor = (int(det.x), int(det.y))

        if not right_anchor and not left_anchor:
            logger.warning("施肥流程：锚点检测失败，未找到 btn_land_right / btn_land_left")
            return []

        cells = get_lands_from_land_anchor(right_anchor, left_anchor, rows=4, cols=6)
        if not cells:
            logger.warning("施肥流程：锚点网格推算返回 0 个地块")
            return []

        # 转换为 DetectResult 兼容格式
        results = []
        for cell in cells:
            results.append(DetectResult(
                name=f"land_anchor_{cell.label}",
                category="land",
                x=cell.center[0],
                y=cell.center[1],
                w=0, h=0,
                confidence=1.0,
            ))
        logger.info(f"施肥流程：锚点检测成功，推算 {len(results)} 个地块")
        return results

    def fertilize_all(self, rect: tuple, lands: list = None, is_test: bool = False) -> list[str]:
        """对所有地块施用普通肥料

        流程：点击地块 → 检测是否有普通肥料按钮 → 有就拖拽施肥

        Args:
            rect: 窗口区域
            lands: 地块列表，如果为 None 则检测所有土地
            is_test: 是否为测试模式（测试模式会遍历检测所有地块，正式模式直接使用传入的 lands）

        Returns:
            操作列表
        """
        all_actions = []
        land_dets = None  # 保存所有检测到的土地
        fertilizer_det = None  # 保存检测到的肥料按钮位置
        fertilizer_name = None  # 保存肥料名称

        # 如果没有传入地块列表或者是测试模式，遍历检测所有地块找肥料按钮
        if lands is None or is_test:
            logger.info(f"施肥流程：is_test={is_test}, lands={lands}")
            logger.info(f"施肥流程：_capture_fn={self._capture_fn is not None}, stopped={self.stopped}")
            logger.info(f"施肥流程：action_executor={self.action_executor is not None}")
            if self.stopped:
                return all_actions
            cv_img, dets, _ = self.capture(rect)
            logger.info(f"施肥流程：capture 返回 cv_img={cv_img is not None}, dets={len(dets) if dets else 0}")
            if cv_img is None:
                logger.warning("施肥流程：截屏失败")
                return all_actions

            land_dets = [d for d in dets if d.name.startswith("land_")]
            logger.info(f"施肥流程：检测到 {len(land_dets)} 块土地（原始检测 {len(dets)} 个模板）")
            if not land_dets:
                logger.info("施肥流程：land_ 模板匹配为 0，降级到锚点网格推算")
                land_dets = self._detect_lands_by_anchor(cv_img)
                if not land_dets:
                    logger.info("施肥流程：锚点检测也失败，无法施肥")
                    return all_actions

            logger.info(f"施肥流程：检测到 {len(land_dets)} 块土地，开始点击检测...")
            logger.info(f"施肥流程：stopped={self.stopped}, action_executor={self.action_executor is not None}")

            # 点击每块地，检测是否有施肥按钮
            for i, land in enumerate(land_dets):
                if self.stopped:
                    logger.info("施肥流程：收到停止信号，退出检测")
                    return all_actions
                logger.info(f"检测地块 {i+1}/{len(land_dets)}，位置 ({land.x}, {land.y})")
                logger.info(f"点击前检查：stopped={self.stopped}, action_executor={self.action_executor is not None}")
                click_result = self.click(land.x, land.y, f"点击地块 {i+1}/{len(land_dets)}")
                logger.info(f"点击结果：{click_result}")

                # 等待页面加载
                time.sleep(0.3)

                # 先检测施肥按钮，如果有说明弹出的是施肥菜单，不需要关闭
                cv_check, dets_check, _ = self.capture(rect)
                if cv_check is not None:
                    # 检测肥料按钮（只使用普通肥料）
                    fert_btn_pt = self.cv_detector.detect_single_template(
                        cv_check, "bth_feiliao_pt", threshold=self.cv_detector.get_template_threshold("bth_feiliao_pt"))
                    if fert_btn_pt:
                        logger.info(f"地块 {i+1} 可施肥，找到普通肥料按钮 ({fert_btn_pt[0].confidence:.0%})")
                        # 保存肥料按钮位置，找到肥料按钮后，对所有土地施肥（包括空地）
                        fertilizer_det = fert_btn_pt[0]
                        fertilizer_name = "普通肥料"
                        lands = land_dets  # 使用所有检测到的土地
                        logger.info(f"施肥流程：找到肥料按钮，将对所有 {len(lands)} 块土地施肥")
                        # 不关闭弹窗，直接开始施肥流程
                        break

                    # 没找到肥料按钮，检测是否是个人信息页面
                    self._check_and_close_info_page(rect)
                    time.sleep(0.2)

                    # 重新检测施肥按钮（可能关闭个人信息页面后肥料按钮才显示）
                    cv_check, dets_check, _ = self.capture(rect)
                    if cv_check is not None:
                        logger.debug(f"地块 {i+1} 检测：找到 {len(dets_check)} 个模板")
                        template_names = [d.name for d in dets_check[:15]]
                        logger.info(f"地块 {i+1} 检测到的模板：{template_names}")

                        fert_btn_pt = self.cv_detector.detect_single_template(
                            cv_check, "bth_feiliao_pt", threshold=self.cv_detector.get_template_threshold("bth_feiliao_pt"))
                        if fert_btn_pt:
                            logger.info(f"地块 {i+1} 可施肥，找到普通肥料按钮 ({fert_btn_pt[0].confidence:.0%})")
                            fertilizer_det = fert_btn_pt[0]
                            fertilizer_name = "普通肥料"
                            lands = land_dets
                            logger.info(f"施肥流程：找到肥料按钮，将对所有 {len(lands)} 块土地施肥")
                            break

                logger.debug(f"地块 {i+1} 无可施肥按钮")

                # 点击空白处关闭弹窗
                self.click_blank(rect)
                time.sleep(0.5)

            if not lands or lands != land_dets:
                logger.info("施肥流程：所有地块都无可施肥按钮（空地或已施肥）")
                return all_actions

            # 找到肥料按钮，直接开始拖拽施肥（不关闭弹窗）
            logger.info(f"施肥流程：发现肥料按钮，将对所有 {len(lands)} 块土地施肥...")

        elif lands is None:
            logger.info("施肥流程：未提供地块列表且非测试模式")
            return all_actions

        if not lands:
            logger.info("施肥流程：无可施肥的地块")
            return all_actions

        logger.info(f"施肥流程：对 {len(lands)} 块土地施肥")


        # 如果还没有肥料按钮位置，需要重新检测（非测试模式或之前没保存）
        if not fertilizer_det:
            # 点击第一块地，打开施肥选项
            self.click(lands[0].x, lands[0].y, "点击已播种地块")
            for _ in range(5):
                if self.stopped:
                    return all_actions
                time.sleep(0.05)

            # 检测并关闭个人信息页面
            self._check_and_close_info_page(rect)

            # 查找肥料模板（普通肥料或有机肥料）
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return all_actions

            for attempt in range(2):
                if self.stopped:
                    return all_actions
                cv_img, dets, _ = self.capture(rect)
                if cv_img is None:
                    return all_actions
                # 先检测普通肥料，再检测有机肥料
                fertilizer_dets = self.cv_detector.detect_single_template(
                    cv_img, "bth_feiliao_pt", threshold=self.cv_detector.get_template_threshold("bth_feiliao_pt"))
                if fertilizer_dets:
                    fertilizer_det = fertilizer_dets[0]
                    fertilizer_name = "普通肥料"
                    break
                fertilizer_dets = self.cv_detector.detect_single_template(
                    cv_img, "bth_feiliao2_yj", threshold=self.cv_detector.get_template_threshold("bth_feiliao2_yj"))
                if fertilizer_dets:
                    fertilizer_det = fertilizer_dets[0]
                    fertilizer_name = "有机肥料"
                    break
                for _ in range(5):
                    if self.stopped:
                        return all_actions
                    time.sleep(0.05)

            if not fertilizer_det:
                logger.warning("施肥流程：未找到肥料按钮 (bth_feiliao_pt 或 bth_feiliao2_yj)")
                self.click_blank(rect)
                return all_actions

        logger.info(f"施肥流程：找到 {fertilizer_name}，开始拖拽施肥")

        # 按住肥料，拖拽到每块地
        if not self.action_executor:
            return all_actions

        fert_abs_x, fert_abs_y = self.action_executor.relative_to_absolute(
            fertilizer_det.x, fertilizer_det.y)

        # 确定要施肥的土地列表
        if is_test and land_dets:
            total_count = len(land_dets)
            lands_to_fertilize = land_dets
            logger.info(f"施肥流程：测试模式，对所有 {total_count} 块土地施肥")
        else:
            total_count = len(lands)
            lands_to_fertilize = lands
            logger.info(f"施肥流程：正常模式，对 {total_count} 块土地施肥")

        fert_points = [
            self.action_executor.relative_to_absolute(land.x, land.y)
            for land in lands_to_fertilize
        ]
        done = self.action_executor.drag_multi_points(
            fert_abs_x, fert_abs_y, fert_points,
            check_stopped=lambda: self.stopped)
        fertilized_count = total_count if done else 0
        if not done:
            logger.info("施肥流程：拖拽中途停止")
            return all_actions
        logger.info(f"施肥流程：拖拽施肥完成，共 {fertilized_count} 块")
        all_actions.append(f"施肥×{fertilized_count}")

        # 关闭施肥弹窗
        time.sleep(0.5)
        cv_check, _, _ = self.capture(rect)
        if cv_check is not None:
            fert_popup = self.cv_detector.detect_single_template(
                cv_check, "btn_fertilize_popup", threshold=self.cv_detector.get_template_threshold("btn_fertilize_popup"))
            if fert_popup:
                w, h = rect[2], rect[3]
                self.click(w // 2, int(h * 0.15), "关闭施肥弹窗")
                time.sleep(0.3)

        return all_actions

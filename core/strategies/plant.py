"""P2 生产 — 播种 + 购买种子 + 施肥"""
import time
import pyautogui
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.scene_detector import Scene, identify_scene
from core.strategies.base import BaseStrategy


class PlantStrategy(BaseStrategy):
    def __init__(self, cv_detector: CVDetector):
        super().__init__(cv_detector)
        self.auto_buy_seed = False  # 是否自动购买种子
        self.auto_fertilize = False  # 是否自动施肥

    def _check_and_close_info_page(self, rect: tuple) -> bool:
        """检测并关闭个人信息页面或任务菜单，返回是否成功关闭"""
        if self.stopped:
            return False
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return False

        # 检查关闭按钮（优先 btn_close，其次 btn_info_close）
        close_btn = self.cv_detector.detect_single_template(
            cv_img, "btn_close", threshold=0.6)
        if not close_btn:
            close_btn = self.cv_detector.detect_single_template(
                cv_img, "btn_info_close", threshold=0.6)

        if close_btn:
            self.click(close_btn[0].x, close_btn[0].y, "关闭个人信息页面")
            for _ in range(3):
                if self.stopped:
                    return False
                time.sleep(0.1)
            return True

        # 检查任务菜单按钮 (btn_rw)，有则点击空白处关闭
        btn_rw = self.cv_detector.detect_single_template(
            cv_img, "btn_rw", threshold=0.6)
        if btn_rw:
            logger.info("检测到任务菜单，点击空白处关闭")
            self.click_blank(rect)
            for _ in range(3):
                if self.stopped:
                    return False
                time.sleep(0.1)
            return True

        return False

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

        # 查找种子
        seed_det = None
        for attempt in range(2):
            if self.stopped:
                return all_actions
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return all_actions
            # 每次查找前检查停止和收获
            if self.stopped:
                return all_actions
            harvest_btn = self.find_by_name(dets, "btn_harvest")
            if harvest_btn:
                logger.info("播种流程：检测到一键收获按钮，中断播种优先收获")
                return all_actions
            seed_dets = self.cv_detector.detect_single_template(
                cv_img, f"seed_{crop_name}", threshold=0.8)
            if seed_dets:
                seed_det = seed_dets[0]
                break
            for _ in range(5):
                if self.stopped:
                    return all_actions
                time.sleep(0.05)

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
        pyautogui.moveTo(seed_abs_x, seed_abs_y, duration=0.05)
        for _ in range(4):
            if self.stopped:
                return all_actions
            time.sleep(0.05)
        pyautogui.mouseDown()
        for _ in range(2):
            if self.stopped:
                pyautogui.mouseUp()
                return all_actions
            time.sleep(0.05)

        # 依次拖到每块空地（每块地前 + 移动中检查停止）
        planted_count = 0
        total_count = len(lands)
        for i, land in enumerate(lands, 1):
            if self.stopped:
                pyautogui.mouseUp()
                logger.info("播种流程：拖拽中途停止")
                return all_actions
            abs_x, abs_y = self.action_executor.relative_to_absolute(land.x, land.y)
            # 将 0.1s 移动拆分为 10 段 0.01s，每段前检查停止标志（快速响应）
            for _ in range(10):
                if self.stopped:
                    pyautogui.mouseUp()
                    logger.info("播种流程：拖拽中途停止")
                    return all_actions
                pyautogui.moveTo(abs_x, abs_y, duration=0.01)
            planted_count += 1
            # 每播种 10 块地显示一次进度
            if i % 10 == 0 or i == total_count:
                logger.info(f"播种进度：{i}/{total_count} ({i*100//total_count}%)")

        pyautogui.mouseUp()
        logger.info(f"播种流程：拖拽播种完成，共 {planted_count} 块")
        all_actions.append(f"播种{crop_name}×{planted_count}")

        # 验证：检查是否弹出商店
        time.sleep(0.5)
        cv_check, _, _ = self.capture(rect)
        if cv_check is not None:
            shop_close = self.cv_detector.detect_single_template(
                cv_check, "btn_shop_close", threshold=0.8)
            if shop_close:
                self._close_shop_and_buy(rect, crop_name, all_actions)

        return all_actions

    def _is_already_planted(self, cv_img) -> bool:
        """检查地块是否已播种（通过检测施肥按钮）"""
        # 检测施肥按钮，如果存在说明这块地已经播种了
        fertilize_templates = ["bth_feiliao_pt", "bth_feiliao2_yj", "btn_fertilize_popup"]
        for tpl_name in fertilize_templates:
            result = self.cv_detector.detect_single_template(cv_img, tpl_name, threshold=0.95)
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
        all_actions = []

        # 第一步：截屏找所有空地
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return all_actions
        # 只选择真正的空地（land_empty 或 land_empty2）
        lands = [d for d in dets if d.name.startswith("land_empty")]
        lands.sort(key=lambda d: d.confidence, reverse=True)  # 按置信度排序
        if not lands:
            return all_actions
        total_lands = len(lands)  # 保存总数用于进度显示
        logger.info(f"找到 {len(lands)} 块空地，最高置信度：{lands[0].confidence:.0%}")

        # 点击空地前先检测并关闭个人信息页面
        self._check_and_close_info_page(rect)
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

        # 第四步：找到目标种子
        seed_det = None
        for attempt in range(2):
            if self.stopped:
                return all_actions
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return all_actions
            seed_dets = self.cv_detector.detect_single_template(
                cv_img, f"seed_{crop_name}", threshold=0.8)
            if seed_dets:
                seed_det = seed_dets[0]
                break
            for _ in range(5):
                if self.stopped:
                    return all_actions
                time.sleep(0.05)

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
                    logger.info(f"仓库中没有 '{crop_name}' 种子，去商店购买")
                    buy_result = self._buy_seeds(rect, crop_name)
                    if buy_result:
                        all_actions.append(buy_result)
                        # 买完后重新尝试播种
                        return all_actions + self.plant_all(rect, crop_name)
            else:
                logger.info("自动买种未开启，跳过种植")
            return all_actions

        # 第四步：按住种子，拖拽到每块空地
        logger.info(f"播种流程：找到种子 '{crop_name}'，开始拖拽播种 {len(lands)} 块空地")
        if not self.action_executor:
            return all_actions

        # 按住种子位置（按下前检查停止）
        seed_abs_x, seed_abs_y = self.action_executor.relative_to_absolute(
            seed_det.x, seed_det.y)
        pyautogui.moveTo(seed_abs_x, seed_abs_y, duration=0.05)
        for _ in range(5):
            if self.stopped:
                return all_actions
            time.sleep(0.05)
        pyautogui.mouseDown()
        for _ in range(2):
            if self.stopped:
                pyautogui.mouseUp()
                return all_actions
            time.sleep(0.05)

        # 依次拖到每块空地（每块地前 + 移动中检查停止）
        planted_count = 0
        total_count = len(lands)
        for i, land in enumerate(lands, 1):
            if self.stopped:
                pyautogui.mouseUp()
                logger.info("播种流程：拖拽中途停止")
                return all_actions
            abs_x, abs_y = self.action_executor.relative_to_absolute(land.x, land.y)
            # 将 0.1s 移动拆分为 10 段 0.01s，每段前检查停止标志（快速响应）
            for _ in range(10):
                if self.stopped:
                    pyautogui.mouseUp()
                    logger.info("播种流程：拖拽中途停止")
                    return all_actions
                pyautogui.moveTo(abs_x, abs_y, duration=0.01)
            planted_count += 1
            # 每播种 10 块地显示一次进度
            if i % 10 == 0 or i == total_count:
                logger.info(f"播种进度：{i}/{total_count} ({i*100//total_count}%)")

        # 松开鼠标
        pyautogui.mouseUp()
        logger.info(f"播种流程：拖拽播种完成，共 {planted_count} 块")
        all_actions.append(f"播种{crop_name}×{planted_count}")

        # 验证：检查是否弹出商店（种子用完）或施肥弹窗
        for _ in range(10):
            if self.stopped:
                return all_actions
            time.sleep(0.05)
        cv_check, _, _ = self.capture(rect)
        if cv_check is not None:
            shop_close = self.cv_detector.detect_single_template(
                cv_check, "btn_shop_close", threshold=0.8)
            if shop_close:
                logger.info("播种流程：种子用完，进入购买流程")
                self._close_shop_and_buy(rect, crop_name, all_actions)
                return all_actions

            fert = self.cv_detector.detect_single_template(
                cv_check, "btn_fertilize_popup", threshold=0.7)
            if fert:
                w, h = rect[2], rect[3]
                self.click(w // 2, int(h * 0.15), "关闭施肥弹窗")
                time.sleep(0.5)  # 等待点击后页面恢复
                # 验证是否成功关闭，检查是否误开个人信息页面
                cv_check2, dets2, _ = self.capture(rect)
                if cv_check2 is not None:
                    info_close = self.cv_detector.detect_single_template(
                        cv_check2, "btn_info_close", threshold=0.6)
                    if info_close:
                        logger.info("播种流程：误开个人信息页面，关闭")
                        self.click(info_close[0].x, info_close[0].y, "关闭个人信息页面")
                        time.sleep(0.3)

        # 播种完成后，如果开启了自动施肥，立即对相同地块施肥
        if auto_fertilize and self.auto_fertilize and planted_count > 0:
            logger.info("播种完成，开始施肥...")
            fert_actions = self.fertilize_all(rect, lands)
            if fert_actions:
                all_actions.extend(fert_actions)

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
                cv_img, f"seed_{crop_name}", threshold=0.8)

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
                        cv_check, "btn_shop_close", threshold=0.8)
                    if shop_close:
                        logger.info("播种流程：种子已用完，进入购买流程")
                        self._close_shop_and_buy(rect, crop_name, actions_done)
                        return actions_done

                    fert = self.cv_detector.detect_single_template(
                        cv_check, "btn_fertilize_popup", threshold=0.7)
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

        # 在种子页签中查找目标种子
        for attempt in range(3):
            if self.stopped:
                logger.info("检查仓库：收到停止信号，取消")
                self._close_warehouse(rect)
                return {"has_seed": False, "quantity": 0, "position": None}
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                self._close_warehouse(rect)
                return {"has_seed": False, "quantity": 0, "position": None}

            # 查找 seed_作物名 模板（仓库中使用更高阈值 0.95 避免误报）
            seed_det = self.cv_detector.detect_single_template(
                cv_img, f"seed_{crop_name}", threshold=0.95)

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
                logger.info(f"仓库中未找到种子：{crop_name}")
                # 每次查找间隔也检查停止
                for _ in range(3):
                    if self.stopped:
                        self._close_warehouse(rect)
                        return {"has_seed": False, "quantity": 0, "position": None}
                    time.sleep(0.05)

        self._close_warehouse(rect)
        return {"has_seed": False, "quantity": 0, "position": None}

    def _close_warehouse(self, rect: tuple):
        """关闭仓库页面"""
        if self.stopped:
            return
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return
        # 找关闭按钮或空白处
        close_btn = self.find_any(dets, ["btn_close", "btn_shop_close"])
        if close_btn:
            self.click(close_btn.x, close_btn.y, "关闭仓库")
        else:
            self.click_blank(rect)
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
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return
        # 只选择真正的空地（land_empty 或 land_empty2）
        lands = [d for d in dets if d.name.startswith("land_empty")]
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
            cv_img2, f"seed_{crop_name}", threshold=0.85)
        if seed_dets:
            self.click(seed_dets[0].x, seed_dets[0].y,
                       f"播种{crop_name}", ActionType.PLANT)
            actions_done.append(f"播种{crop_name}")

    def _buy_seeds(self, rect: tuple, crop_name: str) -> str | None:
        """购买种子流程：打开商店 → 用 shop_xx 模板匹配找种子 → 点击 → 确认购买"""
        logger.info("购买流程：打开商店")
        if self.stopped:
            return None

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

        # 等待商店打开并查找种子
        for attempt in range(5):
            if self.stopped:
                return None
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return None

            shop_close = self.cv_detector.detect_single_template(
                cv_img, "btn_shop_close", threshold=0.8)
            if not shop_close:
                logger.info(f"购买流程：等待商店加载 ({attempt+1}/5)")
                for _ in range(10):
                    if self.stopped:
                        return None
                    time.sleep(0.05)
                continue

            logger.info("购买流程：商店已打开，查找种子")
            seed_dets = self.cv_detector.detect_single_template(
                cv_img, f"shop_{crop_name}", threshold=0.6)

            if seed_dets:
                det = seed_dets[0]
                logger.info(f"购买流程：找到 '{crop_name}' ({det.confidence:.0%})")
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
                break
            else:
                logger.warning(f"购买流程：商店中未找到 'shop_{crop_name}' 模板")
                self._close_shop(rect)
                return None
        else:
            logger.warning("购买流程：商店加载超时")
            self._close_shop(rect)
            return None

        return self._confirm_purchase(rect, crop_name)

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

    def fertilize_all(self, rect: tuple, lands: list = None) -> list[str]:
        """对所有已播种地块施用普通肥料

        流程：点击地块 → 弹出施肥选项 → 点击普通肥料 (bth_feiliao_pt) → 拖拽到所有地块

        Args:
            rect: 窗口区域
            lands: 已播种的地块列表（由 plant_all 传入），如果为 None 则尝试遍历所有地块检测

        Returns:
            操作列表
        """
        all_actions = []

        # 如果没有传入地块列表，尝试通过遍历检测
        if lands is None:
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return all_actions

            # 遍历所有土地模板，点击检测是否已播种
            lands = []
            land_dets = [d for d in dets if d.name.startswith("land_")]
            if not land_dets:
                logger.info("施肥流程：未找到任何地块")
                return all_actions

            logger.info(f"施肥流程：检测到 {len(land_dets)} 块土地，遍历检测已播种地块...")

            # 点击每块地检测是否有施肥按钮（测试模式不检查停止标志）
            for i, land in enumerate(land_dets[:5]):  # 最多检测 5 块
                logger.info(f"检测地块 {i+1}/{min(5, len(land_dets))}，位置 ({land.x}, {land.y})")
                self.click(land.x, land.y, f"检测地块 {i+1}/{min(5, len(land_dets))}")
                time.sleep(0.5)  # 等待点击生效

                # 关闭可能弹出的个人信息页面
                self._check_and_close_info_page(rect)
                time.sleep(0.3)

                # 检测施肥按钮
                cv_check, dets_check, _ = self.capture(rect)
                if cv_check is not None:
                    logger.debug(f"地块 {i+1} 检测：找到 {len(dets_check)} 个模板")
                    fert_btn = self.cv_detector.detect_single_template(
                        cv_check, "bth_feiliao_pt", threshold=0.6)
                    if fert_btn:
                        # 已播种，记录地块位置
                        lands.append(land)
                        logger.info(f"地块 {i+1} 已播种，找到施肥按钮")
                    else:
                        logger.info(f"地块 {i+1} 未找到施肥按钮 bth_feiliao_pt")

                # 点击空白处关闭弹窗
                self.click_blank(rect)
                time.sleep(0.3)

            if not lands:
                logger.info("施肥流程：未找到已播种的地块")
                return all_actions

        logger.info(f"施肥流程：对 {len(lands)} 块已播种地块施肥")

        # 点击第一块地，打开施肥选项
        self.click(lands[0].x, lands[0].y, "点击已播种地块")
        for _ in range(5):
            if self.stopped:
                return all_actions
            time.sleep(0.05)

        # 检测并关闭个人信息页面
        self._check_and_close_info_page(rect)

        # 查找普通肥料模板
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return all_actions

        fertilizer_det = None
        for attempt in range(2):
            if self.stopped:
                return all_actions
            fertilizer_dets = self.cv_detector.detect_single_template(
                cv_img, "bth_feiliao_pt", threshold=0.8)
            if fertilizer_dets:
                fertilizer_det = fertilizer_dets[0]
                break
            for _ in range(5):
                if self.stopped:
                    return all_actions
                time.sleep(0.05)

        if not fertilizer_det:
            logger.warning("施肥流程：未找到普通肥料 (bth_feiliao_pt)")
            self.click_blank(rect)
            return all_actions

        logger.info(f"施肥流程：找到普通肥料，开始拖拽施肥 {len(fertilized_lands)} 块地")

        # 按住肥料，拖拽到每块地
        if not self.action_executor:
            return all_actions

        fert_abs_x, fert_abs_y = self.action_executor.relative_to_absolute(
            fertilizer_det.x, fertilizer_det.y)
        pyautogui.moveTo(fert_abs_x, fert_abs_y, duration=0.05)
        for _ in range(4):
            if self.stopped:
                return all_actions
            time.sleep(0.05)
        pyautogui.mouseDown()
        for _ in range(2):
            if self.stopped:
                pyautogui.mouseUp()
                return all_actions
            time.sleep(0.05)

        # 依次拖到每块地
        fertilized_count = 0
        total_count = len(fertilized_lands)
        for i, land in enumerate(fertilized_lands, 1):
            if self.stopped:
                pyautogui.mouseUp()
                logger.info("施肥流程：拖拽中途停止")
                return all_actions
            abs_x, abs_y = self.action_executor.relative_to_absolute(land.x, land.y)
            for _ in range(10):
                if self.stopped:
                    pyautogui.mouseUp()
                    logger.info("施肥流程：拖拽中途停止")
                    return all_actions
                pyautogui.moveTo(abs_x, abs_y, duration=0.01)
            fertilized_count += 1
            if i % 10 == 0 or i == total_count:
                logger.info(f"施肥进度：{i}/{total_count} ({i*100//total_count}%)")

        pyautogui.mouseUp()
        logger.info(f"施肥流程：拖拽施肥完成，共 {fertilized_count} 块")
        all_actions.append(f"施肥×{fertilized_count}")

        # 关闭施肥弹窗
        time.sleep(0.5)
        cv_check, _, _ = self.capture(rect)
        if cv_check is not None:
            fert_popup = self.cv_detector.detect_single_template(
                cv_check, "btn_fertilize_popup", threshold=0.7)
            if fert_popup:
                w, h = rect[2], rect[3]
                self.click(w // 2, int(h * 0.15), "关闭施肥弹窗")
                time.sleep(0.3)

        return all_actions

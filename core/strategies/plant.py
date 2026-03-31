"""P2 生产 — 播种 + 购买种子 + 施肥"""
import time
import pyautogui
from loguru import logger

from models.farm_state import ActionType
from core.cv_detector import DetectResult
from core.scene_detector import Scene, identify_scene
from core.strategies.base import BaseStrategy


class PlantStrategy(BaseStrategy):

    def _plant_remaining_lands(self, rect: tuple, lands: list, crop_name: str,
                                buy_qty: int) -> list[str]:
        """播种剩余的空地（跳过第一块已验证不是空地的地块）"""
        if not lands:
            return []
        all_actions = []

        # 点击第一块剩余的空地
        self.click(lands[0].x, lands[0].y, "点击空地")
        time.sleep(0.3)

        # 查找种子
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
            time.sleep(0.3)

        if not seed_det:
            # 还是没有种子，这块地也可能不是空地
            logger.info(f"剩余地块中仍未找到种子，跳过 {lands[0]}")
            self.click_blank(rect)
            time.sleep(0.3)
            if len(lands) > 1:
                return self._plant_remaining_lands(rect, lands[1:], crop_name, buy_qty)
            return all_actions

        # 找到种子，按住拖拽到所有剩余空地
        logger.info(f"播种流程：找到种子 '{crop_name}'，拖拽播种 {len(lands)} 块地")
        if not self.action_executor:
            return all_actions

        seed_abs_x, seed_abs_y = self.action_executor.relative_to_absolute(
            seed_det.x, seed_det.y)
        pyautogui.moveTo(seed_abs_x, seed_abs_y, duration=0.05)
        time.sleep(0.1)
        if self.stopped:
            return all_actions
        pyautogui.mouseDown()
        time.sleep(0.05)

        planted_count = 0
        for land in lands:
            if self.stopped:
                pyautogui.mouseUp()
                logger.info("播种流程：拖拽中途停止")
                return all_actions
            abs_x, abs_y = self.action_executor.relative_to_absolute(land.x, land.y)
            pyautogui.moveTo(abs_x, abs_y, duration=0.1)
            planted_count += 1

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
                self._close_shop_and_buy(rect, crop_name, buy_qty, all_actions)

        return all_actions

    def plant_all(self, rect: tuple, crop_name: str,
                  buy_qty: int = 50) -> list[str]:
        """快速播种所有空地：点击空地弹出种子列表 → 按住种子拖拽到所有空地"""
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
        logger.info(f"找到 {len(lands)} 块空地，最高置信度：{lands[0].confidence:.0%}")

        # 第二步：点击第一块空地，弹出种子列表
        self.click(lands[0].x, lands[0].y, "点击空地")
        time.sleep(0.3)

        # 第三步：找到目标种子
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
            time.sleep(0.3)

        if not seed_det:
            # 没找到种子，检查仓库是否有种子
            logger.info(f"播种流程：未找到 '{crop_name}' 种子，检查仓库...")
            warehouse_result = self.check_warehouse_seeds(rect, crop_name)
            if warehouse_result["has_seed"]:
                # 仓库有种子但弹窗中没有，说明这块地不是真正的空地（已播种/成熟/杂草）
                # 跳过这块地，点击空白处关闭弹窗后返回
                logger.info(f"仓库有种子但弹窗未显示，'{lands[0]}' 可能不是空地，跳过")
                self.click_blank(rect)
                time.sleep(0.3)
                # 从剩余的空地中继续播种（排除第一块）
                if len(lands) > 1:
                    return self._plant_remaining_lands(rect, lands[1:], crop_name, buy_qty)
                return all_actions
            logger.info(f"仓库中没有 '{crop_name}' 种子，去商店购买")
            buy_result = self._buy_seeds(rect, crop_name, buy_qty)
            if buy_result:
                all_actions.append(buy_result)
                # 买完后重新尝试播种
                return all_actions + self.plant_all(rect, crop_name, buy_qty)
            return all_actions

        # 第四步：按住种子，拖拽到每块空地
        logger.info(f"播种流程：找到种子 '{crop_name}'，开始拖拽播种 {len(lands)} 块空地")
        if not self.action_executor:
            return all_actions

        # 按住种子位置（按下前检查停止）
        seed_abs_x, seed_abs_y = self.action_executor.relative_to_absolute(
            seed_det.x, seed_det.y)
        pyautogui.moveTo(seed_abs_x, seed_abs_y, duration=0.05)
        time.sleep(0.1)
        if self.stopped:
            return all_actions
        pyautogui.mouseDown()
        time.sleep(0.05)

        # 依次拖到每块空地（每块地前检查停止）
        planted_count = 0
        for land in lands:
            if self.stopped:
                pyautogui.mouseUp()
                logger.info("播种流程：拖拽中途停止")
                return all_actions
            abs_x, abs_y = self.action_executor.relative_to_absolute(land.x, land.y)
            pyautogui.moveTo(abs_x, abs_y, duration=0.1)
            planted_count += 1

        # 松开鼠标
        pyautogui.mouseUp()
        logger.info(f"播种流程：拖拽播种完成，共 {planted_count} 块")
        all_actions.append(f"播种{crop_name}×{planted_count}")

        # 验证：检查是否弹出商店（种子用完）
        time.sleep(0.5)
        cv_check, _, _ = self.capture(rect)
        if cv_check is not None:
            shop_close = self.cv_detector.detect_single_template(
                cv_check, "btn_shop_close", threshold=0.8)
            if shop_close:
                logger.info("播种流程：种子用完，进入购买流程")
                self._close_shop_and_buy(rect, crop_name, buy_qty, all_actions)

            fert = self.cv_detector.detect_single_template(
                cv_check, "btn_fertilize_popup", threshold=0.7)
            if fert:
                w, h = rect[2], rect[3]
                self.click(w // 2, int(h * 0.15), "关闭施肥弹窗")

        return all_actions

    def _plant_one(self, rect: tuple, land_det: DetectResult,
                   crop_name: str, buy_qty: int) -> list[str]:
        """播种单块空地"""
        actions_done = []
        self.click(land_det.x, land_det.y, "点击空地")

        for attempt in range(2):
            if self.stopped:
                return actions_done
            time.sleep(0.3)

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
                time.sleep(0.5)
                cv_check, _, _ = self.capture(rect)
                if cv_check is not None:
                    shop_close = self.cv_detector.detect_single_template(
                        cv_check, "btn_shop_close", threshold=0.8)
                    if shop_close:
                        logger.info("播种流程：种子已用完，进入购买流程")
                        self._close_shop_and_buy(rect, crop_name, buy_qty, actions_done)
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
                self._close_shop_and_buy(rect, crop_name, buy_qty, actions_done)
                return actions_done

        else:
            logger.info(f"播种流程：未找到 '{crop_name}' 种子，去商店购买")
            self.click_blank(rect)
            time.sleep(0.3)

        # 去商店买
        buy_result = self._buy_seeds(rect, crop_name, buy_qty)
        if buy_result:
            actions_done.append(buy_result)
            self._retry_plant_after_buy(rect, crop_name, actions_done)
        return actions_done


    def _close_shop_and_buy(self, rect, crop_name, buy_qty, actions_done):
        """关闭自动弹出的商店，再手动购买"""
        if self.stopped:
            return
        from core.strategies.popup import PopupStrategy
        ps = PopupStrategy(self.cv_detector)
        ps.action_executor = self.action_executor
        ps.set_capture_fn(self._capture_fn)
        ps.close_shop(rect)
        buy_result = self._buy_seeds(rect, crop_name, buy_qty)
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
            time.sleep(0.1)

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
                    time.sleep(0.1)
                break
            for _ in range(3):
                if self.stopped:
                    self._close_warehouse(rect)
                    return {"has_seed": False, "quantity": 0, "position": None}
                time.sleep(0.1)
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

            # 查找 seed_作物名 模板
            seed_det = self.cv_detector.detect_single_template(
                cv_img, f"seed_{crop_name}", threshold=0.7)

            if seed_det:
                logger.info(f"仓库中找到种子：{crop_name}")
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
                    time.sleep(0.1)

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
        for _ in range(3):
            if self.stopped:
                return
            time.sleep(0.1)

    def _retry_plant_after_buy(self, rect, crop_name, actions_done):
        """购买完成后重新点空地播种"""
        if self.stopped:
            return
        time.sleep(0.3)
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
        time.sleep(0.5)
        cv_img2, _, _ = self.capture(rect)
        if cv_img2 is None:
            return
        seed_dets = self.cv_detector.detect_single_template(
            cv_img2, f"seed_{crop_name}", threshold=0.85)
        if seed_dets:
            self.click(seed_dets[0].x, seed_dets[0].y,
                       f"播种{crop_name}", ActionType.PLANT)
            actions_done.append(f"播种{crop_name}")

    def _buy_seeds(self, rect: tuple, crop_name: str,
                   buy_qty: int) -> str | None:
        """购买种子流程：打开商店 → 用 shop_xx 模板匹配找种子 → 点击 → 确认购买"""
        logger.info("购买流程：打开商店")
        if self.stopped:
            return None
        cv_img, dets, _ = self.capture(rect)
        if cv_img is None:
            return None

        shop_btn = self.find_by_name(dets, "btn_shop")
        if not shop_btn:
            logger.warning("购买流程：未找到商店按钮")
            return None
        self.click(shop_btn.x, shop_btn.y, "打开商店")
        time.sleep(1.0)  # 等待商店页面加载动画

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
                time.sleep(0.5)  # 等待页面渲染
                continue

            logger.info("购买流程：商店已打开，查找种子")
            seed_dets = self.cv_detector.detect_single_template(
                cv_img, f"shop_{crop_name}", threshold=0.6)

            if seed_dets:
                det = seed_dets[0]
                logger.info(f"购买流程：找到 '{crop_name}' ({det.confidence:.0%})")
                self.click(det.x, det.y, f"选择{crop_name}")
                time.sleep(1.0)  # 等待购买弹窗出现
                break
            else:
                logger.warning(f"购买流程：商店中未找到 'shop_{crop_name}' 模板")
                self._close_shop(rect)
                return None
        else:
            logger.warning("购买流程：商店加载超时")
            self._close_shop(rect)
            return None

        return self._confirm_purchase(rect, crop_name, buy_qty)

    def _confirm_purchase(self, rect: tuple, crop_name: str,
                          buy_qty: int) -> str | None:
        """购买确认：点加号设置数量 → 点确定"""
        for attempt in range(5):
            if self.stopped:
                return None
            cv_img, dets, _ = self.capture(rect)
            if cv_img is None:
                return None

            scene = identify_scene(dets, self.cv_detector, cv_img)
            if scene == Scene.BUY_CONFIRM:
                if buy_qty > 1:
                    max_btn = self.find_by_name(dets, "btn_buy_max")
                    if max_btn and self.action_executor:
                        clicks = buy_qty - 1
                        logger.info(f"购买流程：点击加号 {clicks} 次")
                        abs_x, abs_y = self.action_executor.relative_to_absolute(
                            max_btn.x, max_btn.y)
                        for _ in range(clicks):
                            if self.stopped:
                                return None
                            pyautogui.click(abs_x, abs_y)
                            time.sleep(0.1)
                        time.sleep(0.3)  # 等待数量更新

                confirm = self.find_by_name(dets, "btn_buy_confirm")
                if confirm:
                    self.click(confirm.x, confirm.y, f"确定购买{crop_name}×{buy_qty}")
                    time.sleep(0.3)  # 等待购买完成动画
                    self._close_shop(rect)
                    return f"购买{crop_name}×{buy_qty}"

            elif scene == Scene.POPUP:
                from core.strategies.popup import PopupStrategy
                ps = PopupStrategy(self.cv_detector)
                ps.action_executor = self.action_executor
                ps.handle_popup(dets)
                time.sleep(0.3)  # 等待弹窗关闭
                continue

            logger.info(f"购买流程：等待购买弹窗 ({attempt+1}/5)")
            time.sleep(0.3)

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

"""模板采集工具 - 从游戏截图中裁剪并保存模板图片（支持多边形框选）

使用方法：
1. 打开QQ农场小程序窗口
2. 运行此脚本: python tools/template_collector.py
3. 程序会截取游戏窗口画面
4. 用鼠标左键逐点点击绘制多边形轮廓
5. 按 Enter 完成多边形，右键取消最后一个点
6. 输入模板名称，保存为带透明通道的 PNG（背景自动剔除）

命名规范：
  btn_xxx    - 按钮（收获、播种、浇水等）
  icon_xxx   - 状态图标（虫子、杂草、缺水等）
  crop_xxx   - 作物状态（成熟、枯死等）
  land_xxx   - 土地状态（空地等）
  ui_xxx     - UI元素（返回按钮等）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
from core.window_manager import WindowManager
from core.screen_capture import ScreenCapture

# 显示窗口的最大尺寸（适配屏幕）
MAX_DISPLAY_WIDTH = 1280
MAX_DISPLAY_HEIGHT = 800


class TemplateCollector:
    def __init__(self):
        self.wm = WindowManager()
        self.sc = ScreenCapture()
        self.templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates"
        )
        os.makedirs(self.templates_dir, exist_ok=True)
        self._drawing = False
        self._points = []       # 多边形顶点（显示坐标）
        self._original_image = None   # 原始截图（全分辨率）
        self._display_image = None    # 缩放后用于显示的图
        self._scale = 1.0             # 缩放比例

    def capture_game_window(self, keyword: str = "QQ经典农场") -> np.ndarray | None:
        window = self.wm.find_window(keyword)
        if not window:
            print(f"未找到包含 '{keyword}' 的窗口")
            print("请先打开微信小程序中的QQ农场")
            return None

        self.wm.activate_window()
        import time
        time.sleep(0.5)

        rect = (window.left, window.top, window.width, window.height)
        image = self.sc.capture_region(rect)
        if image is None:
            print("截屏失败")
            return None

        rgb = np.array(image.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def _resize_for_display(self, image: np.ndarray) -> np.ndarray:
        """缩放图片以适配屏幕显示，并记录缩放比例"""
        h, w = image.shape[:2]
        scale_w = MAX_DISPLAY_WIDTH / w if w > MAX_DISPLAY_WIDTH else 1.0
        scale_h = MAX_DISPLAY_HEIGHT / h if h > MAX_DISPLAY_HEIGHT else 1.0
        self._scale = min(scale_w, scale_h)

        if self._scale < 1.0:
            new_w = int(w * self._scale)
            new_h = int(h * self._scale)
            return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            self._scale = 1.0
            return image.copy()

    def _display_to_original(self, x: int, y: int) -> tuple[int, int]:
        """将显示坐标转换为原图坐标"""
        ox = int(x / self._scale)
        oy = int(y / self._scale)
        # 限制在原图范围内
        h, w = self._original_image.shape[:2]
        ox = max(0, min(ox, w - 1))
        oy = max(0, min(oy, h - 1))
        return ox, oy

    def _mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self._points.append((x, y))
            # 在显示图上绘制多边形
            self._display_image = self._resize_for_display(self._original_image)
            self._draw_polygon(self._display_image)
        elif event == cv2.EVENT_RBUTTONDOWN:
            # 右键撤销最后一个点
            if self._points:
                self._points.pop()
                self._display_image = self._resize_for_display(self._original_image)
                self._draw_polygon(self._display_image)
        elif event == cv2.EVENT_MOUSEMOVE:
            # 移动时绘制预览线
            if self._points:
                self._display_image = self._resize_for_display(self._original_image)
                self._draw_polygon(self._display_image)
                # 绘制从最后一个点到鼠标位置的预览线
                cv2.line(self._display_image, self._points[-1], (x, y), (0, 200, 80), 1)
                cv2.circle(self._display_image, (x, y), 3, (0, 200, 80), -1)

    def _draw_polygon(self, img):
        """在图像上绘制多边形"""
        if len(self._points) >= 2:
            pts = np.array(self._points, dtype=np.int32)
            cv2.polylines(img, [pts], False, (0, 255, 0), 2)
            # 绘制顶点
            for pt in self._points:
                cv2.circle(img, pt, 4, (0, 255, 0), -1)
        elif len(self._points) == 1:
            cv2.circle(img, self._points[0], 4, (0, 255, 0), -1)

    def _extract_polygon_region(self) -> np.ndarray | None:
        """提取多边形区域，返回带透明通道的 PNG"""
        if len(self._points) < 3:
            return None

        # 转换为原图坐标
        original_points = [self._display_to_original(*pt) for pt in self._points]
        pts = np.array(original_points, dtype=np.int32)

        # 计算边界框
        x, y, w, h = cv2.boundingRect(pts)
        if w < 5 or h < 5:
            return None

        # 裁剪区域
        cropped = self._original_image[y:y+h, x:x+w].copy()

        # 创建蒙版（多边形内部为白色，外部为黑色）
        mask = np.zeros((h, w), dtype=np.uint8)
        # 调整多边形坐标到裁剪区域内
        pts_offset = pts - [x, y]
        cv2.fillPoly(mask, [pts_offset], 255)

        # 合并为 BGRA 四通道图像
        bgra = cv2.cvtColor(cropped, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = mask  # 设置 alpha 通道

        return bgra

    def run(self):
        print("=" * 50)
        print("  QQ农场模板采集工具（多边形框选）")
        print("=" * 50)
        print()
        print("操作说明：")
        print("  1. 鼠标左键逐点点击绘制多边形轮廓")
        print("  2. 按 Enter 完成多边形并保存")
        print("  3. 右键撤销最后一个点")
        print("  4. 按 R 重新截屏")
        print("  5. 按 Q 退出")
        print()
        print("命名规范：")
        print("  btn_harvest  - 收获按钮      icon_weed   - 杂草图标")
        print("  btn_plant    - 播种按钮      icon_bug    - 虫子图标")
        print("  btn_water    - 浇水按钮      icon_water  - 缺水图标")
        print("  btn_weed     - 除草按钮      icon_mature - 成熟标志")
        print("  btn_bug      - 除虫按钮      crop_mature - 成熟作物")
        print("  btn_close    - 关闭弹窗      crop_dead   - 枯死作物")
        print("  btn_sell     - 出售按钮      land_empty  - 空地")
        print()

        self._original_image = self.capture_game_window()
        if self._original_image is None:
            return

        h, w = self._original_image.shape[:2]
        print(f"截图尺寸: {w}x{h}")

        self._display_image = self._resize_for_display(self._original_image)
        if self._scale < 1.0:
            dh, dw = self._display_image.shape[:2]
            print(f"显示缩放: {self._scale:.2f} ({dw}x{dh})")

        window_name = "Template Collector - Enter:Save R:Refresh Q:Quit"
        cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(window_name, self._mouse_callback)

        saved_count = 0

        while True:
            cv2.imshow(window_name, self._display_image)
            key = cv2.waitKey(30) & 0xFF

            if key == ord('q') or key == 27:
                break

            elif key == ord('r'):
                print("重新截屏...")
                self._original_image = self.capture_game_window()
                if self._original_image is not None:
                    self._display_image = self._resize_for_display(self._original_image)
                    self._points = []
                    h, w = self._original_image.shape[:2]
                    print(f"截屏完成 ({w}x{h})")

            elif key == 13 or key == 10:  # Enter
                if len(self._points) < 3:
                    print("至少需要 3 个点才能形成多边形")
                    continue

                bgra = self._extract_polygon_region()
                if bgra is None:
                    print("提取区域失败，请重新绘制")
                    continue

                # 显示预览
                preview = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
                # 创建棋盘格背景来显示透明度
                checker = np.zeros_like(preview)
                checker[::10, ::10] = 128
                checker[5::10, 5::10] = 128
                preview = cv2.addWeighted(preview, 0.7, checker, 0.3, 0)
                cv2.imshow("Preview", preview)

                name = input("\n输入模板名称 (如 btn_harvest): ").strip()
                if not name:
                    print("已取消")
                    continue

                filepath = os.path.join(self.templates_dir, f"{name}.png")
                # cv2.imwrite 不支持中文路径，用 imencode + 写文件
                success, buf = cv2.imencode('.png', bgra)
                if success:
                    buf.tofile(filepath)
                saved_count += 1
                print(f"✓ 已保存: {filepath} (第{saved_count}个)")

                # 重置
                self._points = []
                self._display_image = self._resize_for_display(self._original_image)
                cv2.destroyWindow("Preview")

        cv2.destroyAllWindows()
        print(f"\n采集完成，共保存 {saved_count} 个模板到 {self.templates_dir}")


if __name__ == "__main__":
    collector = TemplateCollector()
    collector.run()
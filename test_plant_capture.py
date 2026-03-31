"""测试 plant_all 中的 capture 方法"""
import os
import cv2
import numpy as np
from core.cv_detector import CVDetector
from core.screen_capture import ScreenCapture
from core.window_manager import WindowManager
from core.strategies.plant import PlantStrategy

# 初始化
print("初始化...")
detector = CVDetector()
detector.load_templates()
capture = ScreenCapture()
window_manager = WindowManager()

# 找到窗口
print("查找窗口...")
window = window_manager.find_window("QQ经典农场")
if not window:
    print("未找到QQ农场窗口")
    exit(1)

print(f"找到窗口: {window.title} ({window.width}x{window.height})")

# 创建 PlantStrategy
plant_strategy = PlantStrategy(detector)

# 设置 capture 函数
def test_capture(rect, save=False):
    print(f"调用 capture: {rect}")
    if save:
        image, _ = capture.capture_and_save(rect, "test")
    else:
        image = capture.capture_region(rect)
    if image is None:
        print("截图失败")
        return None, [], None
    cv_image = detector.pil_to_cv2(image)
    
    # 检测所有类别
    detections = []
    for cat in detector._templates:
        if cat in ("seed", "shop"):
            continue
        if cat == "land":
            thresh = 0.8
        elif cat == "button":
            thresh = 0.8
        else:
            thresh = 0.8
        detections += detector.detect_category(cv_image, cat, threshold=thresh)
    
    # 过滤
    detections = [d for d in detections
                  if d.name != "btn_shop_close"
                  and not (d.name == "btn_expand" and d.confidence < 0.85)]
    
    # 打印检测结果
    print(f"检测到 {len(detections)} 个对象:")
    for d in detections:
        print(f"  - {d.name}: {d.confidence:.2f} at ({d.x}, {d.y})")
    
    return cv_image, detections, image

plant_strategy.set_capture_fn(test_capture)

# 测试 plant_all
print("\n测试 plant_all...")
rect = (window.left, window.top, window.width, window.height)
crop_name = "土豆"
buy_qty = 50

result = plant_strategy.plant_all(rect, crop_name, buy_qty)
print(f"plant_all 结果: {result}")

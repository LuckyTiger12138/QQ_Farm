"""详细测试土地检测"""
import os
import cv2
import numpy as np
from core.cv_detector import CVDetector
from core.screen_capture import ScreenCapture
from core.window_manager import WindowManager

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

# 截图
rect = (window.left, window.top, window.width, window.height)
print(f"截图区域: {rect}")
image = capture.capture_region(rect)
if image is None:
    print("截图失败")
    exit(1)

# 转换为OpenCV格式
cv_image = detector.pil_to_cv2(image)

# 保存截图以便查看
cv2.imwrite("test_screenshot.png", cv_image)
print("截图已保存为 test_screenshot.png")

# 检查土地模板
print("\n检查土地模板:")
if "land" in detector._templates:
    print(f"找到 {len(detector._templates['land'])} 个土地模板:")
    for tpl in detector._templates["land"]:
        print(f"  - {tpl['name']}: {tpl['image'].shape}")
else:
    print("未找到 land 类别")

# 测试每个土地模板
print("\n测试每个土地模板:")
if "land" in detector._templates:
    for tpl in detector._templates["land"]:
        print(f"\n测试模板: {tpl['name']}")
        # 单模板检测
        results = detector.detect_single_template(cv_image, tpl['name'], threshold=0.7)
        print(f"  检测结果: {len(results)} 个匹配")
        for r in results:
            print(f"    - 置信度: {r.confidence:.2f}, 位置: ({r.x}, {r.y})")

print("\n测试完成！")

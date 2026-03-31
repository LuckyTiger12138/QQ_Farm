"""测试土地检测"""
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

# 测试土地检测
print("\n测试土地检测...")
lands = detector.detect_category(cv_image, "land", threshold=0.8)
print(f"检测到 {len(lands)} 块土地")
for land in lands:
    print(f"  - {land.name}: {land.confidence:.2f} at ({land.x}, {land.y})")

# 测试按钮检测
print("\n测试按钮检测...")
buttons = detector.detect_category(cv_image, "button", threshold=0.8)
print(f"检测到 {len(buttons)} 个按钮")
for btn in buttons[:5]:  # 只显示前5个
    print(f"  - {btn.name}: {btn.confidence:.2f} at ({btn.x}, {btn.y})")

print("\n测试完成！")

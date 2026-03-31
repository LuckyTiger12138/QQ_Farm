"""测试当前游戏中的空地数量（使用更低阈值）"""
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
cv2.imwrite("land_screenshot2.png", cv_image)
print("截图已保存为 land_screenshot2.png")

# 检测所有土地模板（使用更低阈值）
print("\n检测土地模板:")
land_detections = []

# 检测所有土地相关模板
for tpl in detector._templates.get("land", []):
    # 使用更低的阈值
    results = detector.detect_single_template(cv_image, tpl['name'], threshold=0.6)
    print(f"  模板 {tpl['name']}: 检测到 {len(results)} 个匹配")
    for r in results:
        land_detections.append(r)

# 去重（非极大值抑制）
if land_detections:
    # 按置信度排序
    land_detections.sort(key=lambda r: r.confidence, reverse=True)
    # 简单去重：保留置信度最高的，去除距离太近的
    unique_lands = []
    for land in land_detections:
        # 检查是否与已保留的土地距离太近
        too_close = False
        for existing in unique_lands:
            distance = ((land.x - existing.x)**2 + (land.y - existing.y)**2)**0.5
            if distance < 20:  # 距离阈值
                too_close = True
                break
        if not too_close:
            unique_lands.append(land)
    
    print(f"\n去重后检测到 {len(unique_lands)} 块空地:")
    for i, land in enumerate(unique_lands):
        print(f"  空地 {i+1}: {land.name} (置信度: {land.confidence:.2f}, 位置: ({land.x}, {land.y}))")
else:
    print("未检测到空地")

print("\n测试完成！")

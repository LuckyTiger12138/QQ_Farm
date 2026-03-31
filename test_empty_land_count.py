#!/usr/bin/env python3
"""测试当前农场中的空地数量"""
import time
from core.window_manager import WindowManager
from core.screen_capture import ScreenCapture
from core.cv_detector import CVDetector


def test_empty_land_count():
    """测试当前农场中的空地数量"""
    print("开始检测空地数量...")
    
    # 初始化窗口管理器
    window_manager = WindowManager()
    window = window_manager.find_window("QQ经典农场")
    if not window:
        print("未找到QQ农场窗口")
        return
    
    print(f"找到窗口: {window.title} ({window.width}x{window.height})")
    
    # 检查窗口大小是否正常
    if window.width < 400 or window.height < 600:
        print("警告：窗口大小异常，可能被最小化或只显示了标题栏")
        print("请确保QQ农场窗口正常打开且可见")
        return
    
    # 初始化屏幕捕获
    screen_capture = ScreenCapture()
    rect = (window.left, window.top, window.width, window.height)
    
    # 初始化CV检测器
    detector = CVDetector(templates_dir="templates")
    detector.load_templates()
    
    # 捕获屏幕
    image = screen_capture.capture_region(rect)
    if image is None:
        print("截屏失败")
        return
    
    # 转换为OpenCV格式
    cv_image = detector.pil_to_cv2(image)
    
    # 只检测land类别模板，使用更高的阈值来减少误检测
    detections = detector.detect_category(cv_image, "land", threshold=0.95)
    
    # 输出所有land检测结果
    print(f"\n所有land检测结果 ({len(detections)}):")
    for i, d in enumerate(detections, 1):
        print(f"  {i}. {d.name} - 置信度: {d.confidence:.0%} - 位置: ({d.x}, {d.y})")
    
    # 过滤出空地
    empty_lands = [d for d in detections if d.name.startswith("land_empty")]
    
    # 按置信度排序
    empty_lands.sort(key=lambda d: d.confidence, reverse=True)
    
    # 输出结果
    print(f"\n找到 {len(empty_lands)} 块空地：")
    for i, land in enumerate(empty_lands, 1):
        print(f"  {i}. {land.name} - 置信度: {land.confidence:.0%} - 位置: ({land.x}, {land.y})")
    
    print(f"\n检测完成，共找到 {len(empty_lands)} 块空地")


if __name__ == "__main__":
    test_empty_land_count()

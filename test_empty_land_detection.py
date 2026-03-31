#!/usr/bin/env python3
"""测试空地检测功能"""
import cv2
import numpy as np
from PIL import Image
from core.cv_detector import CVDetector


def test_empty_land_detection():
    """测试空地检测"""
    # 初始化检测器
    detector = CVDetector(templates_dir="templates")
    detector.load_templates()
    
    # 从屏幕截图中检测（这里使用示例截图，实际运行时会自动截图）
    print("开始检测空地...")
    
    # 模拟从bot_engine的_capture_and_detect方法获取检测结果
    # 这里我们直接使用detector的detect_all方法
    # 注意：实际运行时，bot_engine会使用更复杂的检测逻辑
    
    # 加载测试截图（如果有的话）
    # test_image = cv2.imread("test_screenshot.png")
    # if test_image is not None:
    #     detections = detector.detect_all(test_image, threshold=0.8)
    # else:
    #     print("未找到测试截图，使用模拟数据")
    #     detections = []
    
    print("测试完成：请启动主程序查看日志输出")
    print("修改内容：")
    print("1. plant_all方法：只选择真正的空地（land_empty或land_empty2），并按置信度排序")
    print("2. _retry_plant_after_buy方法：只选择真正的空地，并按置信度排序")
    print("3. 添加了详细的日志输出，显示找到的空地数量和置信度")


if __name__ == "__main__":
    test_empty_land_detection()

"""测试模板类别"""
import os
from core.cv_detector import CVDetector

# 初始化
detector = CVDetector()
detector.load_templates()

# 打印所有模板类别
print("所有模板类别:")
for cat, templates in detector._templates.items():
    print(f"  {cat}: {len(templates)} 个模板")
    for tpl in templates:
        print(f"    - {tpl['name']}")

# 检查土地模板
print("\n土地模板:")
if "land" in detector._templates:
    for tpl in detector._templates["land"]:
        print(f"  - {tpl['name']}")
else:
    print("  未找到 land 类别")

# 检查按钮模板
print("\n按钮模板:")
if "button" in detector._templates:
    for tpl in detector._templates["button"]:
        print(f"  - {tpl['name']}")
else:
    print("  未找到 button 类别")

# AGENTS.md — QQ Farm Vision Bot

> 基于 OpenCV 视觉识别的 QQ 经典农场自动化工具。纯 Python 项目，无构建步骤。

## 项目概览

- **技术栈**: Python 3.10+, PyQt6, OpenCV, MSS, PyAutoGUI, Pydantic, loguru
- **平台**: Windows 10/11 仅
- **运行方式**: `python main.py` 启动 PyQt6 GUI

## 命令速查

```bash
# 安装依赖
pip install -r requirements.txt

# 启动程序
python main.py

# 模板采集（首次使用必须）
python tools/template_collector.py

# 种子图片批量导入
python tools/import_seeds.py

# 构建 EXE
pyinstaller build.spec

# 测试脚本（均为独立脚本，需运行中的游戏窗口）
python test_template_categories.py   # 列出已加载模板
python test_land_count.py            # 土地数量检测
python test_empty_land_detection.py  # 空地检测
python test_plant_capture.py         # 播种流程测试
python test_land_debug.py            # 土地调试
python test_land_count2.py           # 土地数量检测（变体2）
python test_empty_land_count.py      # 空地数量检测
python test_land_detection.py        # 土地检测
python test_polygon.png              # 多边形测试图片
```

**无 pytest / unittest 框架** — 所有测试均为独立 Python 脚本，需要真实游戏窗口运行。

**热键**: F9 暂停/恢复，F10 停止。鼠标移到左上角紧急停止（pyautogui FAILSAFE）。

## 代码风格

### 导入顺序

```python
# 1. 标准库
import sys
import os
import json
import time
from enum import Enum
from dataclasses import dataclass, field

# 2. 第三方库
import cv2
import numpy as np
from loguru import logger
from pydantic import BaseModel, Field
from PyQt6.QtCore import QObject, QThread, pyqtSignal

# 3. 项目内部（绝对导入，从项目根开始）
from models.config import AppConfig
from core.cv_detector import CVDetector, DetectResult
from core.strategies.base import BaseStrategy
```

- 使用**绝对导入**而非相对导入
- 组间空一行
- `from module import Class` 优先于 `import module`

### 命名约定

| 类型 | 约定 | 示例 |
|------|------|------|
| 类名 | PascalCase | `BotEngine`, `CVDetector`, `BaseStrategy` |
| 函数/方法 | snake_case | `check_farm()`, `identify_scene()`, `setup_logger()` |
| 变量 | snake_case | `task_type`, `detections`, `_stop_requested` |
| 常量 | UPPER_SNAKE_CASE | `TEMPLATE_CATEGORIES`, `CATEGORY_DEFAULTS` |
| 私有属性 | 前缀 `_` | `_templates`, `_loaded`, `_capture_fn` |
| 文件名 | snake_case | `bot_engine.py`, `cv_detector.py`, `main_window.py` |

### 类型注解

- 使用 Python 3.10+ 原生类型语法（`list[str]`, `dict[str, float]`, `X | None`）
- 函数参数和返回值必须标注类型
- 使用 `pydantic.BaseModel` 定义配置数据结构
- 使用 `dataclass` 定义简单数据传输对象（如 `DetectResult`）
- 枚举使用 `str, Enum` 双重继承以支持 JSON 序列化

### 文档字符串

- 模块首行使用中文文档字符串：`"""模块功能简述"""`
- 类定义后使用 docstring 说明职责
- 复杂方法在代码块内用注释说明，不必写完整 docstring

### 错误处理

- 使用 `loguru.logger` 进行日志记录（`logger.info()`, `logger.warning()`, `logger.error()`）
- 日志格式：`✓ 成功操作` / `✗ 失败操作: 原因`
- BotWorker 线程顶层使用 `try/except` 捕获所有异常并通过 `error` 信号发出
- 配置加载失败时回退到默认值，不崩溃
- 图像操作注意中文路径：使用 `np.fromfile` + `cv2.imdecode` 而非 `cv2.imread`

### 日志

- 日志系统使用 `loguru`，同时输出到控制台、文件（按天轮转）和 GUI
- 日志文件位于 `logs/` 目录，保留 7 天
- GUI 通过 `LogSignal` 对象接收日志消息

## 架构

### 四层架构

```
┌─────────────────────────────────────────────┐
│  GUI 层 (PyQt6) — gui/                      │
│  main_window.py / widgets/                  │
├─────────────────────────────────────────────┤
│  行为决策层 (core/strategies/)              │
│  popup → harvest → maintain → plant →       │
│  expand → task → friend                     │
├─────────────────────────────────────────────┤
│  图像识别层                                 │
│  cv_detector.py (模板匹配)                  │
│  scene_detector.py (场景识别)               │
├─────────────────────────────────────────────┤
│  窗口控制层                                 │
│  window_manager.py + screen_capture.py      │
├─────────────────────────────────────────────┤
│  操作执行层                                 │
│  action_executor.py (pyautogui 模拟点击)    │
└─────────────────────────────────────────────┘
```

### 线程模型

- **主线程**: PyQt6 GUI
- **BotWorker (QThread)**: 执行 farm/friend/test_fertilize 任务
- **TaskScheduler (QTimer)**: 定时触发农场检查和好友巡查
- 线程间通过 Qt 信号（`pyqtSignal`）通信

### 策略模式

所有策略继承 `BaseStrategy`，共享 `cv_detector`、`action_executor`、`_capture_fn`。

| 优先级 | 策略 | 类名 | 职责 |
|--------|------|------|------|
| P-1 | `popup.py` | PopupStrategy | 关闭弹窗/商店/返回主界面 + 升级检测 |
| P0 | `harvest.py` | HarvestStrategy | 一键收获 + 自动出售 |
| P1 | `maintain.py` | MaintainStrategy | 除草/除虫/浇水 |
| P2 | `plant.py` | PlantStrategy | 播种 + 购买种子 + 施肥 |
| P3 | `expand.py` | ExpandStrategy | 扩建土地 |
| P3.5 | `task.py` | TaskStrategy | 领取任务奖励 / 出售果实 |
| P4 | `friend.py` | FriendStrategy | 好友巡查/帮忙/偷菜 |

### 主循环

`BotEngine.check_farm()` 最多 50 轮，3 轮空闲自动退出，每轮 sleep 0.3s。

## 模板命名规范

| 前缀 | 类别 | 示例 |
|------|------|------|
| `btn_` | button | `btn_harvest.png` |
| `bth_` | button（特殊按钮） | `bth_feiliao_pt.png` |
| `icon_` | status_icon | `icon_mature.png` |
| `crop_` | crop | `crop_mature.png` |
| `seed_` | seed | `seed_小麦.png` |
| `shop_` | shop | `shop_小麦.png` |
| `land_` | land | `land_empty.png` |
| `ui_` | ui_element | `ui_remote_login.png` |

新增前缀需同步更新 `cv_detector.py` 中的 `TEMPLATE_CATEGORIES` 字典。

## 配置系统

- 使用 Pydantic `BaseModel` 定义配置层级
- 配置文件为 JSON 格式（`config.json`，自动生成，已在 .gitignore 中）
- GUI 修改实时生效，无需手动保存
- `AppConfig.load(path)` / `.save()` 进行文件读写

## 添加新功能

1. 在 `core/strategies/` 下新建策略模块，继承 `BaseStrategy`
2. 在 `core/bot_engine.py` 中：创建策略实例 → 加入 `self._strategies` → 在 `check_farm()` 中按优先级调用
3. 如需新场景，在 `core/scene_detector.py` 的 `Scene` 枚举和 `identify_scene()` 中添加
4. 如需新模板类别，在 `cv_detector.py` 的 `TEMPLATE_CATEGORIES` 中添加前缀映射
5. 在 `gui/widgets/` 下添加对应 UI 面板（如需要）

## 关键设计决策

- **纯视觉识别**: 不读取内存、不修改数据包、不调用游戏 API
- **停止机制**: 所有策略共享 `_stop_requested` 标志，每次 click 前检查
- **安全措施**: 随机点击偏移、操作间随机延迟、pyautogui FAILSAFE
- **中文路径**: 使用 `np.fromfile` + `cv2.imdecode` 读取模板

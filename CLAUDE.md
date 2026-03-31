# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

QQ Farm Vision Bot — 基于 OpenCV 视觉识别的 QQ 经典农场（微信小程序）自动化工具。纯本地运行，不依赖游戏接口，零封号风险。

**技术栈**: Python 3.10+, PyQt6, OpenCV, MSS, PyAutoGUI, Pydantic

## Quick Start

```bash
# 安装依赖
pip install -r requirements.txt

# 启动程序
python main.py

# 模板采集（首次使用必须）
python tools/template_collector.py

# 种子图片批量导入
python tools/import_seeds.py
```

**热键**: F9 暂停/恢复，F10 停止

## Architecture

### 四层架构

```
┌─────────────────────────────────────────────┐
│  GUI 层 (PyQt6)                              │
│  main_window.py / widgets/                   │
├─────────────────────────────────────────────┤
│  行为决策层 (core/strategies/)               │
│  popup → harvest → maintain → plant →        │
│  expand → task → friend                      │
├─────────────────────────────────────────────┤
│  图像识别层                                  │
│  cv_detector.py (模板匹配)                   │
│  scene_detector.py (场景识别)                │
├─────────────────────────────────────────────┤
│  窗口控制层                                  │
│  window_manager.py + screen_capture.py       │
├─────────────────────────────────────────────┤
│  操作执行层                                  │
│  action_executor.py (pyautogui 模拟点击)     │
└─────────────────────────────────────────────┘
```

### 策略优先级 (core/strategies/)

| 优先级 | 策略 | 职责 |
|--------|------|------|
| P-1 | `popup.py` | 关闭弹窗/商店/返回主界面 + 升级检测 |
| P0 | `harvest.py` | 一键收获 + 自动出售 |
| P1 | `maintain.py` | 除草/除虫/浇水 |
| P2 | `plant.py` | 播种 + 购买种子 + 施肥 |
| P3 | `expand.py` | 扩建土地 |
| P3.5 | `task.py` | 领取任务奖励 / 出售果实 |
| P4 | `friend.py` | 好友巡查/帮忙/偷菜 |

### 核心组件

- **`core/bot_engine.py`**: 主控编排层，`BotEngine` 负责初始化各层组件，`BotWorker` 在独立线程执行任务
- **`core/task_scheduler.py`**: 定时调度器，管理农场检查（默认 1 分钟）和好友巡查（默认 30 分钟）
- **`core/cv_detector.py`**: OpenCV 模板匹配引擎，支持多尺度检测（0.8x~1.2x）
- **`core/scene_detector.py`**: 场景识别状态机（农场主页/商店/好友家/弹窗/升级等）
- **`models/config.py`**: Pydantic 配置模型，GUI 修改实时生效
- **`models/game_data.py`**: 33 种作物静态数据表（经验/生长时间等）

### 场景识别状态机

```python
class Scene(Enum):
    FARM_OVERVIEW = "农场主页"
    SHOP_PAGE = "商店页面"
    BUY_CONFIRM = "购买确认"
    SEED_SELECT = "种子选择"
    FRIEND_FARM = "好友家园"
    POPUP = "弹窗"
    LEVEL_UP = "升级弹窗"
    PLOT_MENU = "土地菜单"
    UNKNOWN = "未知"
```

## Template Naming Convention

| 前缀 | 类别 | 示例 |
|------|------|------|
| `btn_` | 按钮 | `btn_harvest.png` |
| `icon_` | 状态图标 | `icon_mature.png` |
| `seed_` | 种子图标（播种列表） | `seed_小麦.png` |
| `shop_` | 商店种子卡片 | `shop_小麦.png` |
| `land_` | 土地状态 | `land_empty.png` |

## Adding New Features

1. 在 `core/strategies/` 下新建策略模块，继承 `BaseStrategy`
2. 在 `core/bot_engine.py` 中注册策略并按优先级编排
3. 如需新场景，在 `core/scene_detector.py` 中添加枚举和识别逻辑
4. 在 `gui/widgets/` 下添加对应的 UI 面板（如需要）

## Configuration

`config.json` 结构（GUI 修改实时生效，无需手动保存）:

```json
{
  "planting": {
    "strategy": "best_exp_rate",  // 或 "preferred"
    "player_level": 10,
    "window_width": 581,
    "window_height": 1054
  },
  "schedule": {
    "farm_check_minutes": 1,
    "friend_check_minutes": 30
  },
  "features": {
    "auto_harvest": true,
    "auto_plant": true,
    "auto_weed": true,
    "auto_water": true,
    "auto_bug": true
  }
}
```

## Testing

```bash
# 运行测试
python test_land_count.py
python test_empty_land_detection.py
python test_plant_capture.py
```

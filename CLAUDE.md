# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

QQ Farm Vision Bot — 基于 OpenCV 视觉识别的 QQ 经典农场（微信小程序）自动化工具。纯本地运行，不依赖游戏接口，零封号风险。

**技术栈**: Python 3.10+, PyQt6, OpenCV, MSS, PyAutoGUI, Pydantic, loguru, FastAPI (可选 Web 面板)

## Commands

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
```

无 pytest 测试套件。测试脚本为独立脚本，需运行中的游戏窗口：
- `test_template_categories.py` — 列出已加载模板
- `test_land_count.py` — 土地数量检测
- `test_empty_land_detection.py` — 空地检测
- `test_plant_capture.py` — 播种流程测试

**热键**: F9 暂停/恢复，F10 停止。鼠标移到左上角可紧急停止（pyautogui FAILSAFE）。

## Architecture

### 数据流

```
截屏 (mss) → OpenCV 多尺度模板匹配 → 场景识别状态机 → 策略决策 → ActionExecutor (前台 pyautogui / 后台 PostMessageW) → 循环
```

### 四层架构

```
┌─────────────────────────────────────────────┐
│  GUI 层 (PyQt6) + Web 层 (FastAPI, 可选)    │
│  gui/main_window.py / gui/widgets/           │
│  web/server.py (截图预览、启停控制)          │
├─────────────────────────────────────────────┤
│  行为决策层 (core/strategies/)               │
│  popup → harvest → maintain → plant →        │
│  expand → task → friend                      │
├─────────────────────────────────────────────┤
│  图像识别层                                  │
│  cv_detector.py (模板匹配)                   │
│  scene_detector.py (场景识别)                │
├─────────────────────────────────────────────┤
│  窗口控制层 + 操作执行层                     │
│  window_manager.py + screen_capture.py       │
│  action_executor.py (前台/后台, RunMode)     │
└─────────────────────────────────────────────┘
```

### 主控编排: BotEngine (core/bot_engine.py)

- **BotEngine** (QObject): 初始化各层组件、管理配置、连接 Qt 信号
- **BotWorker** (QThread): 在独立线程执行 farm/friend/test_fertilize 任务
- **TaskScheduler** (QTimer): 定时触发农场检查和好友巡查，含窗口存活监控
- **TaskExecutor** (core/task_executor.py): 基于优先级的任务调度器，TaskItem 定义 enabled/check/run/cooldown 函数
- 策略按优先级注册在 `self._strategies` 列表中
- 主循环 `check_farm()` 最多 50 轮，3 轮空闲自动退出，每轮 sleep 0.3s
- 静默时段: `core/silent_hours.py` 支持跨午夜时段（如 22:00-06:00），静默期间不执行操作

### 策略模式 (core/strategies/)

所有策略继承 `BaseStrategy`，共享 `cv_detector`、`action_executor`、`_capture_fn`。

**BaseStrategy** 提供:
- `click(x, y, desc)` — 构造 Action 并通过 action_executor 执行
- `find_by_name()`, `find_by_prefix_first()`, `find_any()` — 检测结果查询
- `stopped` 属性 — 检查停止/暂停信号，所有操作前必须检查

| 优先级 | 策略文件 | 类名 | 职责 |
|--------|----------|------|------|
| P-1 | `popup.py` | PopupStrategy | 关闭弹窗/商店/商城/返回主界面 + 升级检测 |
| P0 | `harvest.py` | HarvestStrategy | 一键收获 + 自动出售 |
| P1 | `maintain.py` | MaintainStrategy | 除草/除虫/浇水 |
| P2 | `plant.py` | PlantStrategy | 播种 + 购买种子 + 施肥 |
| P3 | `expand.py` | ExpandStrategy | 扩建土地 |
| P3.5 | `task.py` | TaskStrategy | 领取任务奖励 / 出售果实 |
| P4 | `friend.py` | FriendStrategy | 好友巡查/帮忙/偷菜/同意好友 |

### 场景识别状态机 (core/scene_detector.py)

Scene 枚举值: FARM_OVERVIEW, FRIEND_FARM, PLOT_MENU, SEED_SELECT, SHOP_PAGE, **MALL_PAGE**（商城干扰页面）, WAREHOUSE, BUY_CONFIRM, POPUP, LEVEL_UP, FRIEND_LIST, INFO_PAGE, REMOTE_LOGIN, UNKNOWN

`identify_scene()` 根据检测到的模板名称集合判断场景，检测顺序有优先级（REMOTE_LOGIN → INFO_PAGE → MALL_PAGE → BUY_CONFIRM → ...）。

### 图像检测 (core/cv_detector.py)

- **模板加载**: 从 `templates/` 目录加载，文件名前缀决定类别
- **多尺度检测**: 0.8x ~ 1.3x 缩放范围
- **NMS**: 非极大值抑制去除重叠结果（IoU 阈值 0.5）
- **DetectResult**: 包含 name, category, x, y, w, h, confidence

### 配置系统 (models/config.py)

Pydantic BaseModel 层级结构，GUI 修改实时生效:
- `AppConfig` → FeaturesConfig, SafetyConfig, ScreenshotConfig, ScheduleConfig, PlantingConfig, SellConfig, **SilentHoursConfig**, **WebConfig**, **FriendConfig**（嵌套在 FeaturesConfig 中）
- `AppConfig.load(path)` / `.save()` — JSON 文件读写
- `PlantMode`: PREFERRED / BEST_EXP_RATE
- `SellMode`: BATCH_ALL / SELECTIVE
- `RunMode`: FOREGROUND / BACKGROUND

### Web 服务 (web/server.py)

FastAPI Web 控制面板（可选，需安装 fastapi+uvicorn）：截图预览、启停控制、状态查看、日志、配置编辑。默认端口 8080。通过回调函数与 BotEngine 交互，不直接引用 GUI。

### 数据模型

- `models/farm_state.py`: `ActionType` 枚举, `Action`, `OperationResult`
- `models/game_data.py`: 作物静态数据表，`get_best_crop_for_level()` 根据等级返回最优作物

## Template Naming Convention

| 前缀 | 类别 | 示例 |
|------|------|------|
| `btn_` | button | `btn_harvest.png` |
| `bth_` | button（特殊按钮如施肥） | `bth_fertilize.png` |
| `icon_` | status_icon | `icon_mature.png` |
| `friend_` | ui_element（好友列表标识） | `friend_list.png` |
| `crop_` | crop | `crop_mature.png` |
| `seed_` | seed（播种列表） | `seed_小麦.png` |
| `shop_` | shop（商店卡片） | `shop_小麦.png` |
| `land_` | land | `land_empty.png` |
| `ui_` | ui_element | `ui_next_time.png` |

前缀与 `TEMPLATE_CATEGORIES` 字典映射决定模板分类。新增前缀需同时更新 `cv_detector.py` 中的 `TEMPLATE_CATEGORIES`。

## Adding New Features

1. 在 `core/strategies/` 下新建策略模块，继承 `BaseStrategy`
2. 在 `core/bot_engine.py` 中: 创建策略实例 → 加入 `self._strategies` 列表 → 在 `check_farm()` 主循环中按优先级添加调用
3. 如需新场景，在 `core/scene_detector.py` 的 `Scene` 枚举和 `identify_scene()` 中添加
4. 如需新模板类别，在 `cv_detector.py` 的 `TEMPLATE_CATEGORIES` 中添加前缀映射
5. 在 `gui/widgets/` 下添加对应的 UI 面板（如需要）

## Known Limitations

- **显示器比例**: 16:10 或非标准比例显示器可能存在坐标精度丢失，建议 16:9 显示器使用
- **自动买种**: 仓库库存判断存在 Bug，建议关闭该功能让用户手动购买
- **播种位置**: 播种流程仅检测仓库第一排前 5 个格子，种子必须放在该位置

## Key Design Decisions

- **纯视觉识别**: 不读取内存、不修改数据包、不调用游戏 API，仅通过屏幕截图 + 模板匹配
- **双模式执行**: ActionExecutor 支持 `RunMode.FOREGROUND`（pyautogui）和 `RunMode.BACKGROUND`（PostMessageW），后台模式不抢占鼠标
- **线程模型**: PyQt6 GUI 在主线程，BotWorker(QThread) 执行任务，通过 Qt 信号通信
- **停止机制**: 所有策略共享 `_stop_requested` 标志，每个 click 操作前检查，支持优雅停止
- **安全措施**: 随机点击偏移（`click_offset_range`）、操作间随机延迟、pyautogui FAILSAFE
- **中文路径**: `cv_detector.py` 使用 `np.fromfile` + `cv2.imdecode` 读取模板，因为 `cv2.imread` 不支持中文路径
- **Git 双仓库**: origin → Gitee, github → GitHub。发行版文件发布在 GitHub Releases（Gitee 有 100MB 限制）

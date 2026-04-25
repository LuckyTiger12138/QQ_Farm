# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

QQ Farm Vision Bot — 基于 OpenCV 视觉识别的 QQ 经典农场（微信小程序）自动化工具。纯本地运行，不依赖游戏接口，零封号风险。支持多实例同时运行。

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

**热键**: F9 暂停/恢复，F10 停止，F11 老板键（隐藏窗口）。鼠标移到左上角可紧急停止（pyautogui FAILSAFE）。

## Architecture

### 数据流

```
截屏 (mss) → OpenCV 多尺度模板匹配 → 场景识别状态机 → 策略决策 → ActionExecutor (前台 pyautogui / 后台 PostMessageW) → 循环
```

### 多实例架构

程序支持同时管理多个游戏窗口（多开）。每个实例拥有独立的配置、日志、截图目录。

- **InstanceManager** (`core/instance_manager.py`): 管理所有实例的元数据、配置和会话，存储在 `instances/profiles.json`
- **InstanceSession**: 封装单个实例的元数据（id/name/state）+ 路径 + 配置
- **实例目录结构**: `instances/{id}/configs/config.json`, `instances/{id}/logs/`, `instances/{id}/screenshots/`
- **MainWindow** 维护 `dict[str, BotEngine]` 映射（`_engines`），每个实例一个 BotEngine
- **InstanceSidebar** (`gui/widgets/instance_sidebar.py`): 实例列表 UI，支持右键菜单（新增/克隆/重命名/删除）
- **BotEngine** 构造函数接受 `instance_id` 和 `cross_bus` 参数，用于日志/截图目录隔离和跨实例通讯

### 四层架构

```
┌─────────────────────────────────────────────┐
│  GUI 层 (PyQt6) + Web 层 (FastAPI, 可选)    │
│  main_window.py / widgets/                   │
│  InstanceSidebar (多实例切换)                 │
│  web/server.py (截图预览、启停控制)          │
├─────────────────────────────────────────────┤
│  行为决策层 (core/strategies/)               │
│  popup → harvest → maintain → plant →        │
│  expand → task → friend → gift →             │
│  targeted_steal                              │
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

- **BotEngine** (QObject): 初始化各层组件、管理配置、连接 Qt 信号。每个实例一个 BotEngine
- **BotWorker** (QThread): 在独立线程执行 farm/friend/test_fertilize 任务
- **TaskScheduler** (`core/task_scheduler.py`, QObject + QTimer): 定时触发农场检查和好友巡查，含窗口存活监控
- **TaskExecutor** (`core/task_executor.py`): 基于优先级的异步任务调度器，支持 interval/daily 触发、时段过滤、失败重试、热更新。通过 `TaskScheduleItemConfig` 配置每个任务
- 策略按优先级注册在 `self._strategies` 列表中
- 主循环 `check_farm()` 最多 50 轮，3 轮空闲自动退出，每轮 sleep 0.3s
- 静默时段: `core/silent_hours.py` 支持跨午夜时段（如 22:00-06:00），静默期间不执行操作
- 快速模板过滤: `SCENE_TEMPLATES` / `LAND_TEMPLATES` / `MAINTAIN_TEMPLATES` 常量只加载场景判断所需的模板子集，避免加载全部 70+ 模板

### 策略模式 (core/strategies/)

所有策略继承 `BaseStrategy`，共享 `cv_detector`、`action_executor`、`_capture_fn`。策略在 `__init__.py` 中统一导出。

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
| P4 | `friend.py` | FriendStrategy | 帮忙除草/浇水/除虫/偷菜 |
| P5 | `gift.py` | GiftStrategy | SVIP 礼包 / 商城免费领取 / 邮件领取 |
| — | `targeted_steal.py` | TargetedStealStrategy | 定点偷菜（大小号通讯触发） |

### 页面导航 (core/ui/navigator.py)

**Navigator**: 基于 BFS 的页面导航器，通过 `ui_ensure(target_page)` 自动从当前页面跳转到目标页面。依赖 `core/ui/page.py` 中的 `Page` 定义和 `ALL_PAGES` 页面图。需要注入 `capture_fn`、`click_fn`、`stopped_fn` 三个回调。

### 地块巡查 (tasks/land_scan.py)

**LandScanTask**: 两阶段滑动扫描（左滑 + 右滑）+ 逐块点击 OCR 采集。利用 `utils/land_grid.py` 的 `LandCell` / `get_lands_from_land_anchor` 计算地块网格坐标，`utils/ocr_utils.py` 的 `OCRTool` 识别成熟倒计时文字。扫描结果供大小号通讯判断即将成熟的地块。

### 大小号通讯 (core/cross_instance_bus.py)

**CrossInstanceBus**: 全局单例内存消息总线（线程安全），所有 BotEngine 共享。数据流：
```
Instance A 地块巡查 → 检测成熟倒计时 < N 秒 → post_alert(StealAlert)
→ Instance B TaskExecutor.poll_alerts() → 动态创建 TargetedStealTask → 定点偷菜
```
- **StealAlert**: 包含 source_instance_id、friend_name、plot_ids、earliest_maturity_seconds
- 内置去重（4 分钟内同一配对不重复发送）
- 配置在 `CrossInstanceConfig` 中：send_alerts / accept_steal / partners

### 场景识别状态机 (core/scene_detector.py)

Scene 枚举值: FARM_OVERVIEW, FRIEND_FARM, PLOT_MENU, SEED_SELECT, SHOP_PAGE, MALL_PAGE（商城干扰页面）, WAREHOUSE, BUY_CONFIRM, POPUP, LEVEL_UP, FRIEND_LIST, INFO_PAGE, REMOTE_LOGIN, UNKNOWN

`identify_scene()` 根据检测到的模板名称集合判断场景，检测顺序有优先级（REMOTE_LOGIN → INFO_PAGE → MALL_PAGE → BUY_CONFIRM → ...）。

### 图像检测 (core/cv_detector.py)

- **模板加载**: 从 `templates/` 目录加载，文件名前缀决定类别
- **多尺度检测**: 0.8x ~ 1.3x 缩放范围
- **NMS**: 非极大值抑制去除重叠结果（IoU 阈值 0.5）
- **DetectResult**: 包含 name, category, x, y, w, h, confidence

### 配置系统 (models/config.py)

Pydantic BaseModel 层级结构，GUI 修改实时生效:
- `AppConfig` → FeaturesConfig, SafetyConfig, ScreenshotConfig, ScheduleConfig, PlantingConfig, SellConfig, SilentHoursConfig, WebConfig, CrossInstanceConfig, LandConfig, ExecutorConfig
- `AppConfig.load(path)` / `.save()` — JSON 文件读写
- 每个实例有独立配置文件: `instances/{id}/configs/config.json`
- `PlantMode`: PREFERRED / BEST_EXP_RATE / LATEST_LEVEL
- `SellMode`: BATCH_ALL / SELECTIVE
- `RunMode`: FOREGROUND / BACKGROUND
- `TaskScheduleItemConfig`: 每个任务的调度配置（间隔/时段/优先级/触发方式），默认任务列表通过 `get_default_tasks()` 获取（main, profile, friend, land_scan, gift, sell, task, fertilize, share）
- `CrossInstanceConfig`: 大小号通讯功能配置（send_alerts / accept_steal / partners）
- `sync_features_to_tasks()`: 将 FeaturesConfig 开关同步到 tasks.features，保持向后兼容

### Web 服务 (web/server.py)

FastAPI Web 控制面板（可选，需安装 fastapi+uvicorn）：截图预览、启停控制、状态查看、日志、配置编辑。默认端口 8080。通过回调函数与 BotEngine 交互，不直接引用 GUI。

### 自动施肥 (PlantStrategy)

两种触发方式：
- **播种后施肥**: `plant_all(auto_fertilize=True)` 播种完成后立即对所有土地施肥，由 `config.features.auto_fertilize` 控制
- **定时施肥**: `_run_task_fertilize()` 由 TaskExecutor 按间隔定时触发，对全部地块执行施肥

施肥流程 (`fertilize_all()`): 锚点检测地块网格 → 逐块点击检测施肥按钮 → 找到肥料（普通/有机）→ 拖拽施肥 → 关闭施肥弹窗。支持 `is_test` 模式遍历所有地块检测。

### 个人信息获取与等级 OCR

两阶段 OCR 采集，结果写入 `config.land.profile`：
1. **主界面 OCR** (`_sync_head_profile_from_ocr()`): 截取头部 ROI，OCR 识别等级/金币/点券/经验
2. **详情页精确经验** (`_run_task_profile()`): 点击经验文字打开个人信息页，OCR 获取精确经验值

- `utils/head_info_ocr.py`: 头部信息 OCR 工具
- `utils/ocr_utils.py`: 通用 OCR 工具
- 配置: `level_ocr_enabled` 开关，`LandProfileConfig(level/gold/coupon/exp)` 存储

### 自动获取地块信息与升级预估 (LandDetailPanel)

`LandScanTask` 定期扫描地块状态（等级、成熟倒计时等），结果存储在 `config.land.plots`。

`estimate_upgrade_hint()` 在地块详情面板展示升级预估：
- 解析 OCR 经验字符串（`parse_exp_string()`，支持 "1234/5000"、"1.2万/3.4万"、"1.5亿/3亿"）
- 根据当前作物经验 × 活跃地块数 × 生长时间，计算所需种植次数和预计耗时
- 展示格式: `"种 4 次 (椰子, 单季, 24块地×476经验/季, 共45696经验, 预计16小时)"`

### 自动出售 (TaskStrategy)

`try_sell_direct()` 实现仓库直接导航出售（不依赖任务条），由 `_run_task_sell()` 定时调度：
- **流程**: 检测 `btn_warehouse` → 进入仓库 → `_batch_sell()` 点击批量出售 → 自动全选 → 点击确认
- **回退**: 若当前页面无仓库按钮，回退到任务条路径 `try_task()` 触发出售
- 只检测出售相关模板（`btn_batch_sell`、`btn_confirm`、`btn_close`），速度快
- 目前仅支持批量全部出售

### 礼品领取 (GiftStrategy)

`try_gift()` 按序执行三个子功能（各自独立开关）：
1. **QQSVIP 礼包**: 主页检测 `btn_qqsvip` 入口并领取
2. **商城免费商品**: 导航到商城页面领取免费物品
3. **邮件领取**: 导航到菜单→邮件页面领取附件

执行完毕后自动导航回主页。由 `_run_task_gift()` 以 daily 模式调度。

### 数据模型

- `models/farm_state.py`: `ActionType` 枚举, `Action`, `OperationResult`
- `models/game_data.py`: 作物静态数据表，`get_best_crop_for_level()` / `get_latest_crop_for_level()` 根据等级返回最优/最新作物，`parse_exp_string()` 解析经验字符串，`estimate_upgrade_hint()` 计算升级预估

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
2. 在 `core/strategies/__init__.py` 中添加导出
3. 在 `core/bot_engine.py` 中: 创建策略实例 → 加入 `self._strategies` 列表
4. 如需定时调度，在 `models/config.py` 的 `get_default_tasks()` 中添加默认任务配置
5. 如需新场景，在 `core/scene_detector.py` 的 `Scene` 枚举和 `identify_scene()` 中添加
6. 如需新模板类别，在 `cv_detector.py` 的 `TEMPLATE_CATEGORIES` 中添加前缀映射
7. 如需页面导航，在 `core/ui/page.py` 中定义 Page 并加入 `ALL_PAGES`
8. 在 `gui/widgets/` 下添加对应的 UI 面板（如需要）

## Known Limitations

- **显示器比例**: 16:10 或非标准比例显示器可能存在坐标精度丢失，建议 16:9 显示器使用
- **自动买种**: 仓库库存判断存在 Bug，建议关闭该功能让用户手动购买
- **播种位置**: 播种流程仅检测仓库第一排前 5 个格子，种子必须放在该位置

## Key Design Decisions

- **纯视觉识别**: 不读取内存、不修改数据包、不调用游戏 API，仅通过屏幕截图 + 模板匹配
- **多实例**: 每个实例独立 BotEngine + 独立配置/日志/截图目录，通过 InstanceManager 统一管理
- **双模式执行**: ActionExecutor 支持 `RunMode.FOREGROUND`（pyautogui）和 `RunMode.BACKGROUND`（PostMessageW），后台模式不抢占鼠标
- **线程模型**: PyQt6 GUI 在主线程，每个实例的 BotWorker(QThread) 在独立线程执行任务，通过 Qt 信号通信
- **停止机制**: 所有策略共享 `_stop_requested` 标志，每个 click 操作前检查，支持优雅停止
- **安全措施**: 随机点击偏移（`click_offset_range`）、操作间随机延迟、pyautogui FAILSAFE、F11 老板键
- **中文路径**: `cv_detector.py` 使用 `np.fromfile` + `cv2.imdecode` 读取模板，因为 `cv2.imread` 不支持中文路径
- **Git 双仓库**: origin → Gitee, github → GitHub。发行版文件发布在 GitHub Releases（Gitee 有 100MB 限制）
- **配置同步**: `sync_features_to_tasks()` 将旧的 FeaturesConfig 开关映射到新的 TaskScheduleItemConfig.features，保持向后兼容

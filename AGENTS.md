# AGENTS.md — QQ Farm Vision Bot

基于 OpenCV 视觉识别的 QQ 经典农场（QQ/微信小程序）自动化工具。纯 Python，Windows only，`python main.py` 启动 PyQt6 GUI。当前已进入 2.0.x 多实例版本：支持后台运行、Web 控制、老板键、地块巡查、跨实例偷菜/捣乱、自动选择账号和 GitHub Actions 发布。

## Commands

```bash
pip install -r requirements.txt
python main.py                        # 启动 GUI
python tools/template_collector.py    # 模板采集（首次使用必须）
python tools/import_seeds.py          # 种子图片批量导入
pyinstaller build.spec                # 构建 EXE
```

**无 pytest** — 测试均为独立脚本（`test_*.py`），需真实游戏窗口运行，已被 .gitignore 排除。

**热键**: F9 暂停/恢复，F10 停止，F11 老板键（隐藏窗口）。鼠标移到左上角紧急停止（pyautogui FAILSAFE）。

## Architecture

数据流：`截屏 (mss) → OpenCV 多尺度模板匹配 → 场景识别状态机 → 策略决策 → ActionExecutor → 循环`

```
┌──────────────────────────────────────────────┐
│  GUI (PyQt6) + Web (FastAPI, 可选)           │
│  main_window.py / widgets/                   │
│  InstanceSidebar (多实例切换)                 │
├──────────────────────────────────────────────┤
│  策略层 (core/strategies/)                   │
│  popup → harvest → maintain → plant →        │
│  expand → task → friend → gift → targeted    │
├──────────────────────────────────────────────┤
│  图像识别层                                  │
│  cv_detector.py (模板匹配, 多尺度 0.8x~1.3x) │
│  scene_detector.py (场景识别状态机)          │
├──────────────────────────────────────────────┤
│  窗口控制 + 操作执行                         │
│  window_manager.py + screen_capture.py       │
│  action_executor.py (RunMode: 前台/后台)     │
└──────────────────────────────────────────────┘
```

### 多实例

支持同时管理多个游戏窗口。每个实例有独立的 BotEngine、配置、日志、截图目录。

- **InstanceManager** (`core/instance_manager.py`) — 管理元数据，存储在 `instances/profiles.json`
- **InstanceSession** — 封装实例 id/name/state + 路径 + 配置
- 实例目录: `instances/{id}/configs/config.json`, `instances/{id}/logs/`, `instances/{id}/screenshots/`
- MainWindow 维护 `dict[str, BotEngine]` (`_engines`)
- 无活动实例时回退到根目录 `config.json`（向后兼容）

### 主控编排 (core/bot_engine.py)

- **BotEngine** (QObject) — 初始化各层组件，每个实例一个
- **BotWorker** (QThread) — 执行 farm/friend/test_fertilize 任务
- **TaskScheduler** (QTimer) — 定时触发，含窗口存活监控
- **TaskExecutor** (`core/task_executor.py`) — 基于优先级的异步任务调度
- **CrossInstanceBus** (`core/cross_instance_bus.py`) — 跨实例偷菜/捣乱消息总线
- 主循环 `check_farm()` 最多 50 轮，3 轮空闲自动退出，每轮 sleep 0.3s
- 静默时段 (`core/silent_hours.py`) 支持跨午夜（如 22:00-06:00），期间不执行操作

### 策略模式

所有策略继承 `BaseStrategy`（`core/strategies/base.py`），共享 `cv_detector`、`action_executor`、`_capture_fn`。

BaseStrategy 提供: `click(x, y, desc)`, `find_by_name()`, `find_by_prefix_first()`, `find_any()`, `stopped` 属性。每次操作前必须检查 `self.stopped`。

| 优先级 | 文件 | 类名 | 职责 |
|--------|------|------|------|
| P-1 | `popup.py` | PopupStrategy | 关闭弹窗/商店/商城 + 升级检测 |
| P0 | `harvest.py` | HarvestStrategy | 一键收获 + 自动出售 |
| P1 | `maintain.py` | MaintainStrategy | 除草/除虫/浇水 |
| P2 | `plant.py` | PlantStrategy | 播种（动态翻页、手动指定次级回退） + 购买种子（OCR/价格/仓库格复查） + 施肥 |
| P3 | `expand.py` | ExpandStrategy | 扩建土地 |
| P3.5 | `task.py` | TaskStrategy | 领取任务奖励 / 出售果实 |
| P4 | `friend.py` | FriendStrategy | 好友巡查/帮忙/偷菜 |
| P3.6 | `gift.py` | GiftStrategy | SVIP礼包 + 商城免费 + 邮件领取 |
| 注入 | `targeted_steal.py` | TargetedStealStrategy | 跨实例定点偷菜 |
| 注入 | `targeted_prank.py` | TargetedPrankStrategy | 跨实例好友捣乱 |

### 任务系统

`TaskExecutor` 支持 interval / daily 触发、优先级队列、失败重试、启用时间段、配置热更新和跨实例动态任务注入。默认任务在 `models/config.py:get_default_tasks()` 中维护：

- `main` — 农场主流程
- `profile` — 个人信息 OCR
- `friend` — 好友巡查
- `land_scan` — 地块巡查，支持普通/红/黑/金/紫晶土地等级识别
- `timed_harvest` — 基于地块巡查倒计时执行轻量收获；依赖 `land_scan` 启用，不隐式拉起完整 `check_farm()`
- `gift` — 礼品领取
- `sell` — 仓库出售
- `task` — 任务奖励/出售入口兜底
- `fertilize` — 定时施肥
- `share` — 每日分享
- `repair` — 任务级修复；失败任务恢复只在 repair 勾选时触发，遵守 `max_repair_attempts`
- `restart` — 任务级重启；窗口/截图类错误才优先考虑，遵守 `max_restart_attempts`
- `捣乱` — 跨实例捣乱任务，注意 key 是中文

旧版 `features` 开关通过 `sync_features_to_tasks()` 同步到任务配置，保持向后兼容。
任务执行必须受 UI 勾选控制：只勾选 `land_scan` 时不能隐式触发 `main`、`repair`、`restart` 或一键务农。
`repair` / `restart` 是失败恢复许可任务：普通 interval 到点时无恢复标记必须跳过；只有 `_queue_recovery_after_failure()` 写入一次性恢复标记后才允许执行实际修复/重启。

调试模式由 `safety.debug_log_enabled` 控制。开启后每个实例写入 `instances/{id}/logs/debug_YYYY-MM-DD.log` 和 `instances/{id}/logs/task_trace_YYYY-MM-DD.jsonl`，后者用于记录 `task_start` / `task_finish` / `task_out_of_time_range` / 动态注入 / 恢复跳过等调度诊断事件。

### ActionExecutor 双模式

- `RunMode.FOREGROUND` — pyautogui，需要前台窗口，会移动鼠标
- `RunMode.BACKGROUND` — PostMessageW，不抢占鼠标，推荐

### 场景识别 (core/scene_detector.py)

Scene 枚举: FARM_OVERVIEW, FRIEND_FARM, PLOT_MENU, SEED_SELECT, SHOP_PAGE, MALL_PAGE, WAREHOUSE, BUY_CONFIRM, POPUP, LEVEL_UP, FRIEND_LIST, INFO_PAGE, REMOTE_LOGIN, UNKNOWN

`identify_scene()` 按优先级检测（REMOTE_LOGIN → INFO_PAGE → MALL_PAGE → ...）。

### Web 服务 (web/server.py, 可选)

FastAPI 控制面板：截图预览、启停控制、状态查看、日志、配置编辑。默认端口 8080。需 `fastapi+uvicorn`，通过回调函数与 BotEngine 交互。

### 近期 2.0.x 更新脉络

- v2.0.16: 跨实例捣乱、TargetedPrankStrategy、窗口持久化、中文 task key `捣乱`
- v2.0.15: GitHub Actions 自动构建发布、自定义 Release Notes、直接发布 EXE
- v2.0.14: 窗口监控新增断网/异地登录检测
- v2.0.13: 修复多实例窗口监控过滤误判
- v2.0.12: 窗口监控新增黑屏检测
- v2.0.11: 多实例窗口占用机制、模板截图绑定 hwnd
- v2.0.10: 偷菜策略优化、新增采集图标
- v2.0.9: 多实例启动自动选择账号
- v2.0.4+: 一键务农、稀有/特殊作物、特殊作物偷菜、活动作物数据

## 模板命名

前缀决定类别，新增前缀需同步更新 `cv_detector.py` 中的 `TEMPLATE_CATEGORIES`。

| 前缀 | 类别 | 示例 |
|------|------|------|
| `btn_` | button | `btn_harvest.png` |
| `bth_` | 特殊按钮（如施肥） | `bth_fertilize.png` |
| `icon_` | status_icon | `icon_mature.png` |
| `friend_` | 好友列表标识 | `friend_list.png` |
| `crop_` | crop | `crop_mature.png` |
| `seed_` | 播种列表（支持动态翻页查找） | `seed_小麦.png` |
| `shop_` | 商店卡片（自动买种模板兜底） | `shop_小麦.png` |
| `ws_` | 仓库种子 | `ws_小麦.png` |
| `land_` | land | `land_empty.png` |
| `icon_land_` | 地块等级图标 | `icon_land_amethyst.png` |
| `ui_` | ui_element | `ui_next_time.png` |

读取模板用 `np.fromfile` + `cv2.imdecode`（`cv2.imread` 不支持中文路径）。
`CVDetector` 也支持 GIF 模板，会拆成有限多帧匹配变体；例如 `icon_land_upgrade.gif` 用于地块巡查升级图标检测。

## 配置

Pydantic BaseModel 层级结构，`AppConfig.load(path)` / `.save()` 读写 JSON。GUI 修改实时生效。

每个实例独立配置: `instances/{id}/configs/config.json`。根目录 `config.json` 为兼容默认。

关键枚举: `PlantMode` (PREFERRED / BEST_EXP_RATE), `SellMode` (BATCH_ALL / SELECTIVE), `RunMode` (FOREGROUND / BACKGROUND)

## 代码风格

- **绝对导入**，`from module import Class` 优先
- Python 3.10+ 原生类型: `list[str]`, `X | None`
- 函数参数和返回值必须标注类型
- 枚举用 `str, Enum` 双重继承
- pydantic 定义配置结构，dataclass 定义简单 DTO
- 日志用 `loguru`，格式: `✓ 成功` / `✗ 失败: 原因`
- 模块首行中文 docstring

## 添加新功能

1. `core/strategies/` 新建策略，继承 `BaseStrategy`
2. `core/bot_engine.py` — 创建实例 → 加入 `self._strategies` → 主循环中按优先级调用
3. 新场景 → `scene_detector.py` 的 `Scene` 枚举 + `identify_scene()`
4. 新模板类别 → `cv_detector.py` 的 `TEMPLATE_CATEGORIES`
5. 对应 UI 面板 → `gui/widgets/`

## Known Limitations

- 16:10 或非标准比例显示器坐标精度有损，建议 16:9
- 播种列表已支持动态翻页查找目标种子，不再局限于第一页或前 5 个格子
- 自动买种当前瓶颈是商店商品定位/OCR 灵敏度不足；遇到识别不稳时建议关闭自动买种，手动补种后运行
- 仓库优先播种已存在，但不同分辨率/DPI 下仍需实测模板与坐标稳定性
- 仓库种子格扫描已接入 `utils/warehouse_seed_scan.py`；购买前/后复查会输出 `slot_index/raw_index/locked/confidence`
- `icon_item_locked` / `icon_seed_locked` 锁图标模板尚未内置采集，存在模板时会自动用于跳过锁定种子格
- OCR 依赖截图质量，选择账号、个人信息、地块巡查、买种均需在真实游戏窗口中验证；紫晶土地需要同步 `icon_land_amethyst.png` 模板
- 后台 PostMessageW 对不同 QQ/微信容器版本可能表现不一致

## Gotchas

- **OCR 依赖**: `rapidocr_onnxruntime` 用于商店买种、个人信息、地块巡查和选择账号识别；商店买种 OCR 不稳定时会回退到 `shop_` 模板匹配
- **播种翻页**: `PlantStrategy._find_seed_with_pagination()` 通过 `btn_seed_select_right.png` 动态翻页，每次翻页后重新截屏检测；该按钮模板缺失会导致误判到达末页
- **次级作物回退**: `PlantingConfig.secondary_crop` 仅在 `PlantMode.PREFERRED` 下生效；主流程先尝试 `preferred_crop`，首选尝试期间临时暂停自动买种，剩余空地再尝试 `secondary_crop`
- **买种安全阀**: 自动买种优先按 `configs/goods.json` 单价匹配商店 OCR，名称 OCR 只做同价消歧；购买前/后必须能在仓库格扫描中复查到 `ws_作物名`，否则进入冷却并停止本轮播种
- **买种二阶段边界**: 已落地仓库格子扫描；尚未落地目标仓库的“按仓库序号在播种弹窗翻页选种”，实现前不要声称已完成该链路
- **地块巡查只读**: `LandScanTask._run_pre_scan_maintain()` 必须保持 no-op，不要恢复对一键收获/一键务农的隐式调用
- **状态总览**: 仅展示运行状态、任务队列和下次执行时间；操作次数统计与每日动作统计落盘已移除
- **调试模式**: 勾选后优先查看 `task_trace_YYYY-MM-DD.jsonl` 判断任务是否被禁用、时间窗挡住、没有 runner、配置关闭或恢复链路跳过
- **构建**: PyInstaller 打包时排除 `easyocr/torch/torchvision`（见 `build.spec`），打包后 `sys._MEIPASS` 为资源目录
- **Git 双仓库**: origin → Gitee, github → GitHub。发行版发布在 GitHub Releases（Gitee 100MB 限制）
- `QT_ENABLE_HIGHDPI_SCALING=0` — main.py 中强制禁用高 DPI 缩放
- PyQt6 使用 Fusion 风格 + 强制浅色调色板，覆盖 Windows 暗色主题

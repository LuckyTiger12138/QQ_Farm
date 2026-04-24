# AGENTS.md — QQ Farm Vision Bot

基于 OpenCV 视觉识别的 QQ 经典农场（微信小程序）自动化工具。纯 Python，Windows only，`python main.py` 启动 PyQt6 GUI。

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
│  expand → task → friend → gift               │
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
| P2 | `plant.py` | PlantStrategy | 播种 + 购买种子 + 施肥 |
| P3 | `expand.py` | ExpandStrategy | 扩建土地 |
| P3.5 | `task.py` | TaskStrategy | 领取任务奖励 / 出售果实 |
| P4 | `friend.py` | FriendStrategy | 好友巡查/帮忙/偷菜 |
| P3.6 | `gift.py` | GiftStrategy | SVIP礼包 + 商城免费 + 邮件领取 |

### ActionExecutor 双模式

- `RunMode.FOREGROUND` — pyautogui，需要前台窗口，会移动鼠标
- `RunMode.BACKGROUND` — PostMessageW，不抢占鼠标，推荐

### 场景识别 (core/scene_detector.py)

Scene 枚举: FARM_OVERVIEW, FRIEND_FARM, PLOT_MENU, SEED_SELECT, SHOP_PAGE, MALL_PAGE, WAREHOUSE, BUY_CONFIRM, POPUP, LEVEL_UP, FRIEND_LIST, INFO_PAGE, REMOTE_LOGIN, UNKNOWN

`identify_scene()` 按优先级检测（REMOTE_LOGIN → INFO_PAGE → MALL_PAGE → ...）。

### Web 服务 (web/server.py, 可选)

FastAPI 控制面板：截图预览、启停控制、状态查看、日志、配置编辑。默认端口 8080。需 `fastapi+uvicorn`，通过回调函数与 BotEngine 交互。

## 模板命名

前缀决定类别，新增前缀需同步更新 `cv_detector.py` 中的 `TEMPLATE_CATEGORIES`。

| 前缀 | 类别 | 示例 |
|------|------|------|
| `btn_` | button | `btn_harvest.png` |
| `bth_` | 特殊按钮（如施肥） | `bth_fertilize.png` |
| `icon_` | status_icon | `icon_mature.png` |
| `friend_` | 好友列表标识 | `friend_list.png` |
| `crop_` | crop | `crop_mature.png` |
| `seed_` | 播种列表 | `seed_小麦.png` |
| `shop_` | 商店卡片 | `shop_小麦.png` |
| `land_` | land | `land_empty.png` |
| `ui_` | ui_element | `ui_next_time.png` |

读取模板用 `np.fromfile` + `cv2.imdecode`（`cv2.imread` 不支持中文路径）。

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
- 仓库自动买种库存判断有 Bug，建议关闭让用户手动购买
- 播种仅检测仓库第一排前 5 个格子，种子必须放在该位置

## Gotchas

- **OCR 依赖**: `rapidocr_onnxruntime` 用于商店买种识别，是可选依赖
- **构建**: PyInstaller 打包时排除 `easyocr/torch/torchvision`（见 `build.spec`），打包后 `sys._MEIPASS` 为资源目录
- **Git 双仓库**: origin → Gitee, github → GitHub。发行版发布在 GitHub Releases（Gitee 100MB 限制）
- `QT_ENABLE_HIGHDPI_SCALING=0` — main.py 中强制禁用高 DPI 缩放
- PyQt6 使用 Fusion 风格 + 强制浅色调色板，覆盖 Windows 暗色主题

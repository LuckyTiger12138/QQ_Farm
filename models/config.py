"""应用配置模型"""
import json
import os
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunMode(str, Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"


class WindowPosition(str, Enum):
    """窗口在屏幕中的位置位置枚举"""
    TOP_LEFT = "top_left"        # 左上角
    TOP_RIGHT = "top_right"      # 右上角
    BOTTOM_LEFT = "bottom_left"  # 左下角（默认）
    BOTTOM_RIGHT = "bottom_right" # 右下角
    CENTER = "center"            # 居中


class PlantMode(str, Enum):
    PREFERRED = "preferred"          # 用户手动指定作物
    BEST_EXP_RATE = "best_exp_rate"  # 当前等级下单位时间经验最高
    LATEST_LEVEL = "latest_level"    # 当前等级下可种植的最高等级作物


class SellConfig(BaseModel):
    """出售配置 — 仅支持批量全部出售"""


class FriendConfig(BaseModel):
    enable_steal: bool = True       # 是否偷菜
    enable_weed: bool = True        # 是否帮忙除草
    enable_water: bool = True       # 是否帮忙浇水
    enable_bug: bool = True         # 是否帮忙除虫
    max_steal_per_round: int = 0    # 每轮偷菜次数上限（0=无限制）
    blacklist: list[str] = []       # 好友黑名单（前缀匹配）
    enable_accept_request: bool = True  # 自动同意好友请求


class FeaturesConfig(BaseModel):
    auto_harvest: bool = True
    auto_plant: bool = True
    auto_buy_seed: bool = True
    auto_weed: bool = True
    auto_water: bool = True
    auto_bug: bool = True
    auto_fertilize: bool = True
    auto_task: bool = False
    auto_upgrade: bool = False
    auto_svip_gift: bool = False     # QQSVIP礼包领取
    auto_mall_gift: bool = False     # 商城免费领取
    auto_mail: bool = False          # 邮件领取
    friend: FriendConfig = Field(default_factory=FriendConfig)  # 好友操作配置


class SellConfig(BaseModel):
    """出售配置 — 仅支持批量全部出售"""


class SafetyConfig(BaseModel):
    random_delay_min: float = 0.1
    random_delay_max: float = 0.3
    click_offset_range: int = 5
    max_actions_per_round: int = 20
    run_mode: RunMode = RunMode.BACKGROUND
    window_position: WindowPosition = WindowPosition.BOTTOM_LEFT  # 窗口位置
    auto_remote_login: bool = False  # 掉线重登（默认关闭，多实例下不建议开启）
    debug_log_enabled: bool = False  # 调试日志开关


class ScreenshotConfig(BaseModel):
    quality: int = 80
    save_history: bool = True
    max_history_count: int = 50
    capture_interval_seconds: float = 0.3  # 连续截图频率限制


class ScheduleConfig(BaseModel):
    farm_check_seconds: int = 120    # 农场巡查间隔（秒）
    friend_check_seconds: int = 300  # 好友巡查间隔（秒）
    task_check_minutes: int = 60     # 任务检查间隔（分钟）


class SilentHoursConfig(BaseModel):
    enabled: bool = False
    start_hour: int = 2     # 0-23
    start_minute: int = 0   # 0-59
    end_hour: int = 6       # 0-23
    end_minute: int = 0     # 0-59


class WebConfig(BaseModel):
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8080


class TaskTriggerType(str, Enum):
    INTERVAL = "interval"
    DAILY = "daily"


DEFAULT_TASK_ENABLED_TIME_RANGE = "00:00:00-23:59:59"


class TaskScheduleItemConfig(BaseModel):
    """单个任务的调度配置"""
    enabled: bool = True
    priority: int = 10
    trigger: TaskTriggerType = TaskTriggerType.INTERVAL
    interval_seconds: int = 120        # 成功后间隔
    enabled_time_range: str = DEFAULT_TASK_ENABLED_TIME_RANGE
    daily_time: str = "00:01"
    next_run: str = ""                 # ISO 格式 datetime 字符串
    failure_interval_seconds: int = 60  # 失败后间隔
    features: dict[str, Any] = {}


class ExecutorConfig(BaseModel):
    """任务执行器全局配置"""
    min_task_interval_seconds: int = 5
    empty_queue_policy: str = "stay"    # stay / goto_main
    default_success_interval: int = 120
    default_failure_interval: int = 60
    max_failures: int = 3


def normalize_task_enabled_time_range(value: str) -> str:
    """规范化时段字符串为 HH:MM:SS-HH:MM:SS 格式"""
    if not value:
        return DEFAULT_TASK_ENABLED_TIME_RANGE
    value = value.strip()
    pattern = r'^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*-\s*(\d{1,2}):(\d{2})(?::(\d{2}))?$'
    m = re.match(pattern, value)
    if not m:
        return DEFAULT_TASK_ENABLED_TIME_RANGE
    h1, m1, s1, h2, m2, s2 = m.groups()
    s1 = s1 or "00"
    s2 = s2 or "00"
    return f"{int(h1):02d}:{m1}:{s1}-{int(h2):02d}:{m2}:{s2}"


def resolve_task_min_interval_seconds(executor_cfg: ExecutorConfig) -> int:
    """返回任务最小间隔秒数"""
    return max(1, executor_cfg.min_task_interval_seconds)


class PlantingConfig(BaseModel):
    strategy: PlantMode = PlantMode.PREFERRED
    preferred_crop: str = "椰子"  # strategy=preferred 时使用
    player_level: int = 69
    window_width: int = 581
    window_height: int = 1054
    game_shortcut_path: str = ""  # 游戏快捷方式路径，用于自动启动
    warehouse_first: bool = True          # 仓库优先播种
    skip_event_crops: bool = False        # 跳过活动作物
    level_ocr_enabled: bool = True        # 等级OCR开关
    window_platform: str = "qq"           # qq/wechat
    planting_stable_seconds: float = 0.5  # 播种画面稳定等待时间
    planting_stable_timeout_seconds: float = 5.0  # 播种稳定超时


class CrossInstancePartnerConfig(BaseModel):
    """大小号通讯配对配置"""
    instance_id: str = ""           # 目标实例ID
    friend_name: str = ""           # 对应好友昵称（OCR识别用）
    enabled: bool = True            # 是否启用该配对


class CrossInstanceConfig(BaseModel):
    """大小号通讯功能配置"""
    enabled: bool = False                  # 总开关
    send_alerts: bool = True               # 是否发送成熟通知
    accept_steal: bool = True              # 是否接收偷菜任务
    alert_threshold_seconds: int = 300     # 成熟阈值（默认5分钟）
    partners: list[CrossInstancePartnerConfig] = Field(default_factory=list)


class LandProfileConfig(BaseModel):
    level: int = 0
    gold: str = ""
    coupon: str = ""
    exp: str = ""


class LandConfig(BaseModel):
    profile: LandProfileConfig = Field(default_factory=LandProfileConfig)
    plots: list[dict] = []  # [{plot_id, level, maturity_countdown, need_upgrade, need_planting}]


class AppConfig(BaseModel):
    window_title_keyword: str = "QQ经典农场"
    window_select_rule: str = "auto"  # 'auto' 或 'index:N'
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    screenshot: ScreenshotConfig = Field(default_factory=ScreenshotConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    planting: PlantingConfig = Field(default_factory=PlantingConfig)
    sell: SellConfig = Field(default_factory=SellConfig)
    silent_hours: SilentHoursConfig = Field(default_factory=SilentHoursConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    land: LandConfig = Field(default_factory=LandConfig)
    tasks: dict[str, TaskScheduleItemConfig] = Field(default_factory=dict)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    cross_instance: CrossInstanceConfig = Field(default_factory=CrossInstanceConfig)

    _config_path: str = ""

    @classmethod
    def load(cls, path: str = "config.json") -> "AppConfig":
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            config = cls(**data)
        else:
            config = cls()
        config._config_path = path
        config.ensure_default_tasks()
        config.sync_features_to_tasks()
        return config

    def ensure_default_tasks(self):
        """确保 tasks 中包含所有默认任务（旧配置迁移用）"""
        defaults = self.get_default_tasks()
        added = [name for name in defaults if name not in self.tasks]
        for name in added:
            self.tasks[name] = defaults[name]
        if added:
            from loguru import logger
            logger.debug(f"已补充默认任务: {added}")

    def sync_features_to_tasks(self):
        """将 FeaturesConfig 开关同步到 tasks.features（向后兼容）

        同步后会清除不在已知键列表中的残留键（如 auto_bad、steal_stats），
        防止 GUI 显示无效配置项。
        """
        f = self.features

        main_features = {
            "auto_harvest": f.auto_harvest,
            "auto_plant": f.auto_plant,
            "auto_weed": f.auto_weed,
            "auto_water": f.auto_water,
            "auto_bug": f.auto_bug,
            "auto_expand": f.auto_upgrade,
            "auto_upgrade": f.auto_upgrade,
            "auto_buy_seed": f.auto_buy_seed,
            "auto_fertilize": f.auto_fertilize,
            "auto_remote_login": self.safety.auto_remote_login,
        }
        if "main" in self.tasks:
            self.tasks["main"].features = main_features

        friend_features = {
            "auto_steal": f.friend.enable_steal,
            "auto_weed": f.friend.enable_weed,
            "auto_water": f.friend.enable_water,
            "auto_bug": f.friend.enable_bug,
            "blacklist": f.friend.blacklist,
        }
        if "friend" in self.tasks:
            self.tasks["friend"].features = friend_features
        if "gift" in self.tasks:
            self.tasks["gift"].features.update({
                "auto_svip_gift": f.auto_svip_gift,
                "auto_mall_gift": f.auto_mall_gift,
                "auto_mail": f.auto_mail,
            })
            # 有任一开关开启则启用任务
            self.tasks["gift"].enabled = any([f.auto_svip_gift, f.auto_mall_gift, f.auto_mail])
        if "task" in self.tasks:
            self.tasks["task"].enabled = f.auto_task
        if "fertilize" in self.tasks:
            self.tasks["fertilize"].features = {}


    @staticmethod
    def get_default_tasks() -> dict[str, TaskScheduleItemConfig]:
        """获取默认任务配置"""
        return {
            "main": TaskScheduleItemConfig(
                enabled=True, priority=10, trigger=TaskTriggerType.INTERVAL,
                interval_seconds=60, failure_interval_seconds=30,
            ),
            "profile": TaskScheduleItemConfig(
                enabled=True, priority=8, trigger=TaskTriggerType.INTERVAL,
                interval_seconds=300, failure_interval_seconds=60,
            ),
            "friend": TaskScheduleItemConfig(
                enabled=True, priority=20, trigger=TaskTriggerType.INTERVAL,
                interval_seconds=1800, failure_interval_seconds=300,
            ),
            "land_scan": TaskScheduleItemConfig(
                enabled=True, priority=15, trigger=TaskTriggerType.INTERVAL,
                interval_seconds=300, failure_interval_seconds=120,
            ),
            "gift": TaskScheduleItemConfig(
                enabled=False, priority=30, trigger=TaskTriggerType.DAILY,
                daily_time="00:05",
            ),
            "sell": TaskScheduleItemConfig(
                enabled=False, priority=25, trigger=TaskTriggerType.INTERVAL,
                interval_seconds=21600, failure_interval_seconds=3600,
            ),
            "task": TaskScheduleItemConfig(
                enabled=False, priority=25, trigger=TaskTriggerType.INTERVAL,
                interval_seconds=21600, failure_interval_seconds=3600,
            ),
            "fertilize": TaskScheduleItemConfig(
                enabled=False, priority=25, trigger=TaskTriggerType.INTERVAL,
                interval_seconds=7200, failure_interval_seconds=600,
            ),
            "share": TaskScheduleItemConfig(
                enabled=False, priority=30, trigger=TaskTriggerType.DAILY,
                daily_time="00:10",
            ),
        }

    def save(self, path: str | None = None):
        p = path or self._config_path or "config.json"
        from loguru import logger
        logger.info(f"🔒 AppConfig.save: id={id(self)} → {p} | features.auto_harvest={self.features.auto_harvest}")
        # 排除 machine-specific 配置（不同电脑路径不同）
        data = self.model_dump(exclude={"planting": {"game_shortcut_path"}})
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

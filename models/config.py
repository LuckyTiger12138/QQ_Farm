"""应用配置模型"""
import json
import os
from enum import Enum
from pydantic import BaseModel, Field


class RunMode(str, Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"


class PlantMode(str, Enum):
    PREFERRED = "preferred"          # 用户手动指定作物
    BEST_EXP_RATE = "best_exp_rate"  # 当前等级下单位时间经验最高


class SellMode(str, Enum):
    BATCH_ALL = "batch_all"        # 批量全部出售
    SELECTIVE = "selective"        # 选择性出售（只卖勾选的作物）


class FriendConfig(BaseModel):
    enable_steal: bool = True       # 是否偷菜
    enable_weed: bool = True        # 是否帮忙除草
    enable_water: bool = True       # 是否帮忙浇水
    enable_bug: bool = True         # 是否帮忙除虫
    max_steal_per_round: int = 0    # 每轮偷菜次数上限（0=无限制）


class FeaturesConfig(BaseModel):
    auto_harvest: bool = True
    auto_plant: bool = True
    auto_buy_seed: bool = True
    auto_weed: bool = True
    auto_water: bool = True
    auto_bug: bool = True
    auto_fertilize: bool = True
    auto_sell: bool = False
    auto_bad: bool = False
    auto_task: bool = False
    auto_upgrade: bool = False
    friend: FriendConfig = Field(default_factory=FriendConfig)  # 好友操作配置


class SellConfig(BaseModel):
    mode: SellMode = SellMode.BATCH_ALL
    sell_crops: list[str] = []  # selective 模式下要出售的作物名称列表


class SafetyConfig(BaseModel):
    random_delay_min: float = 0.1
    random_delay_max: float = 0.3
    click_offset_range: int = 5
    max_actions_per_round: int = 20
    run_mode: RunMode = RunMode.BACKGROUND


class ScreenshotConfig(BaseModel):
    quality: int = 80
    save_history: bool = True
    max_history_count: int = 50


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


class PlantingConfig(BaseModel):
    strategy: PlantMode = PlantMode.PREFERRED
    preferred_crop: str = "椰子"  # strategy=preferred 时使用
    player_level: int = 69
    window_width: int = 581
    window_height: int = 1054
    game_shortcut_path: str = ""  # 游戏快捷方式路径，用于自动启动


class AppConfig(BaseModel):
    window_title_keyword: str = "QQ经典农场"
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    screenshot: ScreenshotConfig = Field(default_factory=ScreenshotConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    planting: PlantingConfig = Field(default_factory=PlantingConfig)
    sell: SellConfig = Field(default_factory=SellConfig)
    silent_hours: SilentHoursConfig = Field(default_factory=SilentHoursConfig)
    web: WebConfig = Field(default_factory=WebConfig)

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
        return config

    def save(self, path: str | None = None):
        p = path or self._config_path or "config.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(), f, ensure_ascii=False, indent=2)

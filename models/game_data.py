"""游戏数据 - 作物信息、等级经验等静态数据

从 configs/plants.json 动态加载，正确处理双季作物（seasons==2）：
- 生长时间 = 所有阶段之和 + 双季额外时间（最后两个非零阶段）
- 经验 = 基础经验 × 2（双季翻倍）
"""

import json
import math
import os


def _parse_grow_phases_seconds(grow_phases: str) -> list[int]:
    """Parse `种子:30;发芽:30;成熟:0;` into [30, 30, 0]."""
    phases: list[int] = []
    for part in (grow_phases or '').split(';'):
        part = part.strip()
        if not part or ':' not in part:
            continue
        _, sec_str = part.split(':', 1)
        sec_str = sec_str.strip()
        try:
            sec = int(float(sec_str))
        except ValueError:
            continue
        if sec < 0:
            continue
        phases.append(sec)
    return phases


def _calc_grow_time_seconds(grow_phases: str, seasons: int) -> int:
    """Compute total grow time.

    Rules:
    1. Base grow time = sum of all phase seconds.
    2. For dual-season crops (seasons == 2), add the last two non-zero phases.
    """
    phases = _parse_grow_phases_seconds(grow_phases)
    total = sum(phases)

    if seasons == 2:
        non_zero = [s for s in phases if s > 0]
        if len(non_zero) >= 2:
            total += non_zero[-1] + non_zero[-2]
        elif len(non_zero) == 1:
            total += non_zero[-1]
    return total


# 作物 seasons 查询表（name → seasons）
_CROP_SEASONS: dict[str, int] = {}


def _load_crops_from_plant_json() -> list[tuple]:
    """Build CROPS tuple list from configs/plants.json.

    Tuple format:
      (name, seed_id, land_level_need, grow_time_seconds, exp, fruit_count)
    """
    global _CROP_SEASONS
    json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'configs', 'plants.json')

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"作物数据文件不存在: {json_path}")

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    crops: list[tuple] = []
    seasons_map: dict[str, int] = {}
    for item in data:
        name = str(item.get('name', '')).strip()
        if not name:
            continue

        seed_id = int(item.get('seed_id', 0))
        land_level_need = int(item.get('land_level_need', 0))
        seasons = int(item.get('seasons', 1))
        grow_phases = str(item.get('grow_phases', ''))
        grow_time = _calc_grow_time_seconds(grow_phases, seasons)

        exp = int(item.get('exp', 0))
        if seasons == 2:
            exp *= 2

        fruit = item.get('fruit', {}) or {}
        fruit_count = int(fruit.get('count', 0))

        crops.append((name, seed_id, land_level_need, grow_time, exp, fruit_count))
        seasons_map[name] = seasons

    crops.sort(key=lambda c: (c[2], c[1], c[0]))
    _CROP_SEASONS = seasons_map
    return crops


# 作物数据表：(名称, 种子ID, 解锁等级, 总生长时间秒, 经验, 果实数量)
CROPS = _load_crops_from_plant_json()


def get_crop_names() -> list[str]:
    """获取所有作物名称列表"""
    return [c[0] for c in CROPS]


def get_crops_for_level(level: int) -> list[tuple]:
    """获取指定等级可种植的作物"""
    return [c for c in CROPS if c[2] <= level]


def get_crop_by_name(name: str) -> tuple | None:
    """根据名称查找作物"""
    for c in CROPS:
        if c[0] == name:
            return c
    return None


def get_best_crop_for_level(level: int) -> tuple | None:
    """获取当前等级下单位时间经验最高的作物

    计算公式：经验 / 生长时间（秒），值越大效率越高。
    """
    available = get_crops_for_level(level)
    if not available:
        return None
    return max(available, key=lambda c: c[4] / c[3])


def get_latest_crop_for_level(level: int) -> tuple | None:
    """获取当前等级下可种植的最高等级（解锁等级最高）作物"""
    available = get_crops_for_level(level)
    if not available:
        return None
    return max(available, key=lambda c: c[2])


def get_crop_index_in_list(name: str, level: int) -> int:
    """获取指定作物在当前等级可种列表中的位置索引（从0开始）

    游戏中点击空地后弹出的种子列表是按解锁等级排序的。
    返回该作物在列表中的位置，用于相对位置点击。
    返回 -1 表示未找到。
    """
    available = get_crops_for_level(level)
    for i, c in enumerate(available):
        if c[0] == name:
            return i
    return -1


def format_grow_time(seconds: int) -> str:
    """格式化生长时间"""
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分钟"
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    return f"{hours}小时{mins}分" if mins else f"{hours}小时"


def get_crop_display_info() -> list[str]:
    """获取作物显示信息列表，用于下拉框"""
    items = []
    for name, _, level, grow_time, exp, _ in CROPS:
        time_str = format_grow_time(grow_time)
        items.append(f"{name} (Lv{level}, {time_str}, {exp}经验)")
    return items


def get_crop_seasons(name: str) -> int:
    """获取作物的季节数（1=单季, 2=双季）。"""
    return _CROP_SEASONS.get(name, 1)


def parse_exp_string(exp_str: str) -> tuple[float, float]:
    """解析 OCR 经验字符串为 (当前经验, 升级所需经验)。

    支持格式:
      "1234/5000"        → (1234, 5000)
      "1.2万/3.4万"      → (12000, 34000)
      "1.5亿/3亿"        → (150000000, 300000000)
    """
    raw = str(exp_str or '').strip()
    if not raw or '/' not in raw:
        return 0.0, 0.0

    parts = raw.split('/', 1)

    def _parse_number(s: str) -> float:
        s = s.strip()
        if not s:
            return 0.0
        multiplier = 1.0
        if s.endswith('亿'):
            multiplier = 1_0000_0000
            s = s[:-1]
        elif s.endswith('万'):
            multiplier = 1_0000
            s = s[:-1]
        try:
            return float(s) * multiplier
        except ValueError:
            return 0.0

    return _parse_number(parts[0]), _parse_number(parts[1])


def estimate_upgrade_hint(
    exp_str: str,
    crop_name: str,
    plots: list[dict],
) -> str:
    """计算升级预估信息。

    Args:
        exp_str: OCR 识别的经验字符串，如 "1234/5000"
        crop_name: 当前选定的作物名称
        plots: 地块数据列表 (config.land.plots)

    Returns:
        预估字符串，如 "种 4 次 (竹笋, 单季, 24块地×476经验/季, 共45696经验, 预计16小时)"
        数据不足时返回 "--"
        已可升级时返回 "可升级"
    """
    current, needed = parse_exp_string(exp_str)
    if needed <= 0:
        return "--"

    remaining = needed - current
    if remaining <= 0:
        return "可升级"

    crop = get_crop_by_name(crop_name)
    if not crop:
        return "--"

    name, _, _, grow_time, exp_per_harvest, _ = crop
    if exp_per_harvest <= 0:
        return "--"

    active_plots = sum(
        1 for p in (plots or [])
        if isinstance(p, dict) and p.get('level', 'unbuilt') != 'unbuilt'
    )
    if active_plots <= 0:
        return "--"

    seasons = get_crop_seasons(name)
    season_label = "双季" if seasons == 2 else "单季"

    exp_per_round = exp_per_harvest * active_plots
    cycles = math.ceil(remaining / exp_per_round)
    total_exp = cycles * exp_per_round
    total_seconds = cycles * grow_time
    time_str = format_grow_time(total_seconds) if total_seconds > 0 else "--"

    return (
        f"种 {cycles} 次 ({name}, {season_label}, "
        f"{active_plots}块地×{exp_per_harvest}经验/季, "
        f"共{total_exp}经验, 预计{time_str})"
    )

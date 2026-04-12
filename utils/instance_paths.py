"""实例目录与元数据管理工具

为每个实例提供独立的目录结构：
  instances/{instance_id}/
    configs/config.json
    logs/
    screenshots/
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_VALID_NAME_RE = re.compile(r'[^a-zA-Z0-9\u4e00-\u9fff_-]+')


def _get_base_instances_dir() -> Path:
    """获取实例根目录：程序运行目录下的 instances/"""
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent.parent
    return base / "instances"


@dataclass(frozen=True)
class InstancePaths:
    """实例路径集合"""

    instance_id: str
    base_dir: Path
    configs_dir: Path
    config_file: Path
    logs_dir: Path
    screenshots_dir: Path

    @staticmethod
    def from_instance_id(instance_id: str) -> 'InstancePaths':
        """从实例 id 构建路径对象"""
        iid = str(instance_id or 'default').strip() or 'default'
        base = _get_base_instances_dir() / iid
        return InstancePaths(
            instance_id=iid,
            base_dir=base,
            configs_dir=base / "configs",
            config_file=base / "configs" / "config.json",
            logs_dir=base / "logs",
            screenshots_dir=base / "screenshots",
        )


def sanitize_instance_name(name: str) -> str:
    """标准化实例名：移除非法字符，保留字母、数字、中文、下划线、连字符"""
    raw = str(name or '').strip()
    if not raw:
        return 'default'
    text = _VALID_NAME_RE.sub('-', raw).strip('-_')
    return text or 'default'


def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """以原子替换方式写入 JSON"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f'.tmp.{os.getpid()}')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _profiles_meta_file() -> Path:
    """实例元数据文件路径：instances/profiles.json"""
    return _get_base_instances_dir() / "profiles.json"


def _default_instance_meta(instance_id: str = 'default', *, name: str | None = None) -> dict[str, Any]:
    """默认实例元数据"""
    iid = sanitize_instance_name(instance_id)
    return {
        'id': iid,
        'name': str(name or iid),
        'created_at': _now_iso(),
        'updated_at': _now_iso(),
    }


def _default_profiles_meta() -> dict[str, Any]:
    """默认实例元数据集合"""
    return {
        'active_instance_id': 'default',
        'instances': [_default_instance_meta('default', name='default')],
    }


def ensure_instance_layout(instance_id: str) -> InstancePaths:
    """确保实例目录结构存在，如不存在则创建并初始化"""
    paths = InstancePaths.from_instance_id(instance_id)
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    paths.configs_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.screenshots_dir.mkdir(parents=True, exist_ok=True)

    # 如果配置文件不存在，创建空配置
    if not paths.config_file.exists():
        _atomic_write_json(paths.config_file, {})

    return paths


def load_profiles_meta() -> dict[str, Any]:
    """加载实例元数据，不存在则初始化"""
    meta_file = _profiles_meta_file()
    _get_base_instances_dir().mkdir(parents=True, exist_ok=True)

    data = _read_json(meta_file)
    if not data:
        data = _default_profiles_meta()
        _atomic_write_json(meta_file, data)

    instances = data.get('instances')
    if not isinstance(instances, list) or not instances:
        data = _default_profiles_meta()

    # 规范化实例列表
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data.get('instances', []):
        if not isinstance(item, dict):
            continue
        iid = sanitize_instance_name(item.get('id', ''))
        if not iid or iid in seen:
            continue
        seen.add(iid)
        name = str(item.get('name') or iid)
        created_at = str(item.get('created_at') or _now_iso())
        updated_at = str(item.get('updated_at') or _now_iso())
        normalized.append({'id': iid, 'name': name, 'created_at': created_at, 'updated_at': updated_at})

    if not normalized:
        normalized = [_default_instance_meta('default', name='default')]

    # 确保活动实例有效
    active = sanitize_instance_name(data.get('active_instance_id', ''))
    if active not in {item['id'] for item in normalized}:
        active = normalized[0]['id']

    output = {
        'active_instance_id': active,
        'instances': normalized,
    }
    save_profiles_meta(output)

    # 确保所有实例目录存在
    for item in output['instances']:
        ensure_instance_layout(item['id'])

    return output


def save_profiles_meta(meta: dict[str, Any]) -> None:
    """保存实例元数据"""
    data = {
        'active_instance_id': str(meta.get('active_instance_id') or 'default'),
        'instances': list(meta.get('instances') or []),
    }
    _atomic_write_json(_profiles_meta_file(), data)


def list_instances(meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """返回实例列表"""
    data = meta or load_profiles_meta()
    return [dict(item) for item in data.get('instances', []) if isinstance(item, dict)]


def create_instance(instance_id: str, *, name: str | None = None) -> dict[str, Any]:
    """创建实例目录并返回元数据条目"""
    iid = sanitize_instance_name(instance_id)
    paths = ensure_instance_layout(iid)
    now = _now_iso()
    return {
        'id': iid,
        'name': str(name or iid),
        'created_at': now,
        'updated_at': now,
    }


def clone_instance(source_id: str, target_id: str, *, target_name: str | None = None) -> dict[str, Any]:
    """克隆实例配置到新实例"""
    src = InstancePaths.from_instance_id(source_id)
    if not src.base_dir.exists():
        raise FileNotFoundError(f'源实例不存在: {source_id}')

    target_meta = create_instance(target_id, name=target_name)
    dst = InstancePaths.from_instance_id(target_meta['id'])

    # 复制配置文件
    if src.config_file.exists():
        shutil.copy2(src.config_file, dst.config_file)

    return target_meta


def rename_instance(old_id: str, new_id: str, *, new_name: str | None = None) -> dict[str, Any]:
    """重命名实例目录"""
    old_iid = sanitize_instance_name(old_id)
    new_iid = sanitize_instance_name(new_id)

    if old_iid == new_iid:
        return {
            'id': old_iid,
            'name': str(new_name or old_iid),
            'updated_at': _now_iso(),
        }

    src = _get_base_instances_dir() / old_iid
    dst = _get_base_instances_dir() / new_iid

    if not src.exists():
        raise FileNotFoundError(f'实例不存在: {old_iid}')
    if dst.exists():
        raise FileExistsError(f'实例已存在: {new_iid}')

    shutil.move(str(src), str(dst))
    ensure_instance_layout(new_iid)

    return {
        'id': new_iid,
        'name': str(new_name or new_iid),
        'updated_at': _now_iso(),
    }


def delete_instance(instance_id: str) -> None:
    """删除实例目录"""
    iid = sanitize_instance_name(instance_id)
    base = _get_base_instances_dir() / iid
    if base.exists():
        shutil.rmtree(base)




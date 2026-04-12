"""实例会话与实例元数据管理

每个实例对应：
  - 独立的配置文件 (instances/{id}/configs/config.json)
  - 独立的日志目录 (instances/{id}/logs/)
  - 独立的截图目录 (instances/{id}/screenshots/)
  - 独立的 BotEngine 运行状态
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from models.config import AppConfig
from utils.instance_paths import (
    InstancePaths,
    clone_instance,
    create_instance,
    delete_instance,
    list_instances,
    load_profiles_meta,
    rename_instance,
    sanitize_instance_name,
    save_profiles_meta,
)


@dataclass
class InstanceSession:
    """实例会话：封装单个实例的元数据、路径和配置"""

    instance_id: str
    name: str
    paths: InstancePaths
    config: AppConfig
    state: str = 'idle'  # idle, running, paused, error

    def touch(self) -> None:
        """更新时间戳"""
        # 注意：当前元数据中未存储 created_at/updated_at，预留接口
        pass

    def to_meta(self) -> dict[str, Any]:
        """转换为元数据字典"""
        return {
            'id': self.instance_id,
            'name': self.name,
        }


class InstanceManager:
    """实例管理器：管理所有实例的元数据、配置和会话"""

    def __init__(self):
        self._sessions: list[InstanceSession] = []
        self._active_instance_id: str = 'default'

    def load(self) -> None:
        """加载实例元数据与配置"""
        meta = load_profiles_meta()
        self._sessions.clear()

        for item in list_instances(meta):
            iid = sanitize_instance_name(item.get('id', ''))
            name = str(item.get('name') or iid)
            session = self._build_session(iid, name)
            self._sessions.append(session)

        # 加载活动实例 ID
        self._active_instance_id = sanitize_instance_name(meta.get('active_instance_id', ''))

        # 确保活动实例有效
        if self._sessions:
            active_cf = self._active_instance_id.casefold()
            matched = next((s.instance_id for s in self._sessions if s.instance_id.casefold() == active_cf), '')
            if not matched:
                self._active_instance_id = self._sessions[0].instance_id
                self.save()

    def save(self) -> None:
        """保存实例元数据"""
        save_profiles_meta({
            'active_instance_id': self._active_instance_id,
            'instances': [session.to_meta() for session in self._sessions],
        })

    def _build_session(self, instance_id: str, name: str) -> InstanceSession:
        """从实例 ID 和名称构建会话对象"""
        paths = InstancePaths.from_instance_id(instance_id)
        cfg = AppConfig.load(str(paths.config_file))
        return InstanceSession(
            instance_id=instance_id,
            name=name,
            paths=paths,
            config=cfg,
        )

    def _ensure_unique_id(self, preferred: str) -> str:
        """确保实例 ID 唯一，如冲突则追加数字"""
        base = sanitize_instance_name(preferred)
        existing = {session.instance_id.casefold() for session in self._sessions}
        if base.casefold() not in existing:
            return base
        for idx in range(2, 10_000):
            candidate = f'{base}{idx}'
            if candidate.casefold() not in existing:
                return candidate
        raise RuntimeError('无法分配唯一的实例 ID')

    def iter_sessions(self) -> list[InstanceSession]:
        """返回所有实例会话列表"""
        return list(self._sessions)

    def get_session(self, instance_id: str) -> InstanceSession | None:
        """根据实例 ID 获取会话"""
        iid = sanitize_instance_name(instance_id)
        iid_cf = iid.casefold()
        for session in self._sessions:
            if session.instance_id.casefold() == iid_cf:
                return session
        return None

    def get_active(self) -> InstanceSession | None:
        """获取当前活动实例会话"""
        return self.get_session(self._active_instance_id)

    def switch_active(self, instance_id: str) -> InstanceSession:
        """切换当前活动实例"""
        session = self.get_session(instance_id)
        if session is None:
            raise KeyError(f'实例不存在: {instance_id}')
        self._active_instance_id = session.instance_id
        self.save()
        return session

    def create_instance(self, name: str) -> InstanceSession:
        """创建新实例"""
        target_id = self._ensure_unique_id(name)
        meta = create_instance(target_id, name=name)
        session = self._build_session(meta['id'], str(meta['name']))
        self._sessions.append(session)
        self._active_instance_id = session.instance_id
        self.save()
        return session

    def clone_instance(self, source_instance_id: str, target_name: str) -> InstanceSession:
        """克隆实例"""
        source = self.get_session(source_instance_id)
        if source is None:
            raise KeyError(f'源实例不存在: {source_instance_id}')
        target_id = self._ensure_unique_id(target_name)
        meta = clone_instance(source.instance_id, target_id, target_name=target_name)
        session = self._build_session(meta['id'], str(meta['name']))
        self._sessions.append(session)
        self._active_instance_id = session.instance_id
        self.save()
        return session

    def rename_instance(self, instance_id: str, new_name: str) -> InstanceSession:
        """重命名实例"""
        session = self.get_session(instance_id)
        if session is None:
            raise KeyError(f'实例不存在: {instance_id}')

        candidate = sanitize_instance_name(new_name)

        # 如果新名和当前 ID 相同（忽略大小写），只更新显示名称
        if candidate.casefold() == session.instance_id.casefold():
            session.name = str(new_name or session.instance_id)
            session.touch()
            self.save()
            return session

        # 检查新 ID 是否与其他实例冲突
        existing = {
            item.instance_id.casefold()
            for item in self._sessions
            if item.instance_id.casefold() != session.instance_id.casefold()
        }
        new_id = candidate
        if new_id.casefold() in existing:
            for idx in range(2, 10_000):
                alt = f'{candidate}{idx}'
                if alt.casefold() not in existing:
                    new_id = alt
                    break
            else:
                raise RuntimeError('无法分配唯一的实例 ID')

        # 重命名目录
        meta = rename_instance(session.instance_id, new_id, new_name=new_name)
        session.instance_id = str(meta['id'])
        session.name = str(meta['name'])
        session.paths = InstancePaths.from_instance_id(session.instance_id)
        session.config = AppConfig.load(str(session.paths.config_file))
        session.touch()

        # 更新活动实例 ID（如果受影响）
        if self._active_instance_id == sanitize_instance_name(instance_id):
            self._active_instance_id = session.instance_id
        self.save()
        return session

    def delete_instance(self, instance_id: str) -> None:
        """删除实例"""
        session = self.get_session(instance_id)
        if session is None:
            raise KeyError(f'实例不存在: {instance_id}')
        if len(self._sessions) <= 1:
            raise ValueError('不能删除最后一个实例')

        self._sessions = [item for item in self._sessions if item.instance_id != session.instance_id]
        delete_instance(session.instance_id)

        if self._active_instance_id == session.instance_id:
            self._active_instance_id = self._sessions[0].instance_id
        self.save()

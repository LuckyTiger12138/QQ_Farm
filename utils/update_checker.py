"""基于 GitHub Release 的更新检查。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(slots=True)
class UpdateCheckResult:
    ok: bool
    has_update: bool
    current_version: str
    latest_version: str
    latest_tag: str
    release_url: str
    download_url: str
    message: str


def _normalize_version_text(raw: str) -> str:
    text = str(raw or '').strip()
    if text[:1].lower() == 'v':
        text = text[1:].strip()
    return text or '0'


def _version_segments(raw: str) -> list[int]:
    text = _normalize_version_text(raw)
    core = text.split('-', 1)[0].split('+', 1)[0]
    segments: list[int] = []
    for part in core.split('.'):
        item = str(part or '').strip()
        if not item:
            segments.append(0)
            continue
        if item.isdigit():
            segments.append(int(item))
            continue
        match = re.match(r'^(\d+)', item)
        segments.append(int(match.group(1)) if match else 0)
    if not segments:
        digits = re.findall(r'\d+', core)
        segments = [int(v) for v in digits] if digits else [0]
    while len(segments) > 1 and segments[-1] == 0:
        segments.pop()
    return segments


def _is_remote_newer(current_version: str, latest_version: str) -> bool:
    current = _version_segments(current_version)
    latest = _version_segments(latest_version)
    size = max(len(current), len(latest))
    current.extend([0] * (size - len(current)))
    latest.extend([0] * (size - len(latest)))
    return tuple(latest) > tuple(current)


def _pick_download_url(payload: dict) -> str:
    assets = payload.get('assets')
    if not isinstance(assets, list):
        return ''
    candidate = ''
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        url = str(asset.get('browser_download_url') or '').strip()
        if not url:
            continue
        name = str(asset.get('name') or '').lower()
        if name.endswith('.exe') or name.endswith('.zip'):
            return url
        if not candidate:
            candidate = url
    return candidate


def check_github_latest_release(repo: str, current_version: str, timeout_seconds: float = 8.0) -> UpdateCheckResult:
    """检查 GitHub Release 是否有新版本。"""
    repo_name = str(repo or '').strip()
    current_text = _normalize_version_text(current_version)
    if not repo_name:
        return UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url='', download_url='',
            message='仓库配置为空')

    api_url = f'https://api.github.com/repos/{repo_name}/releases/latest'
    fallback_url = f'https://github.com/{repo_name}/releases/latest'
    request = Request(api_url, headers={
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'QQFarmBot-UpdateChecker',
    })

    try:
        with urlopen(request, timeout=float(timeout_seconds)) as response:
            payload = json.loads(response.read().decode('utf-8', errors='replace'))
    except HTTPError as exc:
        msg = f'检查更新失败：HTTP {exc.code}'
        if exc.code == 403:
            msg = '检查更新失败：请求受限（可能触发 GitHub API 限流）'
        return UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=fallback_url,
            download_url='', message=msg)
    except URLError:
        return UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=fallback_url,
            download_url='', message='检查更新失败：网络不可用')
    except Exception:
        return UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=fallback_url,
            download_url='', message='检查更新失败：响应解析异常')

    if not isinstance(payload, dict):
        return UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=fallback_url,
            download_url='', message='检查更新失败：返回数据格式无效')

    latest_tag = str(payload.get('tag_name') or '').strip()
    latest_version = _normalize_version_text(latest_tag)
    release_url = str(payload.get('html_url') or '').strip() or fallback_url
    download_url = _pick_download_url(payload) or release_url

    if not latest_tag:
        return UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=release_url,
            download_url=download_url, message='检查更新失败：未获取到版本标签')

    has_update = _is_remote_newer(current_text, latest_version)
    return UpdateCheckResult(
        ok=True, has_update=has_update, current_version=current_text,
        latest_version=latest_version, latest_tag=latest_tag,
        release_url=release_url, download_url=download_url,
        message='发现新版本' if has_update else '当前已是最新版本')

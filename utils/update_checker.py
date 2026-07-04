"""基于 GitHub Release 的更新检查。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


UPDATE_CHECK_CACHE_SECONDS = 6 * 60 * 60
UPDATE_CHECK_CACHE_PATH = Path('instances') / 'update_check_cache.json'


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


def _cache_key(repo: str, current_version: str) -> str:
    return f'{str(repo or "").strip()}|{_normalize_version_text(current_version)}'


def _load_cache() -> dict:
    try:
        if not UPDATE_CHECK_CACHE_PATH.exists():
            return {}
        with UPDATE_CHECK_CACHE_PATH.open(encoding='utf-8-sig') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        UPDATE_CHECK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with UPDATE_CHECK_CACHE_PATH.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _result_from_cache(payload: dict) -> UpdateCheckResult | None:
    try:
        result = payload.get('result')
        if not isinstance(result, dict):
            return None
        return UpdateCheckResult(
            ok=bool(result.get('ok', False)),
            has_update=bool(result.get('has_update', False)),
            current_version=str(result.get('current_version') or ''),
            latest_version=str(result.get('latest_version') or ''),
            latest_tag=str(result.get('latest_tag') or ''),
            release_url=str(result.get('release_url') or ''),
            download_url=str(result.get('download_url') or ''),
            message=str(result.get('message') or ''),
        )
    except Exception:
        return None


def _read_cached_result(repo: str, current_version: str, ttl_seconds: float) -> UpdateCheckResult | None:
    if ttl_seconds <= 0:
        return None
    cache = _load_cache()
    entry = cache.get(_cache_key(repo, current_version))
    if not isinstance(entry, dict):
        return None
    checked_at = float(entry.get('checked_at') or 0)
    if time.time() - checked_at > float(ttl_seconds):
        return None
    result = _result_from_cache(entry)
    if result is None:
        return None
    if result.message and '缓存' not in result.message:
        result.message = f'{result.message}（缓存）'
    return result


def _write_cached_result(repo: str, current_version: str, result: UpdateCheckResult) -> None:
    cache = _load_cache()
    cache[_cache_key(repo, current_version)] = {
        'checked_at': time.time(),
        'result': asdict(result),
    }
    _save_cache(cache)


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


def check_github_latest_release(
    repo: str,
    current_version: str,
    timeout_seconds: float = 8.0,
    cache_ttl_seconds: float = UPDATE_CHECK_CACHE_SECONDS,
) -> UpdateCheckResult:
    """检查 GitHub Release 是否有新版本。"""
    repo_name = str(repo or '').strip()
    current_text = _normalize_version_text(current_version)
    if not repo_name:
        return UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url='', download_url='',
            message='仓库配置为空')

    cached = _read_cached_result(repo_name, current_text, cache_ttl_seconds)
    if cached is not None:
        return cached

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
        result = UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=fallback_url,
            download_url='', message=msg)
        _write_cached_result(repo_name, current_text, result)
        return result
    except URLError:
        result = UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=fallback_url,
            download_url='', message='检查更新失败：网络不可用')
        _write_cached_result(repo_name, current_text, result)
        return result
    except Exception:
        result = UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=fallback_url,
            download_url='', message='检查更新失败：响应解析异常')
        _write_cached_result(repo_name, current_text, result)
        return result

    if not isinstance(payload, dict):
        result = UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=fallback_url,
            download_url='', message='检查更新失败：返回数据格式无效')
        _write_cached_result(repo_name, current_text, result)
        return result

    latest_tag = str(payload.get('tag_name') or '').strip()
    latest_version = _normalize_version_text(latest_tag)
    release_url = str(payload.get('html_url') or '').strip() or fallback_url
    download_url = _pick_download_url(payload) or release_url

    if not latest_tag:
        result = UpdateCheckResult(
            ok=False, has_update=False, current_version=current_text,
            latest_version='', latest_tag='', release_url=release_url,
            download_url=download_url, message='检查更新失败：未获取到版本标签')
        _write_cached_result(repo_name, current_text, result)
        return result

    has_update = _is_remote_newer(current_text, latest_version)
    result = UpdateCheckResult(
        ok=True, has_update=has_update, current_version=current_text,
        latest_version=latest_version, latest_tag=latest_tag,
        release_url=release_url, download_url=download_url,
        message='发现新版本' if has_update else '当前已是最新版本')
    _write_cached_result(repo_name, current_text, result)
    return result

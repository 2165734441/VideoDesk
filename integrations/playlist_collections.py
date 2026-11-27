"""Resolve a pasted multi-part video / playlist URL into individual videos.

The download queue itself already understands one URL per video.  This module
only answers "这个链接里有哪些视频", so the workbench can show the list before
the user commits the whole collection to the disk.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import video_sources


class CollectionError(ValueError):
    pass


BVID_RE = re.compile(r'/video/(BV[0-9A-Za-z]+)', re.I)
BILI_HOSTS = {'bilibili.com', 'www.bilibili.com', 'm.bilibili.com'}


def bilibili_base(url: str) -> str | None:
    """Return the /video/BV…/ URL, dropping ?p= and tracking parameters."""
    host = (urlparse(url).hostname or '').lower()
    if host not in BILI_HOSTS:
        return None
    match = BVID_RE.search(urlparse(url).path or '')
    if not match:
        return None
    return f'https://www.bilibili.com/video/{match.group(1)}/'


def clean_title(title: str, fallback: str = '') -> str:
    """Bilibili's multi-P entry titles prefix the collection title and pNN.

    Keep the useful part ("【AI漫剧教程】14-…") and fall back to the full title
    for ordinary playlists, where no such prefix exists.
    """
    value = ' '.join(str(title or '').split())
    if not value:
        return fallback
    part = re.search(r'\bp\d{1,4}\b\s*(.*)$', value, re.I)
    if part and part.group(1).strip():
        value = part.group(1).strip()
    return value[:240] or fallback


def _entry_url(entry: dict[str, Any]) -> str:
    for key in ('webpage_url', 'url', 'original_url'):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith(('http://', 'https://')):
            return value
    return ''


def resolve(url: str, data: str | Path, *, cookie_file: str = '', max_items: int = 200) -> dict[str, Any]:
    """Read playlist metadata without downloading a single byte.

    yt-dlp's Bilibili extractor returns the full multi-P playlist for a bare
    /video/BV…/ URL.  A URL copied from the player usually carries ?p=14;
    stripping that query is what makes "整个合集" possible.
    """
    raw = str(url or '').strip()
    if not raw:
        raise CollectionError('请先粘贴视频或合集链接')
    if not re.match(r'^https?://', raw, re.I):
        raise CollectionError('请输入 http 或 https 开头的视频链接')
    max_items = max(1, min(int(max_items or 200), 500))
    target = bilibili_base(raw) or raw

    from yt_dlp import YoutubeDL

    opts = video_sources.ydl_options(data, cookie_file or None)
    opts.update(
        noplaylist=False,
        playlistend=max_items,
        extract_flat=False,
        skip_download=True,
        quiet=True,
        socket_timeout=45,
        retries=1,
        extractor_retries=1,
    )
    try:
        with YoutubeDL(opts) as ydl:
            info = video_sources.resolve_info(ydl, target, None)
    except Exception as exc:
        raise CollectionError('读取合集失败：' + str(exc)[:300]) from exc

    if not isinstance(info, dict):
        raise CollectionError('这个链接没有返回可识别的视频信息')
    entries = [entry for entry in (info.get('entries') or []) if isinstance(entry, dict)]
    title = ' '.join(str(info.get('title') or '').split())[:240] or '未命名合集'
    if not entries:
        # A normal single video is still useful: the caller can keep the
        # original URL and show a one-item collection.
        single = _entry_url(info)
        if not single:
            raise CollectionError('这个链接没有解析到可下载的视频')
        return {
            'kind': 'single',
            'title': title,
            'source_url': raw,
            'resolved_url': target,
            'items': [{'index': 1, 'title': clean_title(info.get('title'), title),
                       'url': single, 'duration': info.get('duration') or 0, 'id': info.get('id') or ''}],
            'total_duration': info.get('duration') or 0,
            'truncated': False,
        }

    items = []
    for index, entry in enumerate(entries[:max_items], 1):
        item_url = _entry_url(entry)
        if not item_url:
            # Flat entries may only expose a relative p URL.
            part = entry.get('playlist_index') or index
            if bilibili_base(raw):
                item_url = f'{bilibili_base(raw)}?p={part}'
            else:
                continue
        items.append({
            'index': index,
            'title': clean_title(entry.get('title'), f'第 {index:02d} 个视频'),
            'url': item_url,
            'duration': entry.get('duration') or 0,
            'id': entry.get('id') or '',
        })
    if not items:
        raise CollectionError('合集目录为空，或平台没有公开分集链接')
    return {
        'kind': 'collection',
        'title': title,
        'source_url': raw,
        'resolved_url': target,
        'items': items,
        'total_duration': sum(float(item.get('duration') or 0) for item in items),
        'truncated': bool(info.get('entries') and len(info['entries']) > max_items),
    }

"""Runs the unmodified upstream engine in its own Python environment.

MediaCrawler: Copyright (c) 2024 relakkes@gmail.com.
Upstream is licensed under NON-COMMERCIAL LEARNING LICENSE 1.1.
See third_party/MediaCrawler/LICENSE. This adapter does not relicense upstream.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
from manual_gate import attach_manual_gate

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / 'third_party' / 'MediaCrawler'


class BudgetReached(BaseException):
    """Stop the upstream paging loop even if it catches ordinary Exceptions."""


async def run(payload):
    sys.path.insert(0, str(UPSTREAM))
    os.chdir(UPSTREAM)
    import config
    from tools.async_file_writer import AsyncFileWriter
    from var import crawler_type_var

    platform = payload['platform']
    config.PLATFORM = platform
    config.CRAWLER_TYPE = payload['mode']
    config.KEYWORDS = payload['query']
    config.CRAWLER_MAX_NOTES_COUNT = payload['max_items']
    config.MAX_CONCURRENCY_NUM = 1
    config.CRAWLER_MAX_SLEEP_SEC = 3
    config.ENABLE_GET_COMMENTS = payload['comments']
    config.ENABLE_GET_SUB_COMMENTS = False
    config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 20
    config.ENABLE_GET_MEDIA = True
    config.SAVE_DATA_OPTION = 'jsonl'
    config.SAVE_DATA_PATH = payload['directory']
    config.HEADLESS = False
    config.SAVE_LOGIN_STATE = True
    config.ENABLE_CDP_MODE = False
    config.ENABLE_IP_PROXY = False
    config.ENABLE_GET_WORDCLOUD = False
    config.CREATOR_MODE = True
    config.COOKIES = payload.get('cookies', '')
    config.LOGIN_TYPE = 'cookie' if config.COOKIES else 'qrcode'
    # Persistent Chromium/Edge profiles are never shared concurrently across engines.
    profile = Path(payload.get('data_directory', str(ROOT / '.local'))) / ('crawler-browser-' + platform)
    targets = []
    import httpx
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        for item in payload['targets']:
            match = re.search(r'https?://[^\s<>，。]+', item)
            target = match.group(0).rstrip(')），;；') if match else item
            if urlparse(target).hostname in ('v.douyin.com', 'b23.tv'):
                response = await client.get(target)
                target = str(response.url)
            targets.append(target)
    if payload['mode'] == 'detail':
        targets = targets[:payload['max_items']]
    if platform == 'dy':
        from media_platform.douyin import DouYinCrawler
        engine_class = DouYinCrawler
        config.DY_SPECIFIED_ID_LIST = targets
        config.DY_CREATOR_ID_LIST = targets
    else:
        from media_platform.bilibili import BilibiliCrawler
        engine_class = BilibiliCrawler
        config.BILI_SPECIFIED_ID_LIST = targets
        config.BILI_CREATOR_ID_LIST = targets

    # Upstream's DASH merger uses this function; reuse our installed FFmpeg directly.
    ff = importlib.import_module('media_downloader.ffmpeg')
    ff.ffmpeg_path = lambda: payload['ffmpeg']

    class LocalCrawler(engine_class):
        download_attempts = 0
        selected_comment_ids = None

        async def create_douyin_client(self, *args, **kwargs):
            client = await super().create_douyin_client(*args, **kwargs)
            attach_manual_gate(client, self.browser_context, self.context_page, payload['control_path'])
            return client

        async def create_bilibili_client(self, *args, **kwargs):
            client = await super().create_bilibili_client(*args, **kwargs)
            attach_manual_gate(client, self.browser_context, self.context_page, payload['control_path'])
            return client

        async def download_media(self, *args, **kwargs):
            if self.download_attempts >= payload['max_items']:
                raise BudgetReached()
            self.download_attempts += 1
            item = args[0] if args else kwargs.get('aweme_item', kwargs.get('video_item', {}))
            comment_id = item.get('aweme_id') if platform == 'dy' else item.get('View', {}).get('aid')
            if self.selected_comment_ids is None:
                self.selected_comment_ids = []
            if comment_id:
                self.selected_comment_ids.append(comment_id)
            await super().download_media(*args, **kwargs)
            if self.download_attempts >= payload['max_items']:
                if config.ENABLE_GET_COMMENTS and self.selected_comment_ids:
                    if platform == 'dy':
                        await self.batch_get_note_comments(self.selected_comment_ids)
                    else:
                        await self.batch_get_video_comments(self.selected_comment_ids)
                raise BudgetReached()

        async def launch_browser(self, chromium, playwright_proxy, user_agent, headless=True):
            browser_options = ({'executable_path': payload['browser_exe']} if payload.get('browser_exe')
                               else {'channel': payload.get('channel', 'msedge')})
            context = await chromium.launch_persistent_context(
                user_data_dir=str(profile), **browser_options,
                headless=False, accept_downloads=False, viewport={'width':1280, 'height':900},
                user_agent=user_agent)
            return context

    crawler = LocalCrawler()
    try:
        await crawler.start()
    except BudgetReached:
        print('Video Desk: requested video limit reached; stopping pagination.')
    finally:
        context = getattr(crawler, 'browser_context', None)
        if context:
            # Upstream's async_playwright context may have already closed it.
            with contextlib.suppress(Exception):
                await context.close()


def main():
    if '--check' in sys.argv:
        sys.path.insert(0, str(UPSTREAM))
        os.chdir(UPSTREAM)
        import execjs
        from media_platform.douyin import DouYinCrawler
        from media_platform.bilibili import BilibiliCrawler
        print(json.dumps({'ready': True, 'javascript': execjs.get().name,
                          'platforms': ['dy', 'bili']}, ensure_ascii=False))
        return
    payload = json.loads(sys.stdin.readline())
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    asyncio.run(run(payload))


if __name__ == '__main__':
    main()

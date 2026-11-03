"""yt-dlp resolution and FFmpeg stream inputs; never downloads a video file."""
from __future__ import annotations

import os
import ipaddress
from functools import lru_cache
from pathlib import Path
import re
import shutil
from urllib.parse import urlparse, parse_qs
from urllib.request import getproxies, proxy_bypass

VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mkv', '.mov', '.avi', '.flv', '.m4v', '.ts', '.m2ts'}


def douyin_video_id(url):
    parsed = urlparse(url)
    if (parsed.hostname or '').lower() not in ('www.douyin.com', 'douyin.com'):
        return ''
    value = parse_qs(parsed.query).get('modal_id', [''])[0]
    if not value:
        match = re.fullmatch(r'/video/(\d+)/?', parsed.path)
        value = match.group(1) if match else ''
    return value if re.fullmatch(r'\d{3,30}', value) else ''


def browser_media_info(url, media):
    return {'id': douyin_video_id(url), 'title': media.get('title') or '已登录页面视频',
            'extractor':'douyin:browser','extractor_key':'Douyin','original_url':url,
            'webpage_url': url, 'url': media['url'], 'ext': 'mp4', 'format_id': 'browser-player',
            'duration': media.get('duration'), 'width': media.get('width'), 'height': media.get('height'),
            'http_headers': {'Referer': url, 'User-Agent': media.get('user_agent', '')}}


def resolve_info(ydl, url, browser=None):
    """Reuse the ordinary player only after the platform extractor fails."""
    try:
        return ydl.extract_info(url, download=False)
    except Exception as exc:
        from yt_dlp.utils import DownloadError
        if not isinstance(exc, DownloadError) or not browser or not douyin_video_id(url):
            raise
        media = browser.playable_media(url)
        if not media:
            raise
        # Page navigation can refresh cookies. Keep them scoped in the jar;
        # never place a browser Cookie header on a CDN redirect.
        import http.cookiejar
        for c in browser.cookies(url):
            ydl.cookiejar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],
                c['domain'].startswith('.'),c['domain'].startswith('.'),c['path'],True,c.get('secure',False),
                int(c['expires']) if c.get('expires',-1)>0 else None,c.get('expires',-1)<=0,None,None,{},False))
        # A player exposes its currently selected rendition. Applying the
        # extractor's height filter to that single source can reject an
        # otherwise playable video (e.g. a 1080p player with a 720p preference).
        # Select this rendition explicitly; downstream image size limits remain.
        previous=ydl.params.get('format')
        previous_selector=ydl.format_selector
        try:
            ydl.params['format']='best'
            ydl.format_selector=ydl.build_format_selector('best')
            return ydl.process_ie_result(browser_media_info(url, media), download=False)
        finally:
            ydl.format_selector=previous_selector
            if previous is None:ydl.params.pop('format',None)
            else:ydl.params['format']=previous


class QuietLogger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def node_path():
    from .runtime_support import available
    if not available('node'):return None
    configured = os.environ.get('VIDEO_DESK_NODE_EXE')
    return configured if configured and Path(configured).is_file() else shutil.which('node')


@lru_cache(maxsize=1)
def youtube_support():
    from .runtime_support import available
    if not available('download') or not available('node'):
        return {'ready':False,'node':False,'ejs':'','warning':'相关运行组件暂不可用，请检测并修复'}
    from importlib.metadata import PackageNotFoundError, version
    from yt_dlp import YoutubeDL
    try:
        ejs = version('yt-dlp-ejs')
    except PackageNotFoundError:
        ejs = ''
    node = node_path()
    ready = False
    if node:
        with YoutubeDL({'quiet': True, 'logger': QuietLogger(), 'js_runtimes': {'node': {'path': node}}}) as ydl:
            runtime = ydl._js_runtimes.get('node')
            ready = bool(runtime and runtime.info)
    return {'ready': bool(ejs and ready), 'node': bool(node and ready), 'ejs': ejs}


def youtube_url(url):
    host = (urlparse(url).hostname or '').lower()
    return host in ('youtube.com', 'youtu.be', 'youtube-nocookie.com') or host.endswith(('.youtube.com', '.youtube-nocookie.com'))


def cookie_origins(url):
    # youtu.be links redirect to youtube.com; its login cookies live there.
    return ['https://www.youtube.com/', 'https://youtube.com/', url] if youtube_url(url) else [url]


def ydl_options(data, cookie_file=None):
    from .runtime_support import require
    require('download')
    result = dict(noplaylist=True, playlistend=1, quiet=True, logger=QuietLogger(), socket_timeout=25,
                  retries=2, extractor_retries=2, fragment_retries=2,
                  cachedir=str(Path(data) / 'yt-dlp-cache'), remote_components=[])
    node = node_path()
    if node:
        result['js_runtimes'] = {'node': {'path': node}}
    if cookie_file and Path(cookie_file).is_file():
        result['cookiefile'] = str(cookie_file)
    return result


def normalize_path(value):
    value = str(value).strip()
    while len(value) >= 2 and (value[0], value[-1]) in (('"', '"'), ('“', '”')):
        value = value[1:-1].strip()
    return value


def parse_paths(value):
    return [normalize_path(line) for line in str(value).splitlines() if normalize_path(line)]


def local_video(value):
    path = Path(normalize_path(value)).expanduser()
    if not path.is_absolute():raise ValueError('请输入完整的本机视频路径')
    if not path.is_file():raise ValueError('视频文件不存在或无法读取')
    if path.suffix.lower() not in VIDEO_EXTENSIONS:raise ValueError('文件格式不支持，请选择 MP4、MKV、MOV、WebM 等视频')
    if path.stat().st_size == 0:
        raise ValueError('视频文件为空')
    return path.resolve()


def format_selector(max_height=0, streaming=False):
    eligible = '[protocol!=http_dash_segments][protocol!=mhtml]' if streaming else ''
    if not max_height:
        return f'bv*{eligible}+ba{eligible}/b{eligible}'
    # Unknown-height generic URLs remain usable; known higher formats do not.
    return f'bv*{eligible}[height<=?{max_height}]+ba{eligible}/b{eligible}[height<=?{max_height}]'


def ffmpeg_input(info, fmt, cookiejar):
    url = fmt.get('url')
    parsed = urlparse(url or '')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('此链接没有可直接读取的 HTTP / HTTPS 视频流')
    protocol = fmt.get('protocol', '')
    if protocol in ('mhtml', 'http_dash_segments') or fmt.get('fragments'):
        raise ValueError('此视频仅提供分段列表，暂不支持不落盘抽帧；可使用保存视频或网页定时截图')
    headers = {**(info.get('http_headers') or {}), **(fmt.get('http_headers') or {})}
    lines = []
    # Do not copy domain-bound cookies into a raw header forwarded on redirects.
    for key, value in headers.items():
        if key.lower() in ('cookie', 'host', 'content-length', 'range', 'connection'):
            continue
        if re.fullmatch(r'[A-Za-z0-9-]+', key) and not any(c in str(value) for c in '\r\n\0'):
            lines.append(f'{key}: {value}\r\n')
    args = ['-rw_timeout', '25000000', '-protocol_whitelist', 'http,https,tcp,tls,crypto,data,httpproxy']
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = parsed.hostname == 'localhost'
    # yt-dlp follows Windows/system proxies. FFmpeg does not read the Windows
    # registry, so explicitly apply the same existing HTTP proxy for CDN access.
    if not local and not proxy_bypass(parsed.hostname):
        proxies = getproxies()
        proxy = proxies.get(parsed.scheme) or proxies.get('all')
        if proxy:
            if urlparse(proxy).scheme != 'http':
                raise ValueError('当前系统代理类型不支持直接抽帧，请使用 HTTP 代理或网页定时截图')
            args += ['-http_proxy', proxy]
    if lines:
        args += ['-headers', ''.join(lines)]
    cookies = []
    if cookiejar is not None:
        for cookie in cookiejar.get_cookies_for_url(url):
            if any(c in cookie.name + cookie.value + cookie.domain + cookie.path for c in '\r\n\0'):
                continue
            cookies.append(f'{cookie.name}={cookie.value}; path={cookie.path}; domain={cookie.domain};' + (' secure;' if cookie.secure else ''))
    if cookies:
        args += ['-cookies', '\n'.join(cookies)]
    return args + ['-i', url]


def resolved_streams(info, cookiejar):
    if info.get('is_live') or info.get('live_status') == 'is_live':
        raise ValueError('直播请使用网页录制与定时截图；链接直接抽帧目前用于已发布的视频')
    formats = info.get('requested_formats') or info.get('requested_downloads') or [info]
    video = next((f for f in formats if f.get('vcodec') != 'none' and f.get('url')), None)
    if video is None:
        raise ValueError('链接中没有可用的视频画面')
    audio = next((f for f in formats if f.get('acodec') not in ('none', None) and f.get('url')), video)
    return ffmpeg_input(info, video, cookiejar), ffmpeg_input(info, audio, cookiejar)

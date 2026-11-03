"""Ephemeral loopback media relay: credentials and signed URLs stay in memory.

Used only by the owned FFmpeg/ffprobe tree. Supports HTTP byte ranges and
rewrites HLS child references without saving playlists or video segments.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import secrets
import threading
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
from urllib.error import HTTPError


class MediaRelay:
    def __init__(self, cookiejar=None):
        self.cookiejar = cookiejar
        self.resources = {}
        self.lock = threading.RLock()
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_): pass
            def do_HEAD(self): relay.serve(self, head=True)
            def do_GET(self): relay.serve(self)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def add(self, url, headers=None):
        if urlparse(url).scheme not in ('http', 'https'):
            raise ValueError('媒体子链接不是 HTTP 视频源')
        key = secrets.token_urlsafe(24)
        with self.lock:
            self.resources[key] = (url, headers or {})
        return f'http://127.0.0.1:{self.server.server_port}/{key}'

    def from_ffmpeg(self, args):
        headers = {}
        if '-headers' in args:
            for line in args[args.index('-headers') + 1].splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    headers[k] = v.strip()
        return self.add(args[args.index('-i')+1], headers)

    def serve(self, handler, head=False):
        key = handler.path.split('?', 1)[0].strip('/')
        with self.lock:
            resource = self.resources.get(key)
        if not resource:
            handler.send_error(404)
            return
        url, base_headers = resource
        headers = dict(base_headers)
        if handler.headers.get('Range'):
            headers['Range'] = handler.headers['Range']
        headers['Accept-Encoding'] = 'identity'
        handlers = [HTTPCookieProcessor(self.cookiejar)] if self.cookiejar is not None else []
        # urllib reads the same existing Windows/system proxy settings as yt-dlp.
        opener = build_opener(*handlers)
        try:
            with opener.open(Request(url, headers=headers, method='HEAD' if head else 'GET'), timeout=35) as response:
                content_type = response.headers.get('Content-Type', '')
                is_hls = 'mpegurl' in content_type.lower() or urlparse(url).path.lower().endswith('.m3u8')
                if is_hls and not head:
                    text = response.read(4 * 1024 * 1024).decode('utf-8-sig')
                    rewritten = []
                    for line in text.splitlines():
                        if line and not line.startswith('#'):
                            line = self.add(urljoin(response.url, line.strip()), base_headers)
                        elif 'URI="' in line:
                            line = re.sub(r'URI="([^"]+)"', lambda m: 'URI="'+self.add(urljoin(response.url, m[1]), base_headers)+'"', line)
                        rewritten.append(line)
                    body = ('\n'.join(rewritten)+'\n').encode()
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                    handler.send_header('Content-Length', str(len(body)))
                    handler.end_headers()
                    handler.wfile.write(body)
                    return
                handler.send_response(response.status)
                for name in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
                    if response.headers.get(name): handler.send_header(name, response.headers[name])
                handler.send_header('Connection', 'close')
                handler.end_headers()
                if not head:
                    while chunk := response.read(128 * 1024):
                        handler.wfile.write(chunk)
                handler.close_connection = True
        except HTTPError as error:
            handler.send_error(error.code)
        except (OSError, ValueError):
            try: handler.send_error(502, 'Media source unavailable')
            except OSError: pass

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        with self.lock: self.resources.clear()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()

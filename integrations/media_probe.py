"""One ffprobe contract for source checks and media batches, without Node/AI."""
import json
import math
import subprocess
import time
from pathlib import Path

MESSAGES = {'missing_path': '视频路径不存在，请重新选择', 'permission_denied': '没有读取视频的权限',
            'source_disconnected': '网页来源已断开，请重新选择同一文件并继续',
            'probe_timeout': '视频检查超时，可重试延长检查', 'unsupported_codec': '当前组件不支持此编码',
            'missing_video_stream': '文件中没有视频流', 'container_error': '无法解析视频容器',
            'component_unavailable': '视频检查组件不可用，请检测并修复'}


class ProbeError(ValueError):
    def __init__(self, category, diagnostic):
        self.diagnostic = {**diagnostic, 'category': category, 'message': MESSAGES[category]}
        super().__init__(MESSAGES[category] + ('：'+diagnostic['summary'] if diagnostic.get('summary') else ''))


def inspect(source, executable, connected=lambda: True, on_retry=lambda: None, timeouts=(12, 45), runner=None):
    diagnostic = {'component': str(executable), 'exit_code': None, 'summary': '', 'attempts': []}
    remote = str(source).startswith(('http://', 'https://'))
    if not remote:
        try:
            with Path(source).open('rb') as stream: stream.read(1)
        except FileNotFoundError as exc: raise ProbeError('missing_path', diagnostic) from exc
        except PermissionError as exc: raise ProbeError('permission_denied', diagnostic) from exc
    for attempt, timeout in enumerate(timeouts):
        started = time.monotonic()
        try:
            result = (runner or subprocess.run)([str(executable), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(source)],
                                    capture_output=True, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            diagnostic.update(exit_code=result.returncode, summary=result.stderr.decode('utf-8', 'replace')[-1200:].strip())
            diagnostic['attempts'].append({'timeout': timeout, 'elapsed': round(time.monotonic()-started, 3), 'exit_code': result.returncode})
        except subprocess.TimeoutExpired as exc:
            diagnostic['attempts'].append({'timeout': timeout, 'elapsed': round(time.monotonic()-started, 3), 'timed_out': True})
            if not connected(): raise ProbeError('source_disconnected', diagnostic) from exc
            if attempt+1 < len(timeouts):
                on_retry(); continue
            raise ProbeError('probe_timeout', diagnostic) from exc
        except (FileNotFoundError, PermissionError, OSError) as exc:
            diagnostic['summary'] = str(exc)
            raise ProbeError('component_unavailable', diagnostic) from exc
        if not connected(): raise ProbeError('source_disconnected', diagnostic)
        if result.returncode:
            error = diagnostic['summary'].lower()
            category = 'permission_denied' if 'permission denied' in error else 'unsupported_codec' if any(s in error for s in ('unsupported codec', 'unknown decoder', 'decoder not found')) else 'container_error'
            raise ProbeError(category, diagnostic)
        try: data = json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc: raise ProbeError('container_error', diagnostic) from exc
        streams = data.get('streams', [])
        video = next((s for s in streams if s.get('codec_type')=='video'), None)
        if video is None: raise ProbeError('missing_video_stream', diagnostic)
        try: duration = float(data.get('format', {}).get('duration') or video.get('duration') or 0)
        except (TypeError, ValueError): duration = 0
        if not math.isfinite(duration): duration = 0
        return {'duration': duration, 'width': video.get('width', 0), 'height': video.get('height', 0),
                'hasAudio': any(s.get('codec_type')=='audio' for s in streams),
                'hasSubtitle': any(s.get('codec_type')=='subtitle' for s in streams), 'codec': video.get('codec_name'),
                'diagnostic': {**diagnostic, 'category': None}, 'raw': data}
    raise ProbeError('probe_timeout', diagnostic)

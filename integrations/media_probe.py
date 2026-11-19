"""One ffprobe contract for source checks and media batches, without Node/AI."""
import json
import math
import re
import subprocess
import time
from pathlib import Path

MESSAGES = {'missing_path': '视频路径不存在，请重新选择', 'permission_denied': '没有读取视频的权限',
            'source_disconnected': '网页来源已断开，请重新选择同一文件并继续',
            'probe_timeout': '视频检查超时，可重试延长检查', 'unsupported_codec': '当前组件不支持此编码',
            'missing_video_stream': '文件中没有视频流', 'container_error': '无法解析视频容器',
            'component_unavailable': '视频检查组件不可用，请检测并修复',
            'incomplete_media': '视频数据不完整，请重新获取原视频；已有部分结果保留'}

DAMAGE_PATTERN = re.compile(r'partial file|invalid data found|error (?:while )?(?:decoding|reading)|corrupt(?:ed)? (?:input|frame|packet)|packet corrupt|error submitting a packet|premature (?:end|EOF)|ended prematurely|prematurely', re.I)


def damaged_log(value):
    if isinstance(value, bytes): value = value.decode('utf-8', 'replace')
    return bool(DAMAGE_PATTERN.search(value))


def check_transport_tail(source):
    """Detect a cut transport packet, without inventing an expected TS duration.

    A cut at a complete packet boundary cannot be distinguished from a legitimate
    short capture unless the caller has an independent expected duration.
    """
    path = Path(source)
    if not path.is_file(): return
    with path.open('rb') as stream: header = stream.read(204*8)
    for width, prefix in ((188, 0), (192, 4), (204, 0)):
        for start in range(min(width, len(header))):
            sync = start+prefix
            if sync+4*width >= len(header): continue
            if all(header[sync+i*width] == 0x47 for i in range(5)):
                if (path.stat().st_size-start) % width:
                    raise ProbeError('incomplete_media', {'component': 'transport-packet-check', 'summary': 'MPEG-TS 末尾数据包被截断'})
                return


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
        if not remote and 'mpegts' in data.get('format', {}).get('format_name', ''):
            check_transport_tail(source)
        if damaged_log(diagnostic['summary']): raise ProbeError('incomplete_media', diagnostic)
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

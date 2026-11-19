"""Bounded, real encoder probes. Merely listing a GPU is not a capability test."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

_lock = threading.Lock()
_cache = {}


def run(args, timeout=20):
    return subprocess.run(list(map(str, args)), capture_output=True, timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def capabilities(ffmpeg, refresh=False):
    with _lock:
        if not refresh and ffmpeg in _cache:
            return dict(_cache[ffmpeg])
        gpus = []
        smi = shutil.which('nvidia-smi')
        if not smi:
            candidate = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/nvidia-smi.exe'
            if candidate.is_file(): smi = str(candidate)
        if smi:
            try:
                result = run([smi, '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader,nounits'], 8)
                for line in result.stdout.decode('utf-8', 'replace').splitlines():
                    name, memory, driver = [v.strip() for v in line.split(',')]
                    gpus.append({'name': name, 'memory_mb': int(memory), 'driver': driver})
            except (OSError, ValueError, subprocess.TimeoutExpired): pass
        encoders = {}
        for encoder in ('h264_nvenc', 'h264_qsv', 'h264_amf'):
            try:
                p = run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                         'color=c=black:s=320x180:r=10:d=0.2', '-an', '-c:v', encoder, '-f', 'null', '-'], 15)
                encoders[encoder] = {'available': p.returncode == 0,
                                     'reason': '' if p.returncode == 0 else '实际试编码未通过'}
            except (OSError, subprocess.TimeoutExpired):
                encoders[encoder] = {'available': False, 'reason': '试编码超时或组件不可用'}
        chosen = next((e for e, v in encoders.items() if v['available']), 'libx264')
        result = {'gpus': gpus, 'encoders': encoders, 'encoder': chosen,
                  'checked': time.time(), 'ai': 'AI 后端按模型试运行，失败时回退 CPU'}
        _cache[ffmpeg] = result
        return dict(result)


def video_args(encoder, quality=23):
    q = str(quality)
    if encoder == 'h264_nvenc': return ['-c:v', encoder, '-preset', 'p4', '-cq', q, '-b:v', '0']
    if encoder == 'h264_qsv': return ['-c:v', encoder, '-global_quality', q]
    if encoder == 'h264_amf': return ['-c:v', encoder, '-quality', 'balanced', '-rc', 'cqp', '-qp_i', q, '-qp_p', q]
    return ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', q]


def encode(a, jid, prefix, suffix, device='auto', quality=23):
    """Suffix ends in an output path. Retry on CPU without changing media filters."""
    encoder = 'libx264' if device == 'cpu' else capabilities(a.FFMPEG)['encoder']
    started = time.monotonic()
    try:
        a.run_ffmpeg(jid, [*prefix, *video_args(encoder, quality), *suffix])
    except Exception:
        a.check_cancel(jid)
        if encoder == 'libx264': raise
        a.change(jid, message='显卡编码不可用，使用 CPU 继续', encoder_fallback=True)
        encoder = 'libx264'
        a.run_ffmpeg(jid, [*prefix, *video_args(encoder, quality), *suffix])
    a.change(jid, encoder=encoder, encode_seconds=round(time.monotonic()-started, 3))
    return encoder

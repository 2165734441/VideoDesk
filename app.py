"""Local Video Desk with optional user-configured transcription services."""
from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime, timedelta, timezone
import http.cookiejar
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse, parse_qs
import uuid

# The isolated portable interpreter intentionally omits the script directory.
# Resolve imports relative to this copy, including when running source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
import imageio_ffmpeg
from integrations import image_dedup, video_sources, storyboard, local_sources, batch_pipeline, material_history, material_packages, runtime_support

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('VIDEO_DESK_DATA', str(ROOT / '.local')))
DATA.mkdir(parents=True, exist_ok=True)
TOKEN = secrets.token_urlsafe(32)
FFMPEG = os.environ.get('VIDEO_DESK_FFMPEG') or shutil.which('ffmpeg') or imageio_ffmpeg.get_ffmpeg_exe()
LOCK = threading.RLock()
WORKERS = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='media')
CRAWLERS = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='crawler')
DEDUP = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='dedup')
JOBS: dict[str, dict] = {}
EVENTS: dict[str, threading.Event] = {}
PROCESSES: dict[str, subprocess.Popen] = {}
SESSION_LOCKS: dict[str, threading.RLock] = {}
LOCAL_PREVIEWS: dict[str, Path] = {}
REPAIR_PENDING = False


from integrations.storage import atomic_json
from integrations.job_recovery import load_jobs


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return fallback


SETTINGS = load_json(DATA / 'settings.json', {'output': os.environ.get('VIDEO_DESK_DEFAULT_OUTPUT', str(ROOT / 'output'))})
for item in load_jobs(DATA,SETTINGS['output']):
    if item['status'] in ('queued', 'running', 'recording', 'processing', 'waiting', 'waiting_source'):
        item.update(status='interrupted', message='上次服务已关闭。已保存的文件仍保留，可在素材中查看。')
    JOBS[item['id']] = item
    EVENTS[item['id']] = threading.Event()
    SESSION_LOCKS[item['id']] = threading.RLock()


def persist():
    with LOCK:
        atomic_json(DATA / 'jobs.json', list(JOBS.values()), backup=True)


def change(jid, **values):
    with LOCK:
        if REPAIR_PENDING and values.get('status') in ('queued','running','recording','processing','waiting','waiting_source'):
            raise HTTPException(409,'修复已开始，请等待新的工作台页面打开后再提交任务')
        if 'message' in values:
            values.setdefault('progress_detail', {})
            values.setdefault('progress_indeterminate', False)
        JOBS[jid].update(values, updated=time.time())
        persist()


def output_dir(value='') -> Path:
    path = Path(value or SETTINGS['output']).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, '请输入完整的本机目录路径，例如 D:\\视频素材')
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ('.video-desk-' + uuid.uuid4().hex)
        probe.write_bytes(b'')
        probe.unlink()
    except OSError as exc:
        raise HTTPException(400, '此目录无法写入，请更换保存位置。') from exc
    return path.resolve()


def new_job(kind, title, output='', source='', options=None):
    with LOCK:
        if REPAIR_PENDING:raise HTTPException(409,'修复已开始，请等待新的工作台页面打开后再提交任务')
    jid = uuid.uuid4().hex[:12]
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    dest = output_dir(output) / (stamp + '_' + jid)
    dest.mkdir()
    job = dict(id=jid, kind=kind, title=title, status='queued', message='排队中', progress=0,
               created=time.time(), updated=time.time(), directory=str(dest), source=source,
               options=options or {}, frames=0, files=[])
    with LOCK:
        if REPAIR_PENDING:
            try:dest.rmdir()
            except OSError:pass
            raise HTTPException(409,'修复已开始，请等待新的工作台页面打开后再提交任务')
        JOBS[jid] = job
        EVENTS[jid] = threading.Event()
        SESSION_LOCKS[jid] = threading.RLock()
        try:
            persist()
        except Exception:
            JOBS.pop(jid, None)
            EVENTS.pop(jid, None)
            SESSION_LOCKS.pop(jid, None)
            try: dest.rmdir()
            except OSError: pass
            raise
    return job


def job_by_id(jid):
    with LOCK:
        if jid not in JOBS:
            raise HTTPException(404, '任务不存在')
        return JOBS[jid]


def safe_file(job, relative):
    root = Path(job['directory']).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, '文件不存在')
    return path


def refresh_files(jid):
    job = job_by_id(jid)
    base = Path(job['directory'])
    files = []
    for path in sorted(base.rglob('*')):
        if path.is_file() and not any(p.startswith('.') for p in path.relative_to(base).parts):
            files.append({'name': path.relative_to(base).as_posix(), 'size': path.stat().st_size})
    change(jid, files=files, frames=sum(f['name'].lower().endswith(('.jpg', '.png')) for f in files))


def write_bundle(jid, extra=None):
    job = job_by_id(jid)
    base = Path(job['directory'])
    manifest = {k: job[k] for k in ('id', 'kind', 'title', 'source', 'created', 'options', 'frames')}
    manifest.update(extra or {})
    if job['options'].get('video_saved') is False:
        manifest['video_saved'] = False
    atomic_json(base / 'manifest.json', manifest)
    (base / 'AI分析提示词.txt').write_text(
        ('本次未附视频文件，请根据截图、时间索引与随附的可选音轨分析原视频内容。\n'
         if job['options'].get('video_saved') is False else '请结合随附视频的声音、字幕与按时间排序的截图，分析这个视频讲了什么。\n') +
        '1. 概括核心主题和主要结论。\n2. 按时间顺序列出关键内容，引用截图时间。\n'
        '3. 提取方法、步骤、数据与待核实说法。\n4. 区分画面、字幕、声音直接提供的信息与推断。\n'
        '5. 若只提供图片，请明确无法从静态图片确认的语音内容，不要编造。\n\n'
        '时间索引见 timeline.json；实时采集时间是本工具的采集时间，不等于播放器进度。\n', encoding='utf-8')
    material_history.register_created(sys.modules[__name__],jid,[base/'manifest.json',base/'AI分析提示词.txt'])
    refresh_files(jid)


class Cancelled(Exception):
    pass


def check_cancel(jid):
    if EVENTS[jid].is_set():
        raise Cancelled()


def run_ffmpeg(jid, args, private=False):
    check_cancel(jid)
    threads=str(job_by_id(jid).get('resources',{}).get('decode_threads',2))
    log = Path(job_by_id(jid)['directory']) / '.ffmpeg.log'
    with log.open('wb') as stream:
        proc = subprocess.Popen([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-threads',threads,'-filter_threads','1', *args[:-1], '-threads',threads,args[-1]],
                                stdout=subprocess.DEVNULL, stderr=stream,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))
        with LOCK:
            PROCESSES[jid] = proc
        try:
            while proc.poll() is None:
                if EVENTS[jid].wait(.15):
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    raise Cancelled()
            check_cancel(jid)
            if proc.returncode:
                summary=log.read_text(encoding='utf-8', errors='replace')[-1000:]
                category='unsupported_codec' if re.search(r'unknown decoder|decoder.*not found|unsupported codec',summary,re.I) else 'permission_denied' if 'Permission denied' in summary else 'media_processing_error'
                change(jid,source_diagnostic={'component':FFMPEG,'exit_code':proc.returncode,'summary':summary,'category':category})
                raise RuntimeError('FFmpeg 处理失败：' + summary)
            # FFmpeg can exit successfully after decoding only a damaged prefix.
            diagnostic=log.read_text(encoding='utf-8',errors='replace')
            if re.search(r'partial file|invalid data found|error (?:while )?(?:decoding|reading)|corrupt(?:ed)? (?:input|frame|packet)|packet corrupt|error submitting a packet|premature (?:end|EOF)',diagnostic,re.I):
                change(jid,source_diagnostic={'component':FFMPEG,'exit_code':0,'category':'incomplete_media','summary':'解码日志报告损坏或提前结束'})
                raise RuntimeError('视频解码不完整：检测到损坏或截断，已生成的部分文件保留，请检查原视频或缩小到可读取的时间范围')
            if '%' not in str(args[-1]):material_history.register_created(sys.modules[__name__],jid,[Path(args[-1])])
        finally:
            with LOCK:
                PROCESSES.pop(jid, None)
            # Batch logs are archived into the private workspace by Pipeline.run.
            if job_by_id(jid).get('kind')!='batch-video':material_history.register_created(sys.modules[__name__],jid,[log])


class FrameOptions(BaseModel):
    mode: str = 'seconds'
    value: float = Field(default=3, gt=0, le=36000)
    crop: list[float] | None = None
    start: float = Field(default=0, ge=0)
    duration: float = Field(default=0, ge=0, le=86400)
    limit: int = Field(default=2000, ge=1, le=100000)
    audio: bool = True


def frame_options(opts):
    if opts.mode not in ('seconds', 'fps', 'frames', 'all'):
        raise HTTPException(400, '未知抽帧方式')
    if opts.mode == 'fps' and opts.value > 60:
        raise HTTPException(400, '每秒截图最多 60 张')
    if opts.mode == 'frames' and not opts.value.is_integer():
        raise HTTPException(400, '每隔 N 帧的 N 必须是整数')
    if opts.crop:
        if len(opts.crop) != 4 or not all(math.isfinite(v) for v in opts.crop):
            raise HTTPException(400, '裁剪区域格式错误')
        x, y, w, h = opts.crop
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.00001 or y + h > 1.00001:
            raise HTTPException(400, '裁剪范围必须在画面之内')
    return opts


def extract_frames(jid, source, opts, input_args=None, audio_input=None, source_duration=None):
    opts = frame_options(FrameOptions(**opts))
    base = Path(job_by_id(jid)['directory'])
    folder = base / 'frames'
    folder.mkdir(exist_ok=True)
    filters = []
    if opts.crop:
        x, y, w, h = opts.crop
        filters.append(f'crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}')
    if opts.mode == 'seconds':
        filters.append(f'select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{opts.value})')
    elif opts.mode == 'fps':
        filters.append(f'select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{1 / opts.value})')
    elif opts.mode == 'frames':
        filters.append(f'select=not(mod(n\\,{int(opts.value)}))')
    filters.append('showinfo')
    # showinfo provides source presentation timestamps, including variable-frame-rate files.
    remote = input_args is not None
    args = [FFMPEG, '-hide_banner', '-loglevel', 'info', '-nostdin', '-y', *(input_args or ['-i', str(source)])]
    # Trim in the filter to keep the original source timestamps in the index.
    prefix = f'trim=start={opts.start}' + (f':duration={opts.duration}' if opts.duration and not remote else '')
    args += ['-vf', ','.join([prefix, *filters]), '-fps_mode', 'vfr', '-frames:v', str(opts.limit),
             '-q:v', '3']
    if remote and opts.duration:
        args += ['-to', str(opts.start + opts.duration)]
    args += [str(folder / '%06d.jpg')]
    log = base / '.frames.log'
    decode_damage=threading.Event()
    with log.open('wb') as stream:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE if remote else stream,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def timestamps_only():
            # Retain source timestamps, never signed CDN URLs or request headers.
            last = 0
            for line in proc.stderr:
                if re.search(rb'partial file|invalid data found|error (?:while )?(?:decoding|reading)|corrupt(?:ed)? (?:input|frame|packet)|packet corrupt|error submitting a packet|premature (?:end|EOF)',line,re.I):decode_damage.set()
                match = re.search(rb'\bpts_time:([\d.eE+\-]+)', line)
                if match:
                    stream.write(b'pts_time:' + match[1] + b'\n')
                    now = time.monotonic()
                    if now - last > 1:
                        last = now
                        seconds = float(match[1])
                        duration = opts.duration or max(0, (source_duration or 0) - opts.start)
                        progress = min(95, max(10, 10 + (seconds - opts.start) / duration * 85)) if duration else 10
                        change(jid, progress=round(progress), message=f'正在直连抽帧 · 读取至 {seconds:.1f} 秒 · 不保存视频')
            proc.stderr.close()
        reader = threading.Thread(target=timestamps_only, daemon=True) if remote else None
        if reader:
            reader.start()
        with LOCK:
            PROCESSES[jid] = proc
        try:
            while proc.poll() is None:
                if EVENTS[jid].wait(.2):
                    proc.terminate()
                    proc.wait(timeout=10)
                    raise Cancelled()
            check_cancel(jid)
        finally:
            if proc.poll() is None:
                stop_owned_process(proc)
            if reader:
                reader.join(timeout=10)
            with LOCK:
                PROCESSES.pop(jid, None)
    material_history.register_created(sys.modules[__name__],jid,[log])
    logs = log.read_text(encoding='utf-8', errors='replace')
    if proc.returncode:
        if remote:
            raise RuntimeError('直连抽帧失败。请确认网络、登录状态和视频是否可播放；没有保存视频文件。')
        raise RuntimeError('视频抽帧失败：' + logs[-900:])
    if decode_damage.is_set() or re.search(r'partial file|invalid data found|error (?:while )?(?:decoding|reading)|corrupt(?:ed)? (?:input|frame|packet)|packet corrupt|error submitting a packet|premature (?:end|EOF)',logs,re.I):
        change(jid,source_diagnostic={'component':FFMPEG,'exit_code':0,'category':'incomplete_media','summary':'解码日志报告损坏或提前结束'})
        raise RuntimeError('视频解码不完整：检测到损坏或截断，请检查原视频或缩小到可读取的时间范围')
    stamps = [float(t) for t in re.findall(r'\bpts_time:([\d.eE+\-]+)', logs)]
    frames = [folder/f'{i+1:06d}.jpg' for i in range(len(stamps)) if (folder/f'{i+1:06d}.jpg').is_file()]
    if not frames:
        raise RuntimeError('所选时间范围没有视频帧，请检查起始时间和视频内容。')
    timeline = [dict(file='frames/' + path.name,
                     seconds=round(stamps[i], 6) if i < len(stamps) else None,
                     clock='source_video') for i, path in enumerate(frames)]
    atomic_json(base / 'timeline.json', timeline)
    material_history.register_created(sys.modules[__name__],jid,[*frames,base/'timeline.json'])
    warnings = []
    if len(frames) >= opts.limit:
        warnings.append(f'已达到 {opts.limit} 张上限；需要更多图片可增大上限或分段抽取。')
    if opts.audio:
        try:
            audio_args = list(audio_input or input_args or ['-i', str(source)])
            if opts.start:
                audio_args += ['-ss', str(opts.start)]
            if opts.duration:
                audio_args += ['-t', str(opts.duration)]
            run_ffmpeg(jid, audio_args + ['-vn', '-ac', '1', '-ar', '16000', '-c:a', 'aac', str(base / 'audio.m4a')], private=remote)
        except Cancelled:
            raise
        except RuntimeError:
            warnings.append('音轨未导出：视频可能无音轨；图片仍可使用。')
    change(jid, frames=len(frames))
    write_bundle(jid, {'timeline_clock': 'source_video', 'warnings': warnings})
    return warnings


def refresh_partial_files(jid):
    # A full destination must not prevent the in-memory terminal state from
    # being set. Persisting that state is retried on the next normal update.
    try:refresh_files(jid)
    except OSError:pass


def guarded(jid, action):
    try:
        check_cancel(jid)
        change(jid, status='running', message='处理中')
        message = action() or '处理完成'
        check_cancel(jid)
        refresh_files(jid)
        change(jid, status='done', progress=100, message=message)
    except Cancelled:
        refresh_partial_files(jid)
        change(jid, status='cancelled', message='已取消，已保存的文件仍保留')
    except storyboard.TakeoverRequired:
        refresh_partial_files(jid)
        # Waiting is persisted; explicit resume schedules the same job again.
    except local_sources.SourceUnavailable:
        refresh_partial_files(jid)
        if EVENTS[jid].is_set():
            change(jid, status='cancelled', message='已取消，已完成片段保留')
        else:
            change(jid, status='waiting_source', message='等待重新选择同一文件；已完成片段保留，可继续处理其他视频')
    except Exception as exc:
        refresh_partial_files(jid)
        if EVENTS[jid].is_set():
            change(jid, status='cancelled', message='已取消，已保存的文件仍保留')
            return
        message = str(exc)
        # Never display CDN signed URLs, cookie values, or raw downloader diagnostics.
        if job_by_id(jid)['kind'] == 'download':
            message = '下载未完成。若页面要求登录或验证，请打开登录浏览器手动完成后重试；也可使用网页录制。'
        elif job_by_id(jid)['kind'] == 'stream' and not isinstance(exc, ValueError):
            message = '链接直接抽帧未完成。请确认本机网络能访问该平台、视频可播放，并在需要时手动登录后重试。没有保存视频文件。'
        elif job_by_id(jid)['kind'] == 'analysis':
            message = re.sub(r'https?://\S+', '[媒体来源]', message)
        change(jid, status='error', message=message[:1400])
    finally:
        if 'MATERIAL_HISTORY' in globals() and job_by_id(jid)['kind']=='analysis':
            try:MATERIAL_HISTORY.record(jid)
            except OSError:pass


from integrations.login_browser import LoginBrowser

BROWSER = LoginBrowser(DATA)

CRAWLER_ROOT = ROOT / 'third_party' / 'MediaCrawler'
CRAWLER_PYTHON = Path(os.environ.get('VIDEO_DESK_CRAWLER_PYTHON', str(ROOT / '.local' / 'mediacrawler-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))))


def crawler_available():
    if not runtime_support.available('crawler'):return False
    ready = os.environ.get('VIDEO_DESK_BUNDLED') == '1' or (DATA / 'mediacrawler-ready.json').is_file()
    return CRAWLER_PYTHON.is_file() and (CRAWLER_ROOT / 'media_platform').is_dir() and ready


def stop_owned_process(proc):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def crawl_platform(jid, req):
    job = job_by_id(jid)
    base = Path(job['directory'])
    target_url = 'https://www.douyin.com/' if req['platform'] == 'dy' else 'https://www.bilibili.com/'
    cookies = BROWSER.cookies(target_url) if req.get('use_login') else []
    imported = ANALYSIS_ENGINE.cookie_path(req.get('cookie_id', ''))
    if imported:
        jar = http.cookiejar.MozillaCookieJar(str(imported)); jar.load(ignore_discard=True, ignore_expires=True)
        host = urlparse(target_url).hostname
        cookies = [{'name': c.name, 'value': c.value} for c in jar if host == c.domain.lstrip('.') or host.endswith('.'+c.domain.lstrip('.'))]
    payload = {**req, 'directory': str(base), 'ffmpeg': FFMPEG,
               'data_directory': str(DATA), 'browser_exe': os.environ.get('VIDEO_DESK_BROWSER_EXE', ''),
               'cookies': '; '.join(c['name'] + '=' + c['value'] for c in cookies),
               'targets': [t.strip() for t in re.split(r'[\n,，]+', req['query']) if t.strip()]}
    control_path = DATA / ('crawler-' + jid + '-control.json')
    payload['control_path'] = str(control_path)
    control_path.unlink(missing_ok=True)
    control_path.with_suffix('.resume').unlink(missing_ok=True)
    for path in ('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
                 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'):
        if Path(path).is_file():
            payload['channel'] = 'msedge'
            break
    else:
        payload['channel'] = 'chrome'
    change(jid, message='平台采集中：请查看已打开的浏览器，手动完成登录 / 验证。')
    log_path = DATA / ('crawler-' + jid + '.log')
    with log_path.open('wb') as log:
        proc = subprocess.Popen([str(CRAWLER_PYTHON), '-u', str(ROOT / 'integrations' / 'mediacrawler_bridge.py')],
            cwd=CRAWLER_ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log,
            env={**os.environ, 'PYTHONIOENCODING':'utf-8', 'PYTHONUTF8':'1'},
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with LOCK:
            PROCESSES[jid] = proc
        try:
            proc.stdin.write((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
            proc.stdin.close()
            while proc.poll() is None:
                if EVENTS[jid].wait(.5):
                    stop_owned_process(proc)
                    raise Cancelled()
                waiting = control_path.is_file()
                if waiting and job['status'] != 'waiting':
                    change(jid, status='waiting', message='平台要求人工验证：请在采集浏览器处理后，点击“已完成验证，继续”。')
                elif not waiting and job['status'] == 'waiting':
                    change(jid, status='running', message='人工验证后继续采集中')
            check_cancel(jid)
        finally:
            with LOCK:
                PROCESSES.pop(jid, None)
            control_path.unlink(missing_ok=True)
            control_path.with_suffix('.resume').unlink(missing_ok=True)
    if proc.returncode:
        raise RuntimeError('MediaCrawler 未完成。请检查登录 / 验证，或减少数量重试。诊断日志在 .local/crawler-' + jid + '.log（仅本机）。')
    videos = [p for p in base.rglob('*') if p.suffix.lower() in ('.mp4', '.webm', '.mkv', '.flv', '.mov') and p.stat().st_size]
    records = sum(len(p.read_text(encoding='utf-8', errors='replace').splitlines()) for p in base.rglob('*.jsonl'))
    if not videos and not records:
        raise RuntimeError('采集结束，但没有获得视频或数据。请确认目标存在、登录已完成，并检查平台浏览器中的提示。')
    extracted = []
    if req['extract']:
        for source in videos:
            check_cancel(jid)
            child = new_job('extract', '自动抽帧 · ' + source.parent.name, str(base.parent),
                            job['source'], req['frames'])
            child_id = child['id']
            WORKERS.submit(guarded, child_id, lambda j=child_id, p=source: ('抽帧完成。' + ' '.join(extract_frames(j, p, req['frames']))))
            extracted.append(child_id)
    write_bundle(jid, {'engine':'MediaCrawler', 'records':records, 'videos':len(videos), 'extraction_jobs':extracted})
    return f'平台采集完成：{records} 条数据，{len(videos)} 个视频。' + (f' 已创建 {len(extracted)} 个抽帧任务。' if extracted else '') + (' 部分视频可能受平台权限限制未能下载。' if not videos else '')


def normalized_url(value):
    match = re.search(r'https?://[^\s<>"，。]+', value)
    if not match:
        raise HTTPException(400, '请输入视频网址或含网址的分享文本')
    url = match.group(0).rstrip(')）,;；')
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or not parsed.hostname:
        raise HTTPException(400, '网址格式错误')
    if parsed.hostname.lower() in ('www.douyin.com', 'douyin.com'):
        modal=parse_qs(parsed.query).get('modal_id',[''])[0]
        if re.fullmatch(r'\d{10,30}',modal):return 'https://www.douyin.com/video/'+modal
    return url


def browser_cookie_file(jid, url, enabled):
    cookies_path = DATA / ('.cookies-' + jid + '.txt')
    if enabled:
        cookies = {}
        for origin in video_sources.cookie_origins(url):
            for c in BROWSER.cookies(origin):
                cookies[(c['domain'], c['path'], c['name'])] = c
        jar = http.cookiejar.MozillaCookieJar(str(cookies_path))
        for c in cookies.values():
            jar.set_cookie(http.cookiejar.Cookie(0, c['name'], c['value'], None, False, c['domain'],
                c['domain'].startswith('.'), c['domain'].startswith('.'), c['path'], True,
                c.get('secure', False), int(c['expires']) if c.get('expires', -1) > 0 else None,
                c.get('expires', -1) <= 0, None, None, {}, False))
        jar.save(ignore_discard=True, ignore_expires=True)
    return cookies_path


def stream_frames(jid, req):
    from yt_dlp import YoutubeDL
    job = job_by_id(jid)
    base = Path(job['directory'])
    imported = ANALYSIS_ENGINE.cookie_path(req.get('cookie_id', ''))
    cookies_path = imported or browser_cookie_file(jid, job['source'], req['login'])
    opts = video_sources.ydl_options(DATA, cookies_path)
    opts['format'] = video_sources.format_selector(req.get('max_height', 0), streaming=True)
    try:
        check_cancel(jid)
        change(jid, message='正在解析视频链接；只输出图片，不保存视频文件',progress=2,progress_indeterminate=True)
        with YoutubeDL(opts) as ydl:
            info = video_sources.resolve_info(ydl, job['source'], BROWSER if req.get('login') and not imported else None)
            if info and info.get('entries'):
                info = next((e for e in info['entries'] if e), None)
            check_cancel(jid)
            if not info:
                raise ValueError('未取得可抽帧的视频')
            video_input, audio_input = video_sources.resolved_streams(info, ydl.cookiejar)
            metadata = {k: info.get(k) for k in ('id', 'title', 'description', 'duration', 'uploader', 'webpage_url', 'extractor_key')}
            metadata['video_saved'] = False
            atomic_json(base / 'video-info.json', metadata)
            change(jid, title='直接抽帧 · ' + (info.get('title') or '网络视频'), progress=10,
                   message='正在读取视频流并抽帧；不会保存视频文件')
            warnings = extract_frames(jid, None, req['frames'], video_input, audio_input, info.get('duration'))
        return '直接抽帧完成，未保存视频文件。' + ' '.join(warnings)
    finally:
        if not imported: cookies_path.unlink(missing_ok=True)


def download_video(jid, req):
    runtime_support.require('download')
    from yt_dlp import YoutubeDL
    job = job_by_id(jid)
    base = Path(job['directory'])
    imported = ANALYSIS_ENGINE.cookie_path(req.get('cookie_id', ''))
    cookies_path = imported or browser_cookie_file(jid, job['source'], req['login'])

    last_update = [0.0]
    def progress(info):
        check_cancel(jid)
        now = time.monotonic()
        if now - last_update[0] < .6:
            return
        last_update[0] = now
        total = info.get('total_bytes') or info.get('total_bytes_estimate') or 0
        percent = min(95, info.get('downloaded_bytes', 0) / total * 95) if total else 0
        change(jid, progress=round(percent), message='下载音视频中…', progress_indeterminate=not bool(total),
               progress_detail={'stage':'采集视频','downloaded_bytes':info.get('downloaded_bytes',0),'total_bytes':total,
                                'speed':info.get('speed'),'eta':info.get('eta')})

    moved = []
    opts = video_sources.ydl_options(DATA, cookies_path)
    opts.update(outtmpl=str(base / 'video.%(ext)s'), format=video_sources.format_selector(req.get('max_height', 0)),
                merge_output_format='mp4', ffmpeg_location=FFMPEG, quiet=True,
                progress_hooks=[progress], socket_timeout=25,
                retries=2, fragment_retries=2, concurrent_fragment_downloads=1,
                postprocessor_hooks=[lambda d: check_cancel(jid)],
                post_hooks=[lambda filename: moved.append(filename)],
                writesubtitles=True, writeautomaticsub=True, subtitleslangs=['zh.*', 'en.*'],
                windowsfilenames=True)
    if cookies_path.exists():
        opts['cookiefile'] = str(cookies_path)
    try:
        with YoutubeDL(opts) as ydl:
            info = video_sources.resolve_info(ydl, job['source'], BROWSER if req.get('login') and not imported else None)
            if info and info.get('entries'):
                info = next((e for e in info['entries'] if e), None)
            if not info:
                raise RuntimeError('未取得视频')
            ydl.process_info(info)
            change(jid, title=info.get('title') or job['title'], message='整理素材中')
            source = next((Path(p) for p in reversed(moved) if Path(p).is_file()), None)
            if source is None:
                source = next((p for p in base.glob('video.*') if p.suffix in ('.mp4', '.webm', '.mkv', '.flv', '.mov')), None)
            if not source:
                raise RuntimeError('未找到下载的视频文件')
            metadata = {k: info.get(k) for k in ('id', 'title', 'description', 'duration', 'uploader', 'webpage_url')}
            atomic_json(base / 'video-info.json', metadata)
        if req.get('extract'):
            warnings = extract_frames(jid, source, req['frames'])
            return '下载、抽帧完成。' + ' '.join(warnings)
        write_bundle(jid)
        return '视频下载完成，已有字幕会一并保存'
    finally:
        if not imported: cookies_path.unlink(missing_ok=True)


app = FastAPI(title='Video Desk', docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])


@app.on_event('shutdown')
def close_login_browser():
    BROWSER.close()


@app.middleware('http')
async def local_guard(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('x-app-token') != TOKEN:
            return JSONResponse({'detail': '本机会话失效，请刷新网页'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
        if REPAIR_PENDING and request.url.path not in ('/api/runtime-repair','/api/shutdown'):
            return JSONResponse({'detail':'修复已开始，请等待新的工作台页面打开后再操作'},status_code=409)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/state')
def state():
    with LOCK:
        return {'token': TOKEN, 'settings': dict(SETTINGS), 'jobs': list(reversed(list(JOBS.values()))),
                'ffmpeg': bool(FFMPEG), 'version': os.environ.get('VIDEO_DESK_VERSION', '2.5.1'),
                'application': 'video-desk', 'installation_id': os.environ.get('VIDEO_DESK_INSTALLATION_ID', ''),
                'engines': {'yt_dlp': runtime_support.available('download'), 'mediacrawler': crawler_available()},
                'youtube': video_sources.youtube_support(), 'runtime':runtime_support.status()}


@app.get('/api/runtime-status')
def runtime_status():
    return runtime_support.status()


@app.post('/api/runtime-repair')
def runtime_repair():
    global REPAIR_PENDING
    if os.environ.get('VIDEO_DESK_LAUNCHER')!='1':
        raise HTTPException(409,'请使用新版 EXE 启动器进行检测并修复')
    # The launcher repeats this check immediately before stopping its server.
    with LOCK:
        if any(j['status'] in ('queued','running','recording','processing','waiting','waiting_source') for j in JOBS.values()):
            raise HTTPException(409,'还有任务正在进行，请等待完成或取消任务后再修复')
        if not REPAIR_PENDING:
            (DATA/'repair.request').write_text('repair',encoding='ascii')
            REPAIR_PENDING=True
    return {'ok':True,'message':'修复将重启工作台并自动打开新页面，请保留系统托盘中的映格。账号、设置与素材会保留。'}


@app.get('/api/third-party/mediacrawler-license')
def crawler_license():
    return FileResponse(CRAWLER_ROOT / 'LICENSE', media_type='text/plain; charset=utf-8')


class DedupRequest(BaseModel):
    source: str = ''
    source_job: str = ''
    output: str = ''
    mode: str = 'exact'
    threshold: int = Field(default=4, ge=0, le=16)
    recursive: bool = True
    limit: int = Field(default=20000, ge=1, le=100000)


@app.post('/api/dedup')
def start_dedup(req: DedupRequest):
    if req.mode not in ('exact', 'similar'):
        raise HTTPException(400, '请选择完全重复或相似图片模式')
    source = req.source
    if req.source_job:
        previous = job_by_id(req.source_job)
        if previous['status'] in ('running', 'queued', 'recording', 'processing', 'waiting'):
            raise HTTPException(409, '请等待素材任务完成后再扫描')
        source = previous['directory']
    try:
        root = image_dedup.source_root(source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = new_job('dedup', '照片去重 · ' + root.name, req.output, str(root), req.model_dump())
    jid = job['id']
    def action():
        report = image_dedup.scan(root, job['directory'], req.mode, req.threshold,
                                 req.recursive, req.limit, lambda: check_cancel(jid),
                                 lambda p, m: change(jid, progress=p, message=m))
        change(jid, dedup_summary=report['summary'], dedup_ready=True)
        suffix = '；已达到扫描上限，请分批扫描' if report['truncated'] else ''
        return f"扫描 {report['summary']['images']} 张，找到 {report['summary']['duplicates']} 张重复候选，原图未变更" + suffix
    DEDUP.submit(guarded, jid, action)
    return {'id': jid}


def dedup_job(jid):
    job = job_by_id(jid)
    if job['kind'] != 'dedup' or not (Path(job['directory']) / 'dedup-report.json').is_file():
        raise HTTPException(404, '去重报告尚未生成')
    return job


@app.get('/api/dedup/{jid}')
def dedup_report(jid: str):
    job = dedup_job(jid)
    with SESSION_LOCKS[jid]:
        return image_dedup.read_report(job['directory'])


@app.get('/api/dedup/{jid}/thumbnail/{iid}')
def dedup_thumbnail(jid: str, iid: str):
    job = dedup_job(jid)
    if not re.fullmatch(r'\d{8}', iid):
        raise HTTPException(404, '预览不存在')
    path = Path(job['directory']) / '.dedup-thumbs' / (iid + '.jpg')
    if not path.is_file():
        raise HTTPException(404, '预览不存在')
    return FileResponse(path, media_type='image/jpeg')


@app.get('/api/dedup/{jid}/image/{iid}')
def dedup_original(jid: str, iid: str):
    job = dedup_job(jid)
    report = image_dedup.read_report(job['directory'])
    item = next((i for i in report['items'] if i['id'] == iid), None)
    if not item:
        raise HTTPException(404, '图片不存在')
    try:
        root = image_dedup.source_root(report['source'])
        path = image_dedup.inside(root, item['relative'])
        if not path.is_file():
            for op in reversed(report['operations']):
                if op['action'] != 'quarantine' or op['state'] == 'restored':
                    continue
                for entry in op['entries']:
                    if entry['id'] == iid:
                        target = image_dedup.inside(root, Path(op['directory']).relative_to(root))
                        path = image_dedup.inside(target, entry['destination'])
                        break
        if not path.is_file() or image_dedup.digest(path) != item['sha256']:
            raise ValueError('原图已变化或不存在，请重新扫描')
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path)


class DedupAction(BaseModel):
    action: str
    choices: dict[str, str] = Field(default_factory=dict)


@app.post('/api/dedup/{jid}/action')
def dedup_action(jid: str, req: DedupAction):
    job = dedup_job(jid)
    if req.action not in ('export', 'quarantine', 'restore'):
        raise HTTPException(400, '未知图片处理方式')
    with LOCK:
        if job['status'] in ('queued', 'running', 'processing'):
            raise HTTPException(409, '请等待当前去重操作完成')
        EVENTS[jid].clear()
        change(jid, status='queued', message='等待处理图片', progress=0)
    def action():
        with SESSION_LOCKS[jid]:
            result = image_dedup.operate(job['directory'], req.action, req.choices, jid,
                                       lambda: check_cancel(jid),
                                       lambda p, m: change(jid, progress=p, message=m))
        if req.action == 'restore':
            return f"已恢复 {result['count']} 张图片到原位置"
        return ('已导出去重副本，原图未变更：' if req.action == 'export' else '重复项已移入可恢复隔离区：') + result['directory']
    DEDUP.submit(guarded, jid, action)
    return {'id': jid}


class CrawlerRequest(BaseModel):
    platform: str = 'dy'
    mode: str = 'detail'
    query: str = Field(min_length=1, max_length=12000)
    max_items: int = Field(default=5, ge=1, le=50)
    comments: bool = False
    extract: bool = True
    use_login: bool = True
    cookie_id: str = ''
    output: str = ''
    frames: FrameOptions = Field(default_factory=FrameOptions)


@app.post('/api/crawler')
def crawler(req: CrawlerRequest):
    if not crawler_available():
        raise HTTPException(400, 'MediaCrawler 平台采集暂不可用，请从系统托盘打开“检测并修复”。源码运行可使用 setup.ps1。')
    if req.platform not in ('dy', 'bili') or req.mode not in ('detail', 'search', 'creator'):
        raise HTTPException(400, '请选择抖音 / B 站与支持的采集方式')
    if not req.query.strip():
        raise HTTPException(400, '请输入关键词、视频链接或作者主页')
    frame_options(req.frames)
    platform_name = '抖音' if req.platform == 'dy' else 'B 站'
    job = new_job('crawler', f'{platform_name}平台采集 · {req.mode}', req.output,
                  req.query, {'platform':req.platform, 'mode':req.mode, 'max_items':req.max_items})
    CRAWLERS.submit(guarded, job['id'], lambda: crawl_platform(job['id'], req.model_dump()))
    return {'id':job['id']}


class SettingsRequest(BaseModel):
    output: str


@app.post('/api/settings')
def settings(req: SettingsRequest):
    dest = output_dir(req.output)
    with LOCK:
        SETTINGS['output'] = str(dest)
        atomic_json(DATA / 'settings.json', SETTINGS)
    return SETTINGS


async def legacy_picker(kind):
    task=LOCAL_SOURCES.start_picker(kind)
    try:
        while True:
            record=LOCAL_SOURCES.dialogs[task['id']]
            if record['status']!='pending':break
            await asyncio.sleep(.2)
        if record['status']=='error':raise HTTPException(400,record.get('error','选择器不可用，请重试'))
        paths=record.get('paths',[])
        return {'path':paths[0] if paths else '', 'paths':paths}
    except asyncio.CancelledError:
        record=LOCAL_SOURCES.dialogs[task['id']];record['status']='cancelled'
        proc=record.get('process')
        if proc and proc.poll() is None:proc.kill()
        raise


@app.post('/api/pick-directory')
async def pick_directory():
    return await legacy_picker('folder')


class BrowserRequest(BaseModel):
    url: str


@app.post('/api/browser/open')
async def browser_open(req: BrowserRequest):
    url = normalized_url(req.url)
    try:
        return await asyncio.wrap_future(BROWSER.open(url))
    except Exception as exc:
        raise HTTPException(400, str(exc)[:500]) from exc


class DownloadRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    output: str = ''
    login: bool = True
    cookie_id: str = ''
    extract: bool = False
    frames_only: bool = False
    max_height: int = Field(default=0, ge=0, le=4320)
    frames: FrameOptions = Field(default_factory=FrameOptions)


@app.post('/api/download')
def download(req: DownloadRequest):
    frame_options(req.frames)
    urls = list(dict.fromkeys(normalized_url(u) for u in req.urls if u.strip()))
    if not urls:
        raise HTTPException(400, '请至少输入一个链接')
    created = []
    for url in urls:
        job = new_job('stream' if req.frames_only else 'download',
                      ('直接抽帧 · ' if req.frames_only else '链接下载 · ') + (urlparse(url).hostname or ''), req.output, url,
                      {'frames': req.frames.model_dump() if req.extract or req.frames_only else None,
                       'video_saved': not req.frames_only, 'max_height': req.max_height})
        action = stream_frames if req.frames_only else download_video
        WORKERS.submit(guarded, job['id'], lambda j=job['id'], fn=action: fn(j, req.model_dump()))
        created.append(job['id'])
    return {'ids': created}


class LocalVideoRequest(BaseModel):
    path: str
    output: str = ''
    frames: FrameOptions = Field(default_factory=FrameOptions)


def checked_local_video(value):
    try:
        return video_sources.local_video(value)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/local-video/preview')
def register_local_preview(req: LocalVideoRequest):
    path = checked_local_video(req.path)
    key = secrets.token_urlsafe(24)
    with LOCK:
        if len(LOCAL_PREVIEWS) >= 100:
            LOCAL_PREVIEWS.pop(next(iter(LOCAL_PREVIEWS)))
        LOCAL_PREVIEWS[key] = path
    return {'url': '/api/local-video/preview/' + key, 'name': path.name}


@app.get('/api/local-video/preview/{key}')
def local_preview(key: str):
    with LOCK:
        path = LOCAL_PREVIEWS.get(key)
    if not path or not path.is_file():
        raise HTTPException(404, '预览不存在，请重新选择视频')
    return FileResponse(path)


@app.post('/api/extract-local')
def extract_local(req: LocalVideoRequest):
    source = checked_local_video(req.path)
    frame_options(req.frames)
    job = new_job('extract', '直接抽帧 · ' + source.name, req.output, str(source),
                  {**req.frames.model_dump(), 'video_saved': False, 'source_mode': 'local-path'})
    WORKERS.submit(guarded, job['id'], lambda: '抽帧完成，原视频未复制、未修改。' + ' '.join(
        extract_frames(job['id'], source, req.frames.model_dump())))
    return {'id': job['id']}


@app.post('/api/pick-video')
async def pick_video():
    return await legacy_picker('files')


@app.post('/api/upload-video')
async def upload_video(file: UploadFile = File(...), output: str = Form('')):
    ext=Path(file.filename or '').suffix.lower()
    if ext not in video_sources.VIDEO_EXTENSIONS:raise HTTPException(400,'请选择视频文件')
    job=new_job('import','导入副本 · '+Path(file.filename).name,output,options={'imported_video':True})
    target=Path(job['directory'])/('source'+ext)
    try:
        with target.open('wb') as stream:
            while block:=await file.read(4*1024*1024):stream.write(block)
        change(job['id'],source=str(target),status='done',message='视频副本已导入，可送往抽帧或 AI 分析')
        refresh_files(job['id'])
        return {'id':job['id']}
    except Exception:
        change(job['id'],status='error',message='副本导入中断，请重新选择')
        raise


@app.post('/api/extract')
async def extract(file: UploadFile = File(...), options: str = Form('{}'), output: str = Form('')):
    try:
        opts = frame_options(FrameOptions(**json.loads(options)))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, '抽帧参数不正确') from exc
    ext = Path(file.filename or '').suffix.lower()
    if ext not in ('.mp4', '.webm', '.mkv', '.mov', '.avi', '.flv', '.m4v', '.ts'):
        raise HTTPException(400, '请选择 mp4 / webm / mkv / mov 等视频文件')
    job = new_job('extract', '抽帧 · ' + Path(file.filename).name, output, options=opts.model_dump())
    target = Path(job['directory']) / ('source' + ext)
    try:
        with target.open('wb') as stream:
            while block := await file.read(4 * 1024 * 1024):
                stream.write(block)
    except Exception:
        change(job['id'], status='error', message='导入视频中断，请重试')
        raise
    def action():
        warnings = extract_frames(job['id'], target, opts.model_dump())
        return '抽帧完成。' + ' '.join(warnings)
    WORKERS.submit(guarded, job['id'], action)
    return {'id': job['id']}


class ExtractSavedRequest(BaseModel):
    file: str
    output: str = ''
    frames: FrameOptions = Field(default_factory=FrameOptions)


@app.post('/api/jobs/{jid}/extract')
def extract_saved(jid: str, req: ExtractSavedRequest):
    source = safe_file(job_by_id(jid), req.file)
    if source.suffix.lower() not in ('.mp4', '.webm', '.mkv', '.mov', '.avi', '.flv', '.m4v', '.ts'):
        raise HTTPException(400, '请选择视频文件')
    frame_options(req.frames)
    job = new_job('extract', '抽帧 · ' + source.name, req.output, options=req.frames.model_dump())
    WORKERS.submit(guarded, job['id'], lambda: ('抽帧完成。' + ' '.join(extract_frames(job['id'], source, req.frames.model_dump()))))
    return {'id': job['id']}


class CaptureRequest(BaseModel):
    title: str = Field(default='网页采集', max_length=200)
    output: str = ''
    source: str = Field(default='', max_length=3000)
    options: dict = Field(default_factory=dict)


@app.post('/api/capture')
def capture(req: CaptureRequest):
    job = new_job('capture', req.title, req.output, req.source, req.options)
    change(job['id'], status='recording', message='网页实时采集中')
    return {'id': job['id']}


@app.post('/api/capture/{jid}/chunk')
async def chunk(jid: str, request: Request, index: int):
    job = job_by_id(jid)
    if not 0 <= index < 1000000:
        raise HTTPException(400, '分段编号错误')
    folder = Path(job['directory']) / '.chunks'
    folder.mkdir(exist_ok=True)
    temp = folder / f'{index:08d}.{uuid.uuid4().hex}.tmp'
    try:
        with temp.open('wb') as stream:
            async for block in request.stream():
                stream.write(block)
        # Finalization cannot race the commit of an in-flight upload.
        with SESSION_LOCKS[jid]:
            if job['kind'] != 'capture' or job['status'] != 'recording':
                raise HTTPException(409, '采集已结束')
            temp.replace(folder / f'{index:08d}.part')
    finally:
        temp.unlink(missing_ok=True)
    return {'saved': index}


@app.post('/api/capture/{jid}/shot')
async def shot(jid: str, request: Request, index: int, seconds: float):
    job = job_by_id(jid)
    if not 0 <= index < 1000000 or not math.isfinite(seconds) or seconds < 0:
        raise HTTPException(400, '截图参数错误')
    blob = await request.body()
    if len(blob) > 30 * 1024 * 1024:
        raise HTTPException(413, '单张截图过大')
    if not blob.startswith(b'\xff\xd8\xff'):
        raise HTTPException(400, '截图必须是 JPEG')
    with SESSION_LOCKS[jid]:
        if job['kind'] != 'capture' or job['status'] != 'recording':
            raise HTTPException(409, '采集已结束')
        folder = Path(job['directory']) / 'frames'
        folder.mkdir(exist_ok=True)
        name = f'{index:06d}_{seconds:012.3f}s.jpg'
        (folder / name).write_bytes(blob)
        timeline_path = Path(job['directory']) / 'timeline.json'
        timeline = load_json(timeline_path, [])
        entry = {'file': 'frames/' + name, 'seconds': round(seconds, 3), 'clock': 'capture_elapsed'}
        timeline = [e for e in timeline if e['file'] != entry['file']]
        timeline.append(entry)
        atomic_json(timeline_path, sorted(timeline, key=lambda e: e['seconds']))
        change(jid, frames=len(timeline), message=f'已保存 {len(timeline)} 张截图')
    return {'saved': name}


class FinishRequest(BaseModel):
    chunks: int = Field(default=0, ge=0, le=1000000)
    mp4: bool = True
    audio: bool = True


@app.post('/api/capture/{jid}/finish')
def finish_capture(jid: str, req: FinishRequest):
    job = job_by_id(jid)
    with SESSION_LOCKS[jid]:
        if job['kind'] != 'capture' or job['status'] != 'recording':
            raise HTTPException(409, '当前采集已经结束或正在保存')
        folder = Path(job['directory']) / '.chunks'
        if req.chunks and any(not (folder / f'{i:08d}.part').is_file() for i in range(req.chunks)):
            raise HTTPException(409, '部分视频分段尚未上传成功，请保持网页打开并重试保存。')
        change(jid, status='processing', message='正在整理视频与图片')

    def action():
        base = Path(job['directory'])
        warnings = []
        if req.chunks:
            raw = base / 'recording.webm'
            with raw.open('wb') as out:
                for i in range(req.chunks):
                    check_cancel(jid)
                    with (folder / f'{i:08d}.part').open('rb') as src:
                        shutil.copyfileobj(src, out)
            if req.mp4:
                try:
                    run_ffmpeg(jid, ['-i', str(raw), '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
                        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-c:a', 'aac',
                        '-movflags', '+faststart', str(base / 'recording.mp4')])
                except RuntimeError:
                    (base / 'recording.mp4').unlink(missing_ok=True)
                    warnings.append('MP4 转换失败，原始 WebM 已保留。')
            if req.audio:
                try:
                    run_ffmpeg(jid, ['-i', str(raw), '-vn', '-ac', '1', '-ar', '16000',
                                     '-c:a', 'aac', str(base / 'audio.m4a')])
                except RuntimeError:
                    (base / 'audio.m4a').unlink(missing_ok=True)
                    warnings.append('录制没有可用音轨。请在共享标签页时勾选共享音频。')
            for part in folder.glob('*.part'):
                part.unlink()
        write_bundle(jid, {'timeline_clock': 'capture_elapsed', 'warnings': warnings})
        return '采集完成。' + ' '.join(warnings)
    WORKERS.submit(guarded, jid, action)
    return {'id': jid}


@app.post('/api/jobs/{jid}/cancel')
def cancel(jid: str):
    job = job_by_id(jid)
    if job['kind'] in ('batch', 'batch-video'):
        return BATCH_PIPELINE.cancel(jid)
    if job['kind'] == 'capture' and job['status'] == 'recording':
        raise HTTPException(409, '请在采集页面使用停止并保存，避免丢失录制分段')
    EVENTS[jid].set()
    if job['kind']=='analysis' and job['status']=='waiting':
        change(jid,status='cancelled',message='已取消等待人工接管，已保存文件仍保留')
    return {'message': '已请求取消'}


@app.post('/api/jobs/{jid}/resume')
def resume(jid: str):
    job = job_by_id(jid)
    if job['kind'] in ('batch', 'batch-video'):
        return BATCH_PIPELINE.retry(jid)
    if job['kind'] == 'analysis':
        return ANALYSIS_ENGINE.resume(jid)
    if job['kind'] != 'crawler' or job['status'] != 'waiting':
        raise HTTPException(409, '当前任务没有等待人工验证')
    control_path = DATA / ('crawler-' + jid + '-control.json')
    control_path.with_suffix('.resume').write_text('resume', encoding='utf-8')
    return {'message': '已通知采集引擎继续'}


@app.post('/api/jobs/{jid}/open-folder')
def open_folder(jid: str):
    path = Path(job_by_id(jid)['directory'])
    if not path.is_dir():
        raise HTTPException(404, '保存目录已移动或删除')
    if sys.platform == 'win32':
        os.startfile(str(path))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
    return {'ok': True}


@app.get('/api/jobs/{jid}/files/{relative:path}')
def get_file(jid: str, relative: str):
    path = safe_file(job_by_id(jid), relative)
    if any(part.startswith('.') for part in Path(relative).parts):
        raise HTTPException(404)
    return FileResponse(path)


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.post('/api/shutdown')
def shutdown():
    if any(j['status'] in ('queued', 'running', 'recording', 'processing', 'waiting', 'waiting_source') for j in JOBS.values()):
        raise HTTPException(409, '请先停止并保存录制，等待任务完成或取消任务后退出')
    # The tray owner watches this signal and disposes only its own process tree.
    (DATA / 'shutdown.request').write_text('shutdown', encoding='ascii')
    if os.environ.get('VIDEO_DESK_LAUNCHER') != '1':
        import signal
        threading.Timer(1, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return {'ok': True}


ANALYSIS_ENGINE = storyboard.install(app, sys.modules[__name__])
LOCAL_SOURCES = local_sources.install(app, sys.modules[__name__])
MATERIAL_HISTORY = material_history.install(app, sys.modules[__name__])
BATCH_PIPELINE = batch_pipeline.install(app, sys.modules[__name__], LOCAL_SOURCES)
MATERIAL_PACKAGES = material_packages.install(app, sys.modules[__name__])
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('VIDEO_DESK_PORT', '8765')), ws_max_size=1024*1024+32, ws_max_queue=2, ws_per_message_deflate=False)

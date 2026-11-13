"""Independent live capture pool, durable configuration and segmented replays."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from . import hardware, live_browser
from .storage import atomic_json

ZONE = timezone(timedelta(hours=8))


class Window(BaseModel):
    days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    start: str = '00:00'
    end: str = '00:00'

    @model_validator(mode='after')
    def validate_window(self):
        import re
        if any(d < 0 or d > 6 for d in self.days) or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in (self.start, self.end)):
            raise ValueError('请选择周一至周日和有效的开始、结束时间')
        return self


class Anchor(BaseModel):
    id: str = ''
    name: str = Field(default='', max_length=100)
    url: str = Field(max_length=2000)
    enabled: bool = False
    quality: str = 'origin'
    windows: list[Window] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def validate_anchor(self):
        self.url = live_browser.normalize(self.url)
        if self.quality not in ('origin', 'high', 'standard'): raise ValueError('未知直播画质')
        return self


class LiveSettings(BaseModel):
    concurrency: int = Field(default=4, ge=1, le=8)
    output: str = ''
    segment_minutes: int = Field(default=30, ge=1, le=120)


def in_window(windows, now=None):
    if not windows: return True
    now = now or datetime.now(ZONE)
    minute = now.strftime('%H:%M')
    for window in windows:
        start, end, days = window['start'], window['end'], window['days']
        if start == end and now.weekday() in days: return True
        if start < end and now.weekday() in days and start <= minute < end: return True
        if start > end and ((now.weekday() in days and minute >= start) or ((now.weekday()-1) % 7 in days and minute < end)): return True
    return False


class Manager:
    def __init__(self, a):
        self.a = a
        self.path = a.DATA/'live-settings.json'
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='live')
        raw = a.load_json(self.path, {})
        if not isinstance(raw, dict): raw = {}
        try: self.settings = LiveSettings(**raw.get('settings', {})).model_dump()
        except (ValueError, TypeError): self.settings = LiveSettings().model_dump()
        self.anchors = {}
        for value in raw.get('anchors', []) if isinstance(raw.get('anchors', []), list) else []:
            try:
                item = Anchor(**value).model_dump()
                if item['id']: self.anchors[item['id']] = item
            except (ValueError, TypeError): pass
        self.active = {}
        self.pending = {}
        self.runtime = {}
        self.next_check = {}
        self.last_check_job = {}
        self.thread = None
        # Recover only the exact in-progress file persisted before capture.
        for job in list(a.JOBS.values()):
            pending = job.get('live_pending')
            if job['kind'] != 'live-recording' or not pending or job['status'] != 'interrupted': continue
            try:
                path = a.safe_file(job, pending['name'])
                if path.stat().st_size > 1024:
                    segments = [*job.get('replay', []), {**pending, 'preview': pending['name'], 'complete': False,
                                'gap_before': True, 'ended': job.get('updated', time.time())}]
                    a.change(job['id'], replay=segments, live_pending=None)
                    a.refresh_partial_files(job['id'])
            except Exception: pass

    def save(self):
        atomic_json(self.path, {'settings': self.settings, 'anchors': list(self.anchors.values())}, backup=True)

    def state(self):
        with self.lock:
            return {'settings': dict(self.settings), 'anchors': [dict(v, runtime=dict(self.runtime.get(k, {})),
                    active_job=self.active.get(k) or self.pending.get(k)) for k, v in self.anchors.items()]}

    def put(self, req):
        item = req.model_dump()
        with self.lock:
            existing = next((v for v in self.anchors.values() if v['url'].rstrip('/') == item['url'].rstrip('/')), None)
            if item['id'] and item['id'] not in self.anchors: raise HTTPException(404, '主播不存在')
            if existing and existing['id'] != item['id']:
                raise HTTPException(409, '此主播已在列表中，请修改已有设置')
            if not item['id']: item['id'] = uuid.uuid4().hex[:12]
            item['name'] = item['name'] or '抖音主播'
            self.anchors[item['id']] = item
            self.save()
            self.next_check[item['id']] = 0
            return item

    def start_service(self):
        if self.thread and self.thread.is_alive(): return
        self.thread = threading.Thread(target=self.loop, name='live-monitor', daemon=True)
        self.thread.start()

    def loop(self):
        while not self.stopping.is_set():
            with self.lock:
                for key, jid in list(self.pending.items()):
                    if len(self.active) >= self.settings['concurrency']: break
                    self.start(key, queued_jid=jid)
                for key, item in list(self.anchors.items()):
                    allowed = item['enabled'] and in_window(item['windows'])
                    if key in self.active:
                        if self.runtime.get(key, {}).get('automatic') and not allowed:
                            self.a.EVENTS[self.active[key]].set()
                        continue
                    if allowed and time.monotonic() >= self.next_check.get(key, 0):
                        try: self.start(key, automatic=True)
                        except HTTPException: pass
            self.stopping.wait(2)

    def start(self, key, automatic=False, queued_jid=None):
        with self.lock:
            if key not in self.anchors: raise HTTPException(404, '主播不存在')
            if key in self.active: return {'id': self.active[key]}
            if key in self.pending and not queued_jid: return {'id': self.pending[key], 'queued': True}
            if len(self.active) >= self.settings['concurrency']:
                self.runtime[key] = {'message': '等待录播名额', 'state': 'queued'}
                if not automatic:
                    anchor = self.anchors[key]
                    job = self.a.new_job('live-recording', anchor['name']+' · 等待录播', self.settings['output'], anchor['url'],
                        {'anchor_id': key, 'automatic': False, 'quality': anchor['quality']})
                    self.pending[key] = job['id']
                    return {'id': job['id'], 'queued': True}
                return {}
            anchor = dict(self.anchors[key])
            previous = self.a.JOBS.get(self.last_check_job.get(key))
            if queued_jid:
                job = self.a.job_by_id(queued_jid); self.pending.pop(key, None)
            elif previous and not previous.get('replay') and previous['status'] == 'done':
                job = previous; self.a.EVENTS[job['id']].clear()
                self.a.change(job['id'], status='queued', message='再次检查开播状态')
            else:
                job = self.a.new_job('live-recording', anchor['name']+' · 直播录播', self.settings['output'], anchor['url'],
                                     {'anchor_id': key, 'automatic': automatic, 'quality': anchor['quality']})
            self.last_check_job[key] = job['id']
            self.active[key] = job['id']
            self.runtime[key] = {'message': '检查开播状态', 'state': 'checking', 'automatic': automatic}
            self.pool.submit(self.record, key, anchor, job['id'], automatic)
            return {'id': job['id']}

    def stop(self, key, disable=True):
        with self.lock:
            if key not in self.anchors: raise HTTPException(404, '主播不存在')
            if disable: self.anchors[key]['enabled'] = False; self.save()
            jid = self.active.get(key)
            if jid: self.a.EVENTS[jid].set()
            pending = self.pending.pop(key, None)
            if pending:
                self.a.EVENTS[pending].set()
                self.a.change(pending, status='cancelled', message='已取消等待录播')
            return {'message': '正在停止并保存；自动监控已关闭' if disable else '正在停止并保存'}

    def capture_segment(self, jid, info, target, duration):
        a = self.a
        # Headers remain private, never enter persisted jobs, evidence or command logs.
        header = 'Referer: '+info.get('referer', 'https://live.douyin.com/')+'\r\n'
        cookies = info.get('cookies', [])
        if cookies: header += 'Cookie: '+'; '.join(c['name']+'='+c['value'] for c in cookies)+'\r\n'
        args = [a.FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-rw_timeout', '20000000',
                '-user_agent', info.get('user_agent', 'VideoDesk'), '-headers', header, '-i', info['url'],
                '-t', str(duration), '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-f', 'matroska', str(target)]
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with a.LOCK: a.PROCESSES[jid] = proc
        interrupted = False
        try:
            while proc.poll() is None:
                if a.EVENTS[jid].wait(.5) or self.stopping.is_set() or shutil.disk_usage(target.parent).free < 2*1024**3:
                    interrupted = True
                    try: proc.stdin.write(b'q\n'); proc.stdin.flush(); proc.wait(timeout=12)
                    except (OSError, subprocess.TimeoutExpired):
                        proc.terminate()
                        try: proc.wait(timeout=5)
                        except subprocess.TimeoutExpired: proc.kill(); proc.wait()
                    break
            return proc.returncode, interrupted
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()
            if proc.stdin: proc.stdin.close()
            with a.LOCK: a.PROCESSES.pop(jid, None)

    def record(self, key, anchor, jid, automatic):
        a = self.a; base = Path(a.job_by_id(jid)['directory']); segments = []; failures = 0
        event = a.EVENTS[jid]
        try:
            a.change(jid, status='running', message='正在读取直播间')
            while not event.is_set() and not self.stopping.is_set():
                if shutil.disk_usage(base).free < 2*1024**3: raise ValueError('磁盘剩余不足 2 GB，录播已安全停止')
                try:
                    info = live_browser.resolve(a.BROWSER, anchor['url'], anchor['quality'], lambda:event.is_set() or self.stopping.is_set())
                except Exception:
                    failures += 1
                    if failures >= 5: raise ValueError('多次无法读取直播。请打开主播链接完成登录或验证，再重新启动监控')
                    a.change(jid, status='waiting', message='直播间暂不可用，稍后重试；请检查登录浏览器')
                    event.wait(min(60, 5*2**failures)); continue
                if not info.get('online'):
                    if not segments:
                        a.change(jid, status='done', progress=100, message='当前未开播，自动监控将继续检查' if automatic else '当前未开播，可开启自动监控')
                        return
                    break
                canonical = info.get('room_id')
                with self.lock:
                    duplicate = any(k != key and v.get('room_id') == canonical for k, v in self.runtime.items()
                                    if canonical and k in self.active)
                    if not duplicate: self.runtime[key] = {'state': 'recording', 'room_id': canonical, 'automatic': automatic, 'message': '录制中'}
                if duplicate: raise ValueError('这个直播间已由另一条主播记录录制，已避免重复录制')
                number = len(segments)+1
                target = base/f'part-{number:04d}-{uuid.uuid4().hex[:8]}.mkv'
                started = time.time()
                a.change(jid, status='recording', message=f'正在录制第 {number} 段', progress_indeterminate=True,
                         live_pending={'name': target.name, 'started': started})
                code, stopped = self.capture_segment(jid, info, target, self.settings['segment_minutes']*60)
                if target.is_file() and target.stat().st_size > 1024:
                    entry = {'name': target.name, 'started': started, 'ended': time.time(), 'gap_before': failures > 0,
                             'complete': code == 0, 'preview': target.name}
                    # Stop is graceful: remux a completed segment even when cancel was requested.
                    out = target.with_suffix('.mp4')
                    try:
                        p = subprocess.run([a.FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(target),
                                            '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-movflags', '+faststart', str(out)],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        if p.returncode == 0 and out.stat().st_size > 1024:
                            entry['preview'] = out.name
                        else: out.unlink(missing_ok=True)
                    except (OSError, subprocess.TimeoutExpired): out.unlink(missing_ok=True)
                    segments.append(entry)
                    atomic_json(base/'replay.json', {'anchor': anchor['name'], 'segments': segments})
                    a.change(jid, replay=segments, live_pending=None)
                    a.refresh_files(jid)
                if stopped:
                    if not event.is_set() and not self.stopping.is_set(): raise ValueError('磁盘剩余不足 2 GB，录播已安全停止')
                    break
                if code != 0:
                    failures += 1
                    a.change(jid, message='直播连接中断，重新获取地址后续录，已有片段保留')
                    if failures >= 5: raise ValueError('直播连续断开，已有片段保留，请检查网络或重新登录')
                    event.wait(min(60, 5*2**failures))
                else: failures = 0
            a.change(jid, status='done', progress=100, message=f'已保存 {len(segments)} 段回放', progress_indeterminate=False)
        except Exception as exc:
            # Deliberately no third-party exception details or signed URLs.
            message = str(exc) if isinstance(exc, ValueError) else '录播未完成，请检查登录、网络和保存目录；已有片段保留'
            a.change(jid, status='error', message=message[:500])
        finally:
            a.refresh_partial_files(jid)
            with self.lock:
                self.active.pop(key, None)
                self.next_check[key] = time.monotonic()+(300 if failures >= 5 else 60)
                self.runtime[key] = {'state': 'idle', 'message': a.job_by_id(jid)['message']}

    def close(self):
        self.stopping.set()
        with self.lock:
            for jid in self.active.values(): self.a.EVENTS[jid].set()
            for jid in self.pending.values():
                self.a.EVENTS[jid].set(); self.a.change(jid, status='cancelled', message='服务退出，已取消等待录播')
            self.pending.clear()
        if self.thread: self.thread.join(timeout=4)
        self.pool.shutdown(wait=True, cancel_futures=True)


def install(app, a):
    manager = Manager(a)

    @app.on_event('startup')
    def startup(): manager.start_service()

    @app.get('/api/live')
    def state(): return manager.state()

    @app.post('/api/live/settings')
    def settings(req: LiveSettings):
        value = req.model_dump()
        value['output'] = str(a.output_dir(value['output']))
        with manager.lock: manager.settings = value; manager.save()
        return manager.state()

    @app.post('/api/live/anchors')
    def put(req: Anchor): return manager.put(req)

    @app.delete('/api/live/anchors/{key}')
    def delete(key: str):
        with manager.lock:
            if key in manager.active or key in manager.pending: raise HTTPException(409, '请先停止并保存此主播的录播')
            manager.anchors.pop(key, None); manager.save()
        return {'ok': True}

    @app.post('/api/live/anchors/{key}/start')
    def start(key: str): return manager.start(key)

    @app.post('/api/live/anchors/{key}/stop')
    def stop(key: str): return manager.stop(key)

    @app.post('/api/live/following')
    def following():
        try: return live_browser.import_following(a.BROWSER)
        except Exception: raise HTTPException(400, '关注列表未读取成功，请在登录浏览器打开关注列表，完成登录后重试')

    @app.get('/api/live/replays')
    def replays():
        with a.LOCK:
            return {'jobs': [dict(j) for j in reversed(list(a.JOBS.values())) if j['kind'] == 'live-recording' and j.get('replay')]}

    @app.post('/api/live/replays/{jid}/preview/{number}')
    def preview(jid: str, number: int):
        job = a.job_by_id(jid)
        if job['kind'] != 'live-recording' or number < 0 or number >= len(job.get('replay', [])): raise HTTPException(404)
        segment = job['replay'][number]
        source = a.safe_file(job, segment['name'])
        target = source.with_name(source.stem+'-preview.mp4')
        def work():
            hardware.encode(a, jid, ['-i', str(source), '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-pix_fmt', 'yuv420p'], ['-c:a', 'aac', '-movflags', '+faststart', str(target)])
            segment['preview'] = target.name
            a.change(jid, replay=job['replay'])
            return '兼容回放已生成'
        if job['status'] not in ('done', 'error', 'interrupted', 'cancelled'): raise HTTPException(409, '请先停止录播，再生成兼容回放')
        a.EVENTS[jid].clear()
        a.change(jid, status='queued', message='排队生成兼容回放')
        a.WORKERS.submit(a.guarded, jid, work)
        return {'id': jid}

    return manager

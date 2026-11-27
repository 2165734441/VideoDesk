"""Workbench extras: categories, scheduled enhancement and disk backup.

These features are deliberately kept outside the download pipeline.  A video
can be large and an enhancement can run for hours, so it gets its own bounded
executor and its own job record.  That keeps a conversion from blocking a live
recording or an ordinary download.
"""
from __future__ import annotations

import concurrent.futures
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import video_sources


CATEGORY_FILE = 'categories.json'
BACKUP_FILE = 'backup.json'
CATEGORY_DEFAULT = '未分类'
VIDEO_EXT = ('.mp4', '.mkv', '.mov', '.webm', '.m4v', '.avi', '.flv', '.ts')


class SettingsRequest(BaseModel):
    categories: list[str] | None = None
    backup_root: str = ''
    backup_after_done: bool | None = None


class CategoryRequest(BaseModel):
    category: str = Field(default=CATEGORY_DEFAULT, max_length=80)
    tags: list[str] = Field(default_factory=list)


class BackupRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
    target: str = ''
    all_done: bool = False


class QARequest(BaseModel):
    anchor_id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=300)
    sent_at: float = 0


class EnhanceRequest(BaseModel):
    job_id: str = ''
    file: str = Field(default='', max_length=500)
    source_path: str = Field(default='', max_length=1000)
    output: str = ''
    width: int = Field(default=0, ge=0, le=7680)
    height: int = Field(default=0, ge=0, le=4320)
    fps: float = Field(default=0, ge=0, le=120)
    drop_fps: bool = False
    bitrate: str = Field(default='', max_length=20)
    crf: int = Field(default=18, ge=0, le=51)
    preset: str = Field(default='veryfast', max_length=20)
    schedule_at: str = Field(default='', max_length=40)
    title: str = Field(default='视频增强', max_length=120)


def _clean_category(value: str) -> str:
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return text[:80] or CATEGORY_DEFAULT


def _safe_name(value: str, limit: int = 72) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', str(value or '')).strip()
    text = re.sub(r'\s+', ' ', text)
    return (text[:limit].rstrip(' .') or '素材')


def _parse_when(value: str) -> float:
    text = str(value or '').strip()
    if not text:
        return 0.0
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        stamp = float(text)
        if stamp > 1e12:
            stamp /= 1000
        return stamp
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise HTTPException(400, '开始时间格式不正确，请使用“2026-11-27 09:30”这样的时间') from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.timestamp()


def _video_files(job: dict[str, Any]) -> list[Path]:
    base = Path(job['directory'])
    if not base.is_dir():
        return []
    return [path for path in sorted(base.rglob('*'))
            if path.is_file() and path.suffix.lower() in VIDEO_EXT
            and not any(part.startswith('.') for part in path.relative_to(base).parts)]


class Extra:
    def __init__(self, a):
        self.a = a
        self.lock = threading.RLock()
        self.state_path = Path(a.DATA) / CATEGORY_FILE
        self.backup_path = Path(a.DATA) / BACKUP_FILE
        self.state = self._load()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='workbench')
        self._timers: dict[str, threading.Thread] = {}

    # -- persisted, non-job settings ------------------------------------
    def _load(self) -> dict[str, Any]:
        raw = self.a.load_json(self.state_path, {}) if hasattr(self.a, 'load_json') else {}
        if not isinstance(raw, dict):
            raw = {}
        cats = [CATEGORY_DEFAULT]
        for name in raw.get('categories', []) if isinstance(raw.get('categories', []), list) else []:
            name = _clean_category(name)
            if name not in cats:
                cats.append(name)
        backup = raw.get('backup_root') or ('K:\\VideoDesk备份' if Path('K:/').exists() else '')
        return {'categories': cats, 'backup_root': str(backup),
                'backup_after_done': bool(raw.get('backup_after_done', False))}

    def _persist(self):
        self.a.atomic_json(self.state_path, self.state)

    def category_names(self) -> list[str]:
        names = list(self.state['categories'])
        for job in self.a.JOBS.values():
            name = _clean_category(job.get('category') or '')
            if name and name not in names:
                names.append(name)
        return names

    def save_settings(self, req: SettingsRequest) -> dict[str, Any]:
        with self.lock:
            if req.categories is not None:
                cats = [CATEGORY_DEFAULT]
                for name in req.categories:
                    name = _clean_category(name)
                    if name not in cats:
                        cats.append(name)
                self.state['categories'] = cats
            if req.backup_root:
                path = Path(req.backup_root).expanduser()
                if not path.is_absolute():
                    raise HTTPException(400, '备份目录必须填写完整路径，例如 K:\\VideoDesk备份')
                path.mkdir(parents=True, exist_ok=True)
                self.state['backup_root'] = str(path)
            if req.backup_after_done is not None:
                self.state['backup_after_done'] = bool(req.backup_after_done)
            self._persist()
            return self.public()

    def public(self) -> dict[str, Any]:
        with self.lock:
            return {'categories': self.category_names(),
                    'backup_root': self.state.get('backup_root', ''),
                    'backup_after_done': bool(self.state.get('backup_after_done'))}

    # -- categories ------------------------------------------------------
    def set_category(self, jid: str, req: CategoryRequest) -> dict[str, Any]:
        job = self.a.job_by_id(jid)
        name = _clean_category(req.category)
        tags = [str(tag).strip()[:40] for tag in (req.tags or []) if str(tag).strip()][:8]
        self.a.change(jid, category=name, tags=tags)
        with self.lock:
            if name not in self.state['categories']:
                self.state['categories'].append(name)
                self._persist()
        return {'id': jid, 'category': name, 'tags': tags}

    # -- backup ----------------------------------------------------------
    def backup(self, req: BackupRequest) -> dict[str, Any]:
        target_text = req.target or self.state.get('backup_root', '')
        if not target_text:
            raise HTTPException(400, '请先设置备份目录，例如 K:\\VideoDesk备份')
        target = Path(target_text).expanduser()
        if not target.is_absolute():
            raise HTTPException(400, '备份目录必须填写完整路径')
        target.mkdir(parents=True, exist_ok=True)
        jobs = []
        for jid in req.job_ids:
            try:
                job = self.a.job_by_id(jid)
            except HTTPException:
                continue
            if job.get('kind') == 'backup':
                continue
            if req.all_done and job.get('status') != 'done':
                continue
            jobs.append(job)
        if req.all_done and not jobs:
            jobs = [job for job in self.a.JOBS.values()
                    if job.get('status') == 'done' and job.get('kind') != 'backup']
        if not jobs:
            raise HTTPException(400, '没有可备份的已完成任务')
        job = self.a.new_job('backup', '备份素材 · ' + str(len(jobs)) + ' 个任务',
                             str(target), '', {'source_job_ids': [j['id'] for j in jobs]})
        self.pool.submit(self.a.guarded, job['id'], lambda j=job['id'], items=jobs: self._copy_jobs(j, items))
        return {'id': job['id'], 'count': len(jobs), 'target': str(target)}

    def after_done(self, job: dict[str, Any] | None) -> None:
        """Queue one copy after a finished material job when the switch is on."""
        if not job or not self.state.get('backup_after_done'):
            return
        if job.get('kind') in ('backup', 'live-recording'):
            return
        if job.get('status') != 'done':
            return
        root = str(self.state.get('backup_root') or '').strip()
        if not root:
            return
        try:
            target = Path(root).expanduser()
            if not target.is_absolute():
                return
            if not _video_files(job):
                return
            with self.lock:
                for other in self.a.JOBS.values():
                    if other.get('kind') == 'backup' and job['id'] in (other.get('options') or {}).get('source_job_ids', []):
                        return
            self.backup(BackupRequest(job_ids=[job['id']], target=root))
        except Exception:
            # A backup failure must never turn a finished material job into an error.
            return

    def _copy_jobs(self, jid: str, jobs: list[dict[str, Any]]) -> str:
        dest_root = Path(self.a.job_by_id(jid)['directory'])
        copied = 0
        for source_job in jobs:
            self.a.check_cancel(jid)
            src = Path(source_job['directory'])
            if not src.is_dir():
                continue
            dest = dest_root / _safe_name(source_job.get('title') or source_job['id'])
            if dest.exists():
                dest = dest_root / (dest.name + '_' + source_job['id'][:6])
            shutil.copytree(src, dest, dirs_exist_ok=True)
            copied += 1
        self.a.change(jid, progress=100, message=f'已复制 {copied} 个任务的素材到备份目录')
        return f'备份完成：{copied} 个任务已复制到 {dest_root}'

    # -- enhancement -----------------------------------------------------
    def _choose_source(self, req: EnhanceRequest) -> Path:
        if req.source_path.strip():
            try:
                return video_sources.local_video(req.source_path)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if not req.job_id:
            raise HTTPException(400, '请选择素材任务，或填写本机视频路径')
        job = self.a.job_by_id(req.job_id)
        if req.file:
            try:
                return self.a.safe_file(job, req.file)
            except HTTPException:
                raise HTTPException(404, '所选文件不在该任务目录中')
        files = _video_files(job)
        if not files:
            raise HTTPException(400, '这个任务里没有可处理的视频文件')
        return max(files, key=lambda p: p.stat().st_size)

    def _filters(self, req: EnhanceRequest) -> list[str]:
        filters = []
        width, height = int(req.width), int(req.height)
        if width or height:
            if width and height:
                filters.append(f'scale={width}:{height}:force_original_aspect_ratio=decrease')
                filters.append(f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black')
            else:
                filters.append(f'scale={width or -2}:{height or -2}:flags=lanczos')
            filters.append('unsharp=5:5:0.6:5:5:0.0')
        if req.fps:
            # minterpolate invents frames for smooth slow-motion; the phone's
            # low-frame-rate profile needs the opposite: drop frames and keep
            # every original audio sample (see _run).
            if req.drop_fps:
                filters.append(f'fps={req.fps:g}')
            else:
                filters.append(f'minterpolate=fps={req.fps:g}:mi_mode=mci:mc_mode=aobmc:vsbmc=1')
        return filters

    def create(self, req: EnhanceRequest) -> dict[str, Any]:
        source = self._choose_source(req)
        filters = self._filters(req)
        if not filters and not req.bitrate:
            raise HTTPException(400, '请至少选择分辨率、帧率或码率中的一项')
        if req.bitrate and not re.fullmatch(r'\d+(?:\.\d+)?[kKmM]?', req.bitrate.strip()):
            raise HTTPException(400, '码率请填写例如 2M、800k 或 1500000')
        when = _parse_when(req.schedule_at)
        job = self.a.new_job('enhance', req.title or ('视频增强 · ' + source.name),
                             req.output, str(source), {
                                 'source_job': req.job_id,
                                 'file': req.file,
                                 'width': req.width, 'height': req.height,
                                 'fps': req.fps, 'bitrate': req.bitrate,
                                 'drop_fps': req.drop_fps,
                                 'crf': req.crf, 'preset': req.preset,
                                 'schedule_at': when,
                             })
        self.a.change(job['id'], category=_clean_category(self.a.job_by_id(req.job_id).get('category')
                                                          if req.job_id else CATEGORY_DEFAULT))
        if when > time.time() + 1:
            self.a.change(job['id'], status='waiting', message='等待定时开始：' + req.schedule_at)
            thread = threading.Thread(target=self._wait_and_run, args=(job['id'], when), daemon=True,
                                      name='schedule-' + job['id'])
            self._timers[job['id']] = thread
            thread.start()
        else:
            self.pool.submit(self.a.guarded, job['id'], lambda j=job['id'], r=req: self._run(j, r))
        return {'id': job['id'], 'source': str(source), 'schedule_at': when,
                'message': '已建立视频增强任务' if when <= time.time() + 1 else '已设置定时任务'}

    def _wait_and_run(self, jid: str, when: float):
        try:
            while time.time() < when:
                if self.a.EVENTS.get(jid) and self.a.EVENTS[jid].is_set():
                    return
                time.sleep(min(20, max(1, when - time.time())))
            if self.a.EVENTS.get(jid) and self.a.EVENTS[jid].is_set():
                return
            self.a.change(jid, status='queued', message='定时时间到，开始处理')
            self.pool.submit(self.a.guarded, jid, lambda j=jid: self._run(j, self._request_for(j)))
        finally:
            self._timers.pop(jid, None)

    def _request_for(self, jid: str) -> EnhanceRequest:
        options = self.a.job_by_id(jid).get('options', {})
        return EnhanceRequest(job_id=options.get('source_job', ''), file=options.get('file', ''),
                              width=options.get('width', 0), height=options.get('height', 0),
                              fps=options.get('fps', 0), drop_fps=bool(options.get('drop_fps', False)),
                              bitrate=options.get('bitrate', ''),
                              crf=options.get('crf', 18), preset=options.get('preset', 'veryfast'))

    def _run(self, jid: str, req: EnhanceRequest) -> str:
        job = self.a.job_by_id(jid)
        source = Path(job['source'])
        dest = Path(job['directory']) / 'enhanced.mp4'
        filters = self._filters(req)
        args = ['-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-i', str(source)]
        if filters:
            args += ['-vf', ','.join(filters)]
        args += ['-c:v', 'libx264', '-preset', req.preset or 'veryfast', '-crf', str(req.crf)]
        if req.bitrate:
            args += ['-b:v', req.bitrate.strip()]
        if req.drop_fps:
            # Keeping the encoded audio stream preserves every audio sample and
            # avoids re-encoding or resampling when only video frames are reduced.
            args += ['-c:a', 'copy', '-movflags', '+faststart', str(dest)]
        else:
            args += ['-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(dest)]
        self.a.change(jid, progress=3, message='正在处理视频（插帧/缩放可能较慢，请保持电脑不休眠）')
        self.a.run_ffmpeg(jid, args)
        if not dest.is_file() or dest.stat().st_size == 0:
            raise RuntimeError('没有生成有效的增强视频')
        return f'视频增强完成：{dest.name}（{dest.stat().st_size / 1024 / 1024:.1f} MB）'

    def qa_mark(self, req: QARequest) -> dict[str, Any]:
        """Create a private question/answer folder and, when possible, a clip.

        The actual comment is sent manually by the user.  This endpoint only
        records the marker and copies a short segment from a recording that is
        already on this computer; it never posts to a live room.
        """
        sent = float(req.sent_at or time.time())
        if sent > 1e12:
            sent /= 1000
        with self.a.LIVE.lock:
            anchor = dict(self.a.LIVE.anchors.get(req.anchor_id) or {})
        if not anchor:
            raise HTTPException(404, '主播不存在或已删除')
        job_id = self.a.LIVE.active.get(req.anchor_id) or self.a.LIVE.pending.get(req.anchor_id)
        job = self.a.JOBS.get(job_id)
        anchor_name = _safe_name(anchor.get('name') or '主播', 40)
        stamp = datetime.fromtimestamp(sent).strftime('%Y%m%d-%H%M%S')
        folder_name = _safe_name(stamp + ' ' + req.question, 64)
        if job:
            folder = Path(job['directory']) / '提问—回答' / folder_name
        else:
            folder = Path(self.a.SETTINGS['output']) / '直播复盘' / anchor_name / '提问—回答' / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        clip = None
        note = '没有找到正在进行的电脑录制；已保存提问标记和目录，手机录制时请手动回看。'
        replay = (job or {}).get('replay') or []
        candidates = []
        for entry in replay:
            try:
                started = float(entry.get('started') or 0)
            except (TypeError, ValueError):
                continue
            if started and started <= sent + 8:
                candidates.append((started, entry))
        if candidates:
            started, entry = max(candidates, key=lambda item: item[0])
            source = Path(job['directory']) / str(entry.get('preview') or entry.get('name') or '')
            if source.is_file():
                offset = max(0.0, sent - started - 5.0)
                total = max(0.0, float(entry.get('ended') or (started + 60)) - started)
                duration = max(8.0, min(45.0, total - offset))
                target = folder / '提问片段.mp4'
                args = [self.a.FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                        '-ss', f'{offset:.2f}', '-t', f'{duration:.2f}', '-i', str(source),
                        '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-movflags', '+faststart', str(target)]
                try:
                    done = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          timeout=150, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if done.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
                        target.unlink(missing_ok=True)
                        raise RuntimeError('copy failed')
                    clip = str(target)
                    note = '已从正在录制的直播片段截取提问前后约 40 秒，仅保存在本机复盘目录。'
                except Exception:
                    try:
                        target.unlink(missing_ok=True)
                        args = [self.a.FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                                '-ss', f'{offset:.2f}', '-t', f'{duration:.2f}', '-i', str(source),
                                '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'veryfast',
                                '-crf', '20', '-c:a', 'aac', '-movflags', '+faststart', str(target)]
                        done = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                              timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        if done.returncode == 0 and target.is_file() and target.stat().st_size > 1024:
                            clip = str(target)
                            note = '已从正在录制的直播片段截取提问前后约 40 秒，仅保存在本机复盘目录。'
                    except Exception:
                        pass
        self.a.atomic_json(folder / 'qa.json', {
            'anchor_id': req.anchor_id, 'anchor_name': anchor_name,
            'question': req.question, 'sent_at': sent, 'sent_at_text': stamp,
            'source_job': job_id or '', 'clip': clip or '', 'note': note,
        })
        (folder / '说明.txt').write_text(
            f'提问：{req.question}\n标记时间：{stamp}\n\n{note}\n'
            '这段内容仅供个人复盘；提问由用户本人在直播间手动发送。\n',
            encoding='utf-8')
        return {'folder': str(folder), 'clip': clip or '', 'note': note,
                'message': '已保存提问标记与复盘目录' + ('，并截取片段' if clip else '（未找到可截取的电脑片段）')}

    def cancel(self, jid: str) -> dict[str, Any]:
        self.a.EVENTS[jid].set()
        thread = self._timers.pop(jid, None)
        if thread and thread.is_alive():
            self.a.change(jid, status='cancelled', message='已取消定时任务')
        return {'message': '已请求取消'}


def install(app, a):
    extra = Extra(a)

    # ``app.change`` is also looked up as a module global by guarded(), so
    # wrapping it here catches every terminal status transition, not only
    # requests that happen to call a.change.
    original_change = getattr(a, 'change')
    if not getattr(a, '_workbench_change_wrapped', False):
        def change_with_backup(jid, **values):
            original_change(jid, **values)
            if values.get('status') == 'done':
                try:
                    extra.after_done(a.JOBS.get(jid))
                except Exception:
                    pass
        setattr(a, 'change', change_with_backup)
        setattr(a, '_workbench_change_wrapped', True)

    @app.get('/api/workbench/settings')
    def get_workbench_settings():
        return extra.public()

    @app.post('/api/workbench/settings')
    def save_workbench_settings(req: SettingsRequest):
        return extra.save_settings(req)

    @app.get('/api/workbench/categories')
    def get_categories():
        return {'categories': extra.category_names()}

    @app.post('/api/jobs/{jid}/category')
    def set_job_category(jid: str, req: CategoryRequest):
        return extra.set_category(jid, req)

    @app.post('/api/workbench/backup')
    def backup_jobs(req: BackupRequest):
        return extra.backup(req)

    @app.post('/api/enhance')
    def enhance(req: EnhanceRequest):
        return extra.create(req)

    @app.post('/api/live/qa')
    def live_qa(req: QARequest):
        return extra.qa_mark(req)

    @app.post('/api/enhance/{jid}/cancel')
    def cancel_enhance(jid: str):
        return extra.cancel(jid)

    return extra

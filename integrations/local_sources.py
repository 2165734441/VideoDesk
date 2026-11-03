"""Local source capabilities and bounded browser File -> loopback Range transport.

Only metadata persists. One outstanding 1 MiB read per source, never a video copy.
"""
from __future__ import annotations
import asyncio
import base64
import concurrent.futures
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from .video_sources import VIDEO_EXTENSIONS, local_video, normalize_path, parse_paths
from .media_probe import inspect, ProbeError

CHUNK = 1024 * 1024


class SourceUnavailable(Exception):
    pass


def discover(folder, recursive=True, limit=10000):
    root = Path(normalize_path(folder)).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('文件夹不存在或无法读取')
    paths, skipped, stack = [], [], [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for item in entries:
                    try:
                        stat = item.stat(follow_symlinks=False)
                        if item.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
                            skipped.append({'name': item.path, 'reason': '已跳过目录联接 / 符号链接'})
                        elif item.is_dir(follow_symlinks=False):
                            if recursive: stack.append(Path(item.path))
                        elif Path(item.name).suffix.lower() in VIDEO_EXTENSIONS:
                            paths.append(item.path)
                            if len(paths) >= limit:
                                return sorted(paths), skipped + [{'name': str(root), 'reason': f'最多列出 {limit} 个视频，请分批导入'}]
                    except OSError:
                        skipped.append({'name': item.path, 'reason': '文件不可读'})
        except OSError:
            skipped.append({'name': str(current), 'reason': '目录无法读取'})
    return sorted(paths), skipped


class BrowserReader:
    def __init__(self, size):
        self.size = size
        self.ws = self.loop = None
        self.connected = False
        self.condition = threading.Condition()
        self.serial = threading.Lock()
        self.pending = None
        self.result = None
        self.peak_buffer = 0
        self.bytes_served = self.reads = 0
        self.last_error = ''

    def close(self, ws):
        with self.condition:
            if self.ws is ws:
                self.connected = False
                self.result = None
                self.condition.notify_all()

    def accept(self, data):
        """Ignore valid but late replies, including after timeout/reconnect."""
        with self.condition:
            if not data or len(data) < 32 or len(data) > CHUNK+32:
                raise ValueError('非法文件块')
            key = data[:32]
            if not re.fullmatch(b'[a-f0-9]{32}', key): raise ValueError('非法请求标识')
            if not self.pending or key != self.pending[0].encode('ascii'): return False
            if len(data)-32 != self.pending[1]: raise ValueError('文件块大小不符')
            self.result = data[32:]
            self.peak_buffer = max(self.peak_buffer, len(data)+len(self.result))
            self.condition.notify_all()
            return True

    def read(self, offset, length):
        length = min(CHUNK, length, self.size - offset)
        with self.serial:
            with self.condition:
                if not self.connected: raise SourceUnavailable('等待重新选择文件')
                try:
                    for timeout in (12, 30):
                        key = secrets.token_hex(16)
                        self.pending, self.result = (key, length), None
                        future = asyncio.run_coroutine_threadsafe(self.ws.send_json({'read': key, 'offset': offset, 'length': length}), self.loop)
                        deadline = time.monotonic() + timeout
                        while self.result is None and self.connected and time.monotonic() < deadline:
                            self.condition.wait(.25)
                        future.cancel()
                        if self.result is not None:
                            self.last_error=''
                            self.bytes_served+=len(self.result);self.reads+=1
                            return self.result
                        if not self.connected: break
                    self.last_error='网页文件读取超时或断开，请重新选择同一文件并继续'
                    raise SourceUnavailable(self.last_error)
                finally:
                    self.pending, self.result = None, None


class Sources:
    def __init__(self, a):
        self.a = a
        self.lock = threading.RLock()
        self.items = a.load_json(a.DATA / 'sources.json', {})
        self.readers, self.dialogs = {}, {}
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='sources')
        for item in self.items.values():
            if item['kind'] == 'browser': item['status'] = 'waiting_source'
        manager = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_HEAD(self): self.serve(True)
            def do_GET(self): self.serve(False)
            def serve(self, head):
                key = urlsplit(self.path).path.removeprefix('/media/')
                with manager.lock:
                    item = manager.items.get(key)
                    reader = manager.readers.get(key)
                if not item or not reader or not reader.connected:
                    self.send_error(503, 'Source unavailable'); return
                size = item['size']; start, end, partial = 0, size - 1, False
                value = self.headers.get('Range')
                if value:
                    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
                    if not match or not any(match.groups()): self.send_error(416); return
                    left, right = match.groups(); partial = True
                    if left: start, end = int(left), min(size-1, int(right)) if right else size-1
                    else: start = max(0, size-int(right))
                    if start >= size or end < start:
                        self.send_response(416); self.send_header('Content-Range', f'bytes */{size}'); self.send_header('Content-Length','0'); self.end_headers(); return
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type','application/octet-stream')
                self.send_header('Accept-Ranges','bytes')
                self.send_header('Content-Length',str(end-start+1))
                if partial: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.send_header('Connection','close'); self.end_headers()
                if head: return
                try:
                    while start <= end:
                        chunk = reader.read(start, min(CHUNK,end-start+1))
                        self.wfile.write(chunk)
                        start += len(chunk)
                except (SourceUnavailable, OSError, TimeoutError): pass
                finally: self.close_connection = True
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True, name='file-range').start()

    def save(self):
        with self.lock: self.a.atomic_json(self.a.DATA / 'sources.json', self.items)

    def get(self, sid):
        with self.lock:
            item = self.items.get(sid)
            if not item: raise HTTPException(404, '来源不存在，请重新选择视频')
            return dict(item)

    def public(self, item):
        reader = self.readers.get(item['id'])
        return {**item, 'connected': item['kind'] == 'path' or bool(reader and reader.connected)}

    def register(self, paths):
        result = []
        for value in paths:
            value = normalize_path(value)
            with self.lock:
                existing = next((i for i in self.items.values() if i['kind']=='path' and os.path.normcase(i.get('path',''))==os.path.normcase(value)),None)
                if existing:
                    item = existing
                else:
                    item = {'id': secrets.token_hex(24), 'kind':'path', 'path':value, 'name':Path(value).name, 'created':time.time()}
                    self.items[item['id']] = item
                try:
                    path = local_video(value); st = path.stat()
                    item.update(path=str(path),size=st.st_size,mtime_ns=st.st_mtime_ns,status='validating',error='')
                    self.pool.submit(self.validate,item['id'])
                except (ValueError,OSError) as exc:
                    category='permission_denied' if isinstance(exc,PermissionError) else 'missing_path' if '不存在' in str(exc) else 'container_error'
                    item.update(status='invalid',error=str(exc),error_category=category,diagnostic={'category':category,'message':str(exc)})
                result.append(self.public(item))
        self.save()
        return result

    def validate(self, sid):
        try:
            item = self.get(sid)
            source = self.address(sid)
            def retry():
                with self.lock: self.items[sid].update(status='validating',error='首次检查超时，正在延长检查（45 秒）')
                self.save()
            info = inspect(source,self.a.ANALYSIS_ENGINE.probe,lambda:self.connected(sid),retry)
            with self.lock: self.items[sid].update(status='ready',error='',duration=info['duration'],diagnostic=info['diagnostic'],error_category=None)
        except Exception as exc:
            reason=str(exc)[:350] or '视频不可读'
            category=exc.diagnostic['category'] if isinstance(exc,ProbeError) else 'source_disconnected' if isinstance(exc,SourceUnavailable) else 'missing_path' if isinstance(exc,(ValueError,FileNotFoundError)) else 'permission_denied' if isinstance(exc,PermissionError) else 'container_error'
            with self.lock:
                self.items[sid].update(status='waiting_source' if category in ('source_disconnected','probe_timeout') else 'invalid',error=reason,error_category=category,diagnostic=getattr(exc,'diagnostic',{'category':category,'message':reason}))
        self.save()

    def browser(self, value):
        name = str(value.get('name',''))
        try:size = int(value.get('size',0)); modified = int(value.get('modified',0))
        except (ValueError,TypeError):raise HTTPException(400,'文件大小或修改时间无效')
        fingerprint = str(value.get('fingerprint',''))
        if Path(name).suffix.lower() not in VIDEO_EXTENSIONS or not 0 < size <= 16*1024**4 or not re.fullmatch('[a-f0-9]{64}',fingerprint):
            raise HTTPException(400,'请选择非空的视频文件')
        sid = value.get('source_id')
        with self.lock:
            if sid:
                item = self.get(sid)
                if item['kind']!='browser' or any(item[k]!=v for k,v in [('name',name),('size',size),('modified',modified),('fingerprint',fingerprint)]):
                    raise HTTPException(409,'重新选择的文件与原来源不匹配（名称、大小、时间或首尾内容不同）')
            else:
                sid = secrets.token_hex(24)
                item = {'id':sid,'name':name,'kind':'browser','size':size,'modified':modified,'fingerprint':fingerprint,'created':time.time()}
            connected=bool(self.readers.get(sid) and self.readers[sid].connected)
            item.update(status='ready' if connected else 'waiting_source',error='' if connected else '保留此网页，文件将按需读取')
            self.items[sid] = item
            if sid not in self.readers: self.readers[sid] = BrowserReader(size)
        self.save()
        return self.public(item)

    def connected(self, sid):
        item = self.get(sid)
        return item['kind']=='path' or bool(self.readers.get(sid) and self.readers[sid].connected)

    def address(self, sid):
        item = self.get(sid)
        if item['kind']=='path': return str(local_video(item['path']))
        if not self.connected(sid): raise SourceUnavailable('请保留拖入网页，或重新选择同一文件继续')
        return f'http://127.0.0.1:{self.server.server_port}/media/{sid}'

    async def websocket(self, ws, sid):
        # A capability alone is insufficient: require same-origin and session token.
        origin = ws.headers.get('origin','')
        expected = f"http://{ws.headers.get('host','')}"
        if ws.client.host not in ('127.0.0.1','::1','testclient') or origin != expected or sid not in self.readers:
            await ws.close(code=1008); return
        await ws.accept()
        reader = self.readers[sid]
        try:
            auth = await asyncio.wait_for(ws.receive_json(),5)
            if auth.get('token') != self.a.TOKEN:
                await ws.close(code=1008); return
            with reader.condition:
                if reader.connected:
                    await ws.close(code=1008); return
                reader.ws, reader.loop, reader.connected = ws, asyncio.get_running_loop(), True
                reader.last_error=''
            with self.lock: self.items[sid].update(status='ready',error='')
            self.save()
            await ws.send_json({'ready':True,'chunk':CHUNK})
            # Resume is explicit so a cancelled batch can never restart on attach.
            while True:
                message = await ws.receive()
                if message['type']=='websocket.disconnect': break
                data = message.get('bytes')
                reader.accept(data)
        except (WebSocketDisconnect,asyncio.TimeoutError,ValueError,RuntimeError): pass
        finally:
            reader.close(ws)
            if not reader.connected:
                with self.lock: self.items[sid].update(status='waiting_source',error='网页文件连接已断开，请重新选择文件')
                self.save()
                # Interrupt only owned work reading this source. Its runner persists
                # completed segments and releases the serial media worker.
                for job in list(self.a.JOBS.values()):
                    if job.get('options',{}).get('source_id')==sid and job['status']=='running':
                        proc = self.a.PROCESSES.get(job['id'])
                        if proc: self.pool.submit(self.a.stop_owned_process,proc)
            try: await ws.close()
            except RuntimeError: pass

    def start_picker(self, kind):
        if kind not in ('files','folder'): raise HTTPException(400,'未知选择器类型')
        key = secrets.token_hex(16)
        if os.name == 'nt':
            setup = ("Add-Type -AssemblyName System.Windows.Forms;[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
                     "Add-Type 'using System; using System.Diagnostics; using System.Runtime.InteropServices; public class VDPickerOwner : System.Windows.Forms.IWin32Window { [DllImport(\"user32.dll\")] static extern IntPtr GetForegroundWindow(); [DllImport(\"user32.dll\")] static extern bool IsWindow(IntPtr w); [DllImport(\"user32.dll\")] static extern bool IsWindowVisible(IntPtr w); [DllImport(\"user32.dll\")] static extern uint GetWindowThreadProcessId(IntPtr w,out uint p); [DllImport(\"user32.dll\")] static extern bool SetForegroundWindow(IntPtr w); [DllImport(\"user32.dll\")] static extern bool SetWindowPos(IntPtr w,IntPtr after,int x,int y,int width,int height,uint flags); public delegate bool EnumProc(IntPtr w,IntPtr p); [DllImport(\"user32.dll\")] static extern bool EnumWindows(EnumProc callback,IntPtr p); readonly IntPtr owner=GetForegroundWindow(); public IntPtr Handle {get{return IsWindow(owner)?owner:IntPtr.Zero;}} public static bool RaiseDialog(){bool found=false; EnumWindows((w,v)=>{uint id;GetWindowThreadProcessId(w,out id);if(id==(uint)Process.GetCurrentProcess().Id && IsWindowVisible(w)){SetWindowPos(w,new IntPtr(-1),0,0,0,0,0x43);SetForegroundWindow(w);found=true;}return true;},IntPtr.Zero);return found;} }' -ReferencedAssemblies System.Windows.Forms;"
                     "$o=New-Object VDPickerOwner;$t=New-Object System.Windows.Forms.Timer;$t.Interval=150;$t.add_Tick({if([VDPickerOwner]::RaiseDialog()){$t.Stop()}});$t.Start();")
            if kind=='files':
                command = setup + "$d=New-Object System.Windows.Forms.OpenFileDialog;$d.Multiselect=$true;$d.Title='选择多个视频';$d.Filter='视频|*.mp4;*.mkv;*.mov;*.webm;*.avi;*.flv;*.m4v;*.ts;*.m2ts';if($d.ShowDialog()-eq 'OK'){$d.FileNames|ConvertTo-Json -Compress};$t.Dispose();$d.Dispose()"
            else:
                command = setup + "$d=New-Object System.Windows.Forms.FolderBrowserDialog;$d.Description='选择视频文件夹';if($d.ShowDialog()-eq 'OK'){ConvertTo-Json -InputObject $d.SelectedPath -Compress};$t.Dispose();$d.Dispose()"
            encoded = base64.b64encode(command.encode('utf-16le')).decode()
            args = [str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-STA','-EncodedCommand',encoded]
        else:
            call = 'askopenfilenames' if kind=='files' else 'askdirectory'
            args = [sys.executable,'-c',f'import tkinter as t,json;from tkinter import filedialog;r=t.Tk();r.withdraw();print(json.dumps(filedialog.{call}()));r.destroy()']
        proc = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        record = self.dialogs[key] = {'status':'pending','paths':[],'process':proc}
        def finish():
            try:
                out,diagnostic = proc.communicate(timeout=300)
                if diagnostic:record['diagnostic']=diagnostic.decode('utf-8','replace')[-1600:]
                if record['status']!='pending': return
                if proc.returncode: raise ValueError('文件选择器未完成，请重试或粘贴路径')
                parsed = json.loads(out.decode('utf-8-sig').strip() or '[]')
                record.update(status='done',paths=[parsed] if isinstance(parsed,str) else list(parsed))
            except subprocess.TimeoutExpired:
                proc.kill(); proc.communicate();record.update(status='cancelled',error='选择已超时，请重新打开')
            except Exception as exc: record.update(status='error',error=str(exc))
            finally: record.pop('process',None)
        threading.Thread(target=finish,daemon=True).start()
        return {'id':key,'status':'pending'}


def install(app,a):
    sources = Sources(a)
    @app.post('/api/sources/register')
    def register(req:dict):
        paths = req.get('paths',[])
        if isinstance(paths,str): paths = parse_paths(paths)
        if not isinstance(paths,list) or len(paths)>10000: raise HTTPException(400,'一次最多导入 10000 个路径')
        return {'sources':sources.register(paths)}
    @app.post('/api/sources/discover')
    def folder(req:dict):
        try: paths,skipped = discover(req.get('folder',''),bool(req.get('recursive',True)))
        except ValueError as exc: raise HTTPException(400,str(exc))
        return {'sources':sources.register(paths),'skipped':skipped}
    @app.get('/api/sources')
    def listing():
        return {'sources':[sources.public(i) for i in list(sources.items.values())]}
    @app.post('/api/sources/browser')
    def browser(req:dict): return sources.browser(req)
    @app.websocket('/api/sources/{sid}/read')
    async def read(ws:WebSocket,sid:str): await sources.websocket(ws,sid)
    @app.get('/api/sources/{sid}/metrics')
    def metrics(sid:str):
        sources.get(sid);reader=sources.readers.get(sid)
        return {'chunk_bytes':CHUNK,'peak_response_buffer_bytes':reader.peak_buffer if reader else 0,'bytes_served':reader.bytes_served if reader else 0,'reads':reader.reads if reader else 0}
    @app.post('/api/sources/{sid}/preview')
    def preview(sid:str):
        item = sources.get(sid)
        return {'url':f'/api/sources/{sid}/media' if item['kind']=='path' else '', 'cover':f'/api/sources/{sid}/cover'}
    @app.post('/api/sources/{sid}/validate')
    def validate(sid:str):
        sources.get(sid)
        sources.pool.submit(sources.validate,sid)
        return {'status':'validating'}
    @app.get('/api/sources/{sid}/media')
    def media(sid:str):
        item = sources.get(sid)
        if item['kind']!='path': raise HTTPException(400,'请使用网页文件预览')
        try:path=local_video(item['path'])
        except (ValueError,OSError) as exc:raise HTTPException(422,'文件不可读：'+str(exc))
        return FileResponse(path)
    @app.get('/api/sources/{sid}/cover')
    def cover(sid:str):
        try:source = sources.address(sid)
        except SourceUnavailable:raise HTTPException(409,'网页来源已断开，请重新选择文件')
        except (ValueError,OSError) as exc:raise HTTPException(422,'文件不可读：'+str(exc))
        dest = a.DATA / 'source-covers';dest.mkdir(exist_ok=True)
        path = dest / (sid+'.jpg')
        if not path.exists():
            result = subprocess.run([a.FFMPEG,'-v','error','-nostdin','-y','-i',source,'-frames:v','1','-vf','scale=640:-2',str(path)],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or not path.is_file(): raise HTTPException(422,'此视频无法生成封面，请检查文件')
        return FileResponse(path)
    @app.post('/api/pickers')
    def picker(req:dict): return sources.start_picker(req.get('kind','files'))
    @app.get('/api/pickers/{key}')
    def picker_state(key:str):
        if key not in sources.dialogs: raise HTTPException(404,'选择器已失效')
        return {k:v for k,v in sources.dialogs[key].items() if k!='process'}
    @app.post('/api/pickers/{key}/cancel')
    def cancel(key:str):
        record = sources.dialogs.get(key)
        if not record: raise HTTPException(404,'选择器已失效')
        record['status']='cancelled'
        proc = record.get('process')
        if proc and proc.poll() is None: proc.kill()
        return {'status':'cancelled'}
    return sources

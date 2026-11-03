"""Cancelable OCR RPC with a bounded lifetime and a per-image timeout."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


class OcrSession:
    def __init__(self,a=None,jid=None,timeout=120,worker=None,threads=None):
        self.a=a;self.jid=jid;self.timeout=timeout;self.proc=None;self.count=0
        try:self.threads=max(1,min(4,int(threads if threads is not None else os.environ.get('VIDEO_DESK_OCR_THREADS',2))))
        except (TypeError,ValueError):self.threads=1
        if (os.cpu_count() or 1)<4:self.threads=1
        self.worker=Path(worker) if worker else Path(__file__).with_name('ocr_worker.py')

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def close(self):
        p=self.proc
        if p is None:return
        try:
            if p.poll() is None:
                p.stdin.close()
                try:p.wait(timeout=2)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
        finally:
            p.stdout.close()
            if self.a:
                with self.a.LOCK:
                    if self.a.PROCESSES.get(self.jid) is p:self.a.PROCESSES.pop(self.jid,None)
            self.proc=None

    def start(self):
        from .runtime_support import require
        require('ocr')
        self.close();self.messages=queue.Queue(maxsize=4);messages=self.messages
        env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8',
             'VIDEO_DESK_OCR_THREADS':str(self.threads),
             'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0)
        self.proc=subprocess.Popen([sys.executable,str(self.worker)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            encoding='utf-8',env=env,creationflags=flags,bufsize=1)
        p=self.proc
        if self.a:
            with self.a.LOCK:self.a.PROCESSES[self.jid]=p
        def reader():
            try:
                for line in p.stdout:
                    try:item=json.loads(line)
                    except ValueError:continue
                    if 'result' in item or 'error' in item:messages.put(item)
            finally:messages.put(None)
        threading.Thread(target=reader,daemon=True).start();self.count=0

    def read(self,path):
        # Recycle native allocators every 24 images, including between videos.
        if self.proc is None or self.proc.poll() is not None or self.count>=24:self.start()
        if self.a:self.a.check_cancel(self.jid)
        self.proc.stdin.write(json.dumps({'path':str(path)},ensure_ascii=False)+'\n');self.proc.stdin.flush()
        deadline=time.monotonic()+self.timeout
        try:
            while time.monotonic()<deadline:
                if self.a:self.a.check_cancel(self.jid)
                try:item=self.messages.get(timeout=.15)
                except queue.Empty:continue
                if item is None:
                    code=self.proc.wait(timeout=2)
                    raise ValueError(f'文字识别进程已退出（代码 {code}），已保留原尺寸图片')
                if 'error' in item:raise ValueError(item['error'])
                self.cache_stats=item.get('cache',{})
                self.count+=1;return item['result']
            raise ValueError('单张画面识别超时，已保留原尺寸图片')
        except BaseException:
            self.close();raise

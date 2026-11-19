"""Atomic metadata replacement with bounded retry for Windows sharing locks."""
import json
import errno
import os
from pathlib import Path
import shutil
import threading
import time
import uuid


def relocate_file(source,target):
    """Publish a generated file across drives without exposing a partial copy."""
    source,target=Path(source),Path(target)
    try:
        os.replace(source,target)
        return
    except OSError as exc:
        if exc.errno!=errno.EXDEV and getattr(exc,'winerror',None)!=17:raise
    temp=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        shutil.copy2(source,temp)
        os.replace(temp,target)
        source.unlink()
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path, value, backup=False):
    path=Path(path)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as stream:
            stream.write(json.dumps(value,ensure_ascii=False,indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        if backup and path.is_file():
            try:
                previous=json.loads(path.read_text('utf-8'))
            except (OSError,ValueError):
                previous=None
            if previous is not None:
                atomic_json(path.with_suffix(path.suffix+'.bak'),previous)
        for attempt in range(8):
            try:
                os.replace(temp,path)
                return
            except PermissionError:
                if attempt==7:raise
                time.sleep(min(.01*2**attempt,.2))
    finally:
        try:temp.unlink(missing_ok=True)
        except OSError:pass

def atomic_text(path, text, backup_from=None):
    """Write already-serialized text atomically.

    ``backup_from`` copies the previous file instead of re-serializing it: the
    jobs file grows to hundreds of kilobytes, and a second full write could
    double the stall on a slow disk.
    """
    path=Path(path)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if backup_from and path.is_file():
            try: shutil.copy2(path,backup_from)
            except OSError: pass
        for attempt in range(8):
            try:
                os.replace(temp,path)
                return
            except PermissionError:
                if attempt==7:raise
                time.sleep(min(.01*2**attempt,.2))
    finally:
        try:temp.unlink(missing_ok=True)
        except OSError:pass

_DEFER_LOCK = threading.Lock()
_DEFER_ITEMS = {}
_DEFER_EVENT = threading.Event()
_DEFER_THREAD = None


def _defer_loop():
    while True:
        if not _DEFER_EVENT.wait(1.0): continue
        _DEFER_EVENT.clear()
        time.sleep(.25)
        with _DEFER_LOCK:
            items=list(_DEFER_ITEMS.items()); _DEFER_ITEMS.clear()
        for path,text in items:
            try: atomic_text(path,text)
            except Exception: pass


def defer_text(path, text):
    """Queue a metadata write so the caller never waits on fsync.

    A stalled disk must not freeze a thread that holds a data lock; the newest
    payload for a path wins, so bursts collapse into one write.
    """
    global _DEFER_THREAD
    with _DEFER_LOCK:
        _DEFER_ITEMS[Path(path)]=text
        if _DEFER_THREAD is None or not _DEFER_THREAD.is_alive():
            _DEFER_THREAD=threading.Thread(target=_defer_loop,name='deferred-writer',daemon=True)
            _DEFER_THREAD.start()
    _DEFER_EVENT.set()


def flush_deferred():
    with _DEFER_LOCK:
        items=list(_DEFER_ITEMS.items()); _DEFER_ITEMS.clear()
    for path,text in items:
        try: atomic_text(path,text)
        except Exception: pass

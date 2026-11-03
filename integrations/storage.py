"""Atomic metadata replacement with bounded retry for Windows sharing locks."""
import json
import errno
import os
from pathlib import Path
import shutil
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

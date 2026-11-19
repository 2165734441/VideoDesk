"""Optional, private Python runtime and hash-verified translation models."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
from urllib.request import Request, urlopen
import zipfile
from .storage import atomic_json

ROOT = Path(__file__).resolve().parents[1]
NAMES = {'english-asr': '英文语音识别', 'english-chinese': '英中翻译', 'chinese-voice': '中文配音', 'background': '背景声分离'}
LOCK = threading.Lock()


def component_root(data):
    data = Path(data)
    try:
        value = json.loads((data/'translation-location.json').read_text('utf-8'))['path']
        path = Path(value)
        if path.is_absolute(): return path
    except (OSError, ValueError, KeyError, TypeError): pass
    return data


def catalog(): return json.loads((ROOT/'integrations/translation-catalog.json').read_text('utf-8'))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(4*1024*1024): h.update(block)
    return h.hexdigest()


def verify(data, key):
    dest = component_root(data)/'translation-models'/key
    try:
        record = json.loads((dest/'verified.json').read_text('utf-8'))
        for name, meta in record['files'].items():
            path = dest/name
            if not path.resolve().is_relative_to(dest.resolve()) or path.is_symlink() or not path.is_file() or path.stat().st_size != meta['bytes'] or digest(path) != meta['sha256']:
                raise ValueError('模型文件已变化，请重新安装：'+NAMES[key])
        if not record['files']: raise ValueError('模型校验记录为空')
    except (OSError, KeyError, TypeError, json.JSONDecodeError): raise ValueError('模型校验失败，请重新安装：'+NAMES[key])


def runtime(data, gpu=False):
    return component_root(data)/'translation-runtime'/('gpu' if gpu else 'cpu')/'python.exe'


def state(data):
    data = component_root(data)
    required = {'english-asr':'model.bin', 'english-chinese':'ct2/model.bin', 'chinese-voice':'model.onnx', 'background':'955717e8.safetensors'}
    return {'path': str(data.resolve()), 'runtime': {kind: (runtime(data, kind == 'gpu').parent/'ready.json').is_file() for kind in ('cpu', 'gpu')},
            'models': [{'id': key, 'name': name, 'ready': (data/'translation-models'/key/'verified.json').is_file() and
                        (data/'translation-models'/key/required[key]).is_file()} for key, name in NAMES.items()]}


def fetch(data, entry, report, check):
    folder = component_root(data)/'translation-downloads'; folder.mkdir(parents=True, exist_ok=True)
    target = folder/entry['sha256']
    if target.is_file() and digest(target) == entry['sha256']: return target
    part = target.with_suffix('.partial')
    try:
        check()
        with urlopen(Request(entry['url'], headers={'User-Agent': 'VideoDesk'}), timeout=60) as response, part.open('wb') as out:
            total = 0; last = 0
            while block := response.read(1024*1024):
                check(); out.write(block); total += len(block)
                if total-last >= 16*1024*1024 or total >= entry['bytes']:
                    report(message=f'下载翻译组件：{total//1048576} / {entry["bytes"]//1048576} MB'); last = total
        if digest(part) != entry['sha256']: raise ValueError('组件 SHA256 校验失败，请重试')
        part.replace(target)
        return target
    finally: part.unlink(missing_ok=True)


def process(args, check, report, env=None):
    # Log is private; provider credentials never enter this command line.
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(list(map(str, args)), stdout=log, stderr=log, env=env,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while proc.poll() is None:
                check()
                try: proc.wait(timeout=.5)
                except subprocess.TimeoutExpired: pass
            if proc.returncode:
                log.seek(0); tail = log.read()[-1500:].decode('utf-8', 'replace')
                raise ValueError('翻译组件安装失败：'+tail)
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()


def install_runtime(data, gpu, report, check):
    entries = catalog(); exe = runtime(data, gpu); folder = exe.parent
    if (folder/'ready.json').is_file(): return
    folder.mkdir(parents=True, exist_ok=True)
    required = (20 if gpu else 5)*1024**3
    if shutil.disk_usage(folder).free < required:
        raise ValueError(f'安装前至少需要 {required//1024**3} GB 可用空间，请在翻译页面更换 AI 组件目录后重试')
    archive = fetch(data, entries['python'], report, check)
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if not (folder/item.filename).resolve().is_relative_to(folder.resolve()): raise ValueError('组件路径无效')
        package.extractall(folder)
    (folder/'python311._pth').write_text('python311.zip\n.\nLib/site-packages\nimport site\n', encoding='utf-8')
    pip = fetch(data, entries['pip'], report, check)
    site = folder/'Lib/site-packages'; site.mkdir(parents=True, exist_ok=True)
    report(message='安装隔离的 AI 运行环境，首次安装可能需要较长时间')
    env = dict(os.environ); env.pop('PYTHONPATH', None); env.pop('PYTHONHOME', None)
    base = [exe, pip, 'install', '--disable-pip-version-check', '--no-warn-script-location', '--no-compile', '--upgrade', '--target', site]
    process([*base, '--no-deps', 'torch==2.7.1', '--index-url', 'https://download.pytorch.org/whl/'+('cu128' if gpu else 'cpu')], check, report, env)
    process([*base, '--no-deps', '-r', ROOT/'integrations/translation-requirements-lock.txt'], check, report, env)
    process([exe, '-c', 'import ctranslate2, transformers, sentencepiece, sherpa_onnx, demucs; import torch; print(torch.__version__)'], check, report, env)
    atomic_json(folder/'ready.json', {'python': '3.11.9', 'gpu': gpu, 'requirements_sha256': digest(ROOT/'integrations/translation-requirements-lock.txt')})


def install_model(data, key, report, check):
    entries = catalog(); data = component_root(data)
    if key not in NAMES: raise ValueError('未知翻译模型')
    dest = data/'translation-models'/key
    if (dest/'verified.json').is_file():
        try: verify(data, key); return
        except ValueError: (dest/'verified.json').unlink()
    dest.mkdir(parents=True, exist_ok=True)
    if key == 'chinese-voice':
        archive = fetch(data, entries[key], report, check)
        with tarfile.open(archive, 'r:bz2') as package:
            for member in package.getmembers():
                check()
                parts = Path(member.name).parts[1:]
                if not parts or not member.isfile() or '..' in parts: continue
                target = dest.joinpath(*parts)
                if not target.resolve().is_relative_to(dest.resolve()): raise ValueError('模型路径无效')
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile(member) as src, target.open('wb') as out: shutil.copyfileobj(src, out)
    else:
        for entry in entries[key]['files']:
            source = fetch(data, entry, report, check)
            shutil.copyfile(source, dest/entry['name'])
    if key == 'english-chinese':
        report(message='转换本地翻译模型为 INT8，之后无需重复转换')
        exe = runtime(data)
        if not exe.is_file(): exe = runtime(data, True)
        process([exe, ROOT/'integrations/translation_worker.py', '--convert', dest], check, report)
    atomic_json(dest/'verified.json', {'files': {p.relative_to(dest).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
                for p in dest.rglob('*') if p.is_file() and p.name != 'verified.json'}})


def install(data, gpu, report, check):
    with LOCK:
        install_runtime(data, gpu, report, check)
        for key in NAMES:
            check(); report(message='安装'+NAMES[key]); install_model(data, key, report, check)
    return '本地翻译、中文配音和音频分离组件已安装'


def export_models(data, target, check):
    entries = catalog()
    needed = [e for key in NAMES for e in (entries[key]['files'] if 'files' in entries[key] else [entries[key]])]
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as package:
        for entry in needed:
            check(); source = component_root(data)/'translation-downloads'/entry['sha256']
            if not source.is_file() or digest(source) != entry['sha256']: raise ValueError('请先安装全部模型再导出离线模型包')
            package.write(source, entry['sha256'])


def import_models(data, source, report, check):
    entries = catalog()
    needed = {e['sha256']: e for key in NAMES for e in (entries[key]['files'] if 'files' in entries[key] else [entries[key]])}
    cache = component_root(data)/'translation-downloads'; cache.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as package:
        names = package.namelist()
        if len(names) != len(set(names)) or not set(needed).issubset(names): raise ValueError('离线包缺少所需的官方模型文件')
        for sha, entry in needed.items():
            check(); info = package.getinfo(sha)
            if info.file_size != entry['bytes']: raise ValueError('离线模型大小不符')
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as out:
                temporary = Path(out.name)
                with package.open(sha) as src: shutil.copyfileobj(src, out)
            try:
                if digest(temporary) != sha: raise ValueError('离线模型 SHA256 校验失败')
                temporary.replace(cache/sha)
            finally: temporary.unlink(missing_ok=True)
    for key in NAMES: install_model(data, key, report, check)
    return '离线模型已校验并安装'

"""Pinned optional models, verified online/offline installation and CPU OCR."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import tarfile
import tempfile
from urllib.request import Request, urlopen
import zipfile
from . import runtime_support

ROOT = Path(__file__).resolve().parents[1]


def catalog():
    path=ROOT/'integrations/model-catalog.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while part:=f.read(1024*1024): h.update(part)
    return h.hexdigest()


def state(data):
    result=[]
    for key,entry in catalog().items():
        dest=Path(data)/'models'/key
        result.append({**entry,'id':key,'ready':(dest/'verified.json').is_file() and (dest/entry['model']).is_file()})
    # Inspect package metadata only. Importing RapidOCR here previously loaded
    # native inference DLLs in the server just to render the settings page.
    spec=importlib.util.find_spec('rapidocr')
    models=Path(spec.origin).parent/'models' if spec and spec.origin else None
    ocr=bool(models and list(models.glob('*.onnx')) and runtime_support.available('ocr'))
    return {'models':result,'ocr':ocr,
            'asr_runtime':runtime_support.available('asr') and importlib.util.find_spec('sherpa_onnx') is not None}


def install(data,key,report,check,offline=None):
    entry=catalog().get(key)
    if not entry: raise ValueError('未知模型')
    root=Path(data)/'models';root.mkdir(exist_ok=True)
    archive=Path(offline) if offline else root/(key+'.download')
    if offline and (not archive.is_absolute() or not archive.is_file()): raise ValueError('请选择存在的离线模型压缩包')
    if not offline:
        check()
        with urlopen(Request(entry['url'],headers={'User-Agent':'VideoDesk'}),timeout=60) as response, archive.open('wb') as dest:
            length=int(response.headers.get('Content-Length',entry['bytes']));total=0;last=0
            while chunk:=response.read(1024*1024):
                check();dest.write(chunk);total+=len(chunk)
                percent=int(total/max(length,1)*85)
                if percent!=last: report(message=f'下载本地语音模型：{total//1048576} / {length//1048576} MB',progress=min(85,percent));last=percent
    check()
    if digest(archive)!=entry['sha256']: raise ValueError('模型校验失败，请重新下载官方模型包')
    report(message='校验完成，正在安装本地模型',progress=90)
    dest=root/key
    with tempfile.TemporaryDirectory(dir=root,prefix='.install-') as temporary:
        stage=Path(temporary)
        with tarfile.open(archive,'r:bz2') as tar:
            # Copy regular model, tokens, license and small official speech
            # fixtures only. Never honor archive links or member paths.
            for member in tar.getmembers():
                check()
                name=Path(member.name).name
                if member.isfile() and (name in (entry['model'],entry['tokens'],'LICENSE','README.md') or name.endswith('.wav')):
                    source=tar.extractfile(member)
                    with (stage/name).open('wb') as out: shutil.copyfileobj(source,out)
        if not all((stage/name).is_file() for name in (entry['model'],entry['tokens'])): raise ValueError('模型包缺少所需文件')
        dest.mkdir(exist_ok=True)
        for path in stage.iterdir(): os.replace(path,dest/path.name)
        (dest/'verified.json').write_text(json.dumps({'archive_sha256':entry['sha256'],'files':{p.name:digest(p) for p in dest.iterdir() if p.is_file() and p.name!='verified.json'}}),encoding='utf-8')
    # Retain the verified publisher archive for a genuinely offline export.
    publisher=root/(key+'.offline.tar.bz2')
    if not offline:os.replace(archive,publisher)
    elif archive.resolve()!=publisher.resolve():
        # Keep the verified publisher archive for later exports without network.
        with tempfile.NamedTemporaryFile(dir=root,prefix='.model-copy-',delete=False) as file:temporary=Path(file.name)
        try:shutil.copy2(archive,temporary);os.replace(temporary,publisher)
        finally:temporary.unlink(missing_ok=True)
    return '本地语音模型已安装，可离线使用'


def offline_package(data,key,destination,report=lambda **v:None,check=lambda:None):
    entry=catalog().get(key)
    root=Path(data)/'models';root.mkdir(exist_ok=True)
    source=root/(key+'.offline.tar.bz2')
    # Export exactly the publisher archive; it can be verified by another PC.
    if not entry: raise ValueError('未知模型')
    if not source.exists() or digest(source)!=entry['sha256']:
        with tempfile.NamedTemporaryFile(dir=root,prefix='.model-download-',delete=False) as file:temporary=Path(file.name)
        try:
            with urlopen(Request(entry['url'],headers={'User-Agent':'VideoDesk'}),timeout=60) as response, temporary.open('wb') as out:
                total=0
                while chunk:=response.read(1024*1024):
                    check();out.write(chunk);total+=len(chunk)
                    report(message=f'准备离线模型包：{total//1048576} MB',progress=min(90,int(total/entry['bytes']*90)))
            check()
            if digest(temporary)!=entry['sha256']:raise ValueError('离线模型包校验失败')
            os.replace(temporary,source)
        finally:temporary.unlink(missing_ok=True)
    check()
    if digest(source)!=entry['sha256']: raise ValueError('离线模型包校验失败')
    shutil.copy2(source,destination)


def ocr_image(path):
    from .ocr_session import OcrSession
    with OcrSession() as session:result=session.read(path)
    return [{'text':text,'confidence':score} for text,score in zip(result['text'],result['confidence'])]

"""One capability per disposable process: native crashes cannot kill the desk.

No video, account, model or user output is opened by these probes.
"""
import importlib
import json
import os
from pathlib import Path
import sys


def probe(name):
    print('正在检查：'+name,flush=True)
    if name=='base':
        for module in ('fastapi','uvicorn','PIL.Image','imageio_ffmpeg'):
            importlib.import_module(module)
        print(sys.version.split()[0],flush=True);print(sys.executable,flush=True)
    elif name=='media':
        import imageio_ffmpeg
        import subprocess
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        ffprobe=Path(__file__).resolve().parents[1]/'runtime-tools/ffprobe.exe'
        for tool in (ffmpeg,str(ffprobe)):
            subprocess.run([tool,'-version'],check=True,stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE,timeout=15,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    elif name=='download':
        importlib.import_module('yt_dlp')
        importlib.import_module('yt_dlp_ejs')
    elif name=='browser':
        from playwright.sync_api import sync_playwright
        root=Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH',''))
        executable=next(root.glob('chromium-*/chrome-win*/chrome.exe'),None)
        if executable is None:
            configured=os.environ.get('VIDEO_DESK_BROWSER_EXE','')
            if configured and Path(configured).is_file():executable=Path(configured)
        if executable is None:raise FileNotFoundError('未找到随包提供的浏览器，请检测并修复')
        with sync_playwright() as api:
            browser=api.chromium.launch(executable_path=str(executable),headless=True,args=['--disable-gpu'])
            browser.close()
    elif name=='dedup':
        from PIL import Image
        import imagehash
        imagehash.phash(Image.new('RGB',(32,32)))
    elif name=='ocr':
        import cv2
        import onnxruntime
        from rapidocr import RapidOCR
        assert RapidOCR is not None
        print('ONNX Runtime '+onnxruntime.__version__,flush=True)
    elif name=='asr':
        import sherpa_onnx
        assert sherpa_onnx.OfflineRecognizer is not None
    else:
        raise ValueError('Unknown probe: '+name)
    print(json.dumps({'component':name,'ready':True}),flush=True)


if __name__=='__main__':
    probe(sys.argv[1])

"""Fetch verified media tools and pin the optional ASR model catalog."""
from pathlib import Path
import hashlib
import json
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.local/build-exe/cache'
TOOLS = ROOT / 'runtime-tools'


def read(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'VideoDesk-build'}), timeout=120) as r:
        return r.read()


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    TOOLS.mkdir(exist_ok=True)
    base = 'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip'
    manifest = TOOLS / 'media-tools.json'
    if not manifest.exists() or not (TOOLS / 'ffprobe.exe').is_file():
        digest = '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
        archive = CACHE / ('ffmpeg-' + digest[:12] + '.zip')
        if not archive.exists():
            print('Downloading verified FFmpeg tools', flush=True)
            with urllib.request.urlopen('https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip', timeout=120) as response, archive.open('wb') as dest:
                shutil.copyfileobj(response, dest)
        if archive.stat().st_size != 114768076:
            with urllib.request.urlopen('https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip', timeout=120) as response, archive.open('wb') as dest:
                shutil.copyfileobj(response, dest)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Media tools checksum mismatch')
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                leaf = Path(name).name
                if leaf in ('ffprobe.exe', 'LICENSE', 'README.txt'):
                    (TOOLS / leaf).write_bytes(z.read(name))
        manifest.write_text(json.dumps({'url': base, 'archive_sha256': digest,
            'ffprobe_sha256': hashlib.sha256((TOOLS/'ffprobe.exe').read_bytes()).hexdigest()}, indent=2))
    model_url = 'https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/asr-models'
    release = json.loads(read(model_url))
    wanted = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2'
    asset = next(a for a in release['assets'] if a['name'] == wanted)
    # GitHub supplies an asset digest; fail closed if it is unavailable.
    digest = asset.get('digest', '').removeprefix('sha256:')
    if len(digest) != 64:
        raise RuntimeError('ASR publisher did not provide a SHA256 digest')
    catalog = {'sensevoice': {'name': 'SenseVoice · 中英日韩粤', 'url': asset['browser_download_url'],
        'bytes': asset['size'], 'sha256': digest, 'model': 'model.int8.onnx', 'tokens': 'tokens.txt',
        'languages': ['auto', 'zh', 'en', 'ja', 'ko', 'yue'],
        'license_url': 'https://github.com/FunAudioLLM/SenseVoice/blob/main/LICENSE'}}
    (ROOT/'integrations/model-catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Media tools and pinned model catalog ready', flush=True)


if __name__ == '__main__':
    main()

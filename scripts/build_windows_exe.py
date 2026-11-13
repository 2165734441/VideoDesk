"""Build a self-contained Windows x64 EXE; recipients need no developer tools.

Run with the project's build-time Python: prepare, bundle, or all.
Only explicit app files, tracked upstream source, and public runtime packages
enter the payload. User data, profiles and the developer environment are excluded.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
BUILD = ROOT / '.local' / 'build-exe'
CACHE = BUILD / 'cache'
STAGE = BUILD / 'payload'
VERSION = '2.5.1'
PYTHON_VERSION = '3.13.17'
PYTHON_HASH = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'
NODE_VERSION = '22.23.4'
NODE_HASH = 'cbc74b5e0982c9dbb41525fd8fb5dcd32fbee237863f7829072f4639a1ce5f20'
UPSTREAM_COMMIT = '8f583999006d5b692272c608440e193ca282e73b'
APP_FILES = [
    'tests/test_bug_report25.py','tests/EnvironmentHarness.cs','scripts/verify_bugfix25.py',
    'LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','docs/BUILD.md','docs/USAGE-HISTORY.md',
    'integrations/performance.py','integrations/media_probe.py','integrations/workflow_settings.py','integrations/recycle.py',
    'static/workflows25.js','static/workflows25.css','tests/test_workflows25.py','WORKFLOWS25-TEST-REPORT.md',
    'scripts/verify_workflows25.py','scripts/verify_sources25.py','scripts/verify_v25.py','scripts/verify_formats25.py','scripts/verify_resume25.py','scripts/verify_cleanup25.py','scripts/verify_crossdrive25.py',
    'integrations/runtime_support.py','integrations/runtime_probe.py','static/runtime.js',
    'integrations/material_packages.py','static/materials.js','static/materials.css','static/icon.ico','static/icon.png','scripts/create_icon.py','tests/test_material_packages.py','scripts/verify_materials.py','MATERIALS-TEST-REPORT.md',
    "integrations/ocr_cache.py","static/episodes.js","tests/test_ocr_cache.py","scripts/verify_episodes.py","PERFORMANCE-EPISODES-TEST-REPORT.md",
    'integrations/ocr_worker.py','integrations/ocr_session.py','integrations/job_recovery.py','tests/test_stability.py','scripts/verify_stability_ui.py',
    'STABILITY-TEST-REPORT.md',
    'integrations/ai_package.py','integrations/material_history.py','static/enhancements.js','static/enhancements.css',
    'tests/test_ai_package.py','tests/test_sheet_limit.mjs','scripts/verify_ai22.py','scripts/verify_ai22_ui.py','AI22-TEST-REPORT.md',
    'integrations/login_browser.py','tests/test_login_browser.py','tests/test_progress_ui.mjs',
    'integrations/storage.py','integrations/local_sources.py','integrations/batch_pipeline.py','static/local_sources.js','static/local_sources.css',
    'tests/test_batch_pipeline.py','scripts/verify_batch.py','BATCH-TEST-REPORT.md',
    'app.py', 'README.md', 'EXE-README.txt', 'requirements.txt', 'requirements-lock.txt',
    'static/index.html', 'static/app.js', 'static/dedup.js', 'static/style.css', 'static/icon.svg',
    'integrations/__init__.py', 'integrations/mediacrawler_bridge.py',
    'integrations/manual_gate.py', 'integrations/mediacrawler-requirements-lock.txt',
    'third_party/README.md', 'third_party/ImageHash-LICENSE.txt', 'tests/test_pipeline.py', 'tests/test_manual_gate.py',
    'tests/test_image_dedup.py', 'integrations/image_dedup.py',
    'tests/test_video_sources.py', 'integrations/video_sources.py', 'third_party/yt-dlp-ejs-LICENSE.txt',
    'static/workflow.js','static/workflow.css','integrations/storyboard.py',
    'integrations/storyboard_bridge.mjs','integrations/media_relay.py','integrations/ai_models.py',
    'integrations/asr_worker.py','integrations/model-catalog.json','integrations/desk_client.mjs',
    'bin/video-desk.mjs','MERGE-PLAN.md','MERGE-TEST-REPORT.md',
    'third_party/STORYBOARD-PATCHES.md','third_party/SenseVoice-LICENSE.txt',
    'runtime-tools/ffprobe.exe','runtime-tools/LICENSE','runtime-tools/README.txt','runtime-tools/media-tools.json',
]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def run(args, **kwargs):
    print('RUN:', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def fetch(url, name, expected):
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / name
    if target.is_file() and sha256(target) == expected:
        return target
    partial = target.with_suffix(target.suffix + '.partial')
    with urlopen(url, timeout=180) as response, partial.open('wb') as dest:
        shutil.copyfileobj(response, dest, 1024 * 1024)
    if sha256(partial) != expected:
        raise RuntimeError('Official asset checksum mismatch: ' + name)
    partial.replace(target)
    return target


def copy_app():
    app_root = STAGE / 'app'
    for relative in APP_FILES:
        target = app_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    for folder in ('dsh-plugin','third_party/dsh-video-storyboard'):
        source_root=ROOT/folder
        for source in source_root.rglob('*'):
            relative=source.relative_to(source_root)
            if not source.is_file() or any(p.startswith('.') or p in ('node_modules','test','tests','screenshots') for p in relative.parts):continue
            target=app_root/folder/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    upstream = ROOT / 'third_party' / 'MediaCrawler'
    commit = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != UPSTREAM_COMMIT:
        raise RuntimeError('Upstream commit differs from the reviewed version')
    files = subprocess.check_output(['git', '-C', str(upstream), 'ls-files', '-z']).decode('utf-8').split('\0')
    for relative in filter(None, files):
        source = upstream / relative
        if source.is_file():
            target = app_root / 'third_party' / 'MediaCrawler' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def prepare_python(python_zip, name):
    target = STAGE / 'runtime' / name
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(python_zip) as archive:
        archive.extractall(target)
    # _pth isolates this interpreter from installed Python and registry settings.
    (target / 'python313._pth').write_text(
        'python313.zip\n.\nLib/site-packages\n../../app\n../../app/integrations\nimport site\n', encoding='utf-8')
    return target


def install_packages(name, lock):
    destination = STAGE / 'runtime' / name / 'Lib' / 'site-packages'
    marker = BUILD / (name + '-packages.sha256')
    lock_hash = sha256(lock)
    if marker.is_file() and marker.read_text().strip() == lock_hash and destination.is_dir():
        print('Reuse verified package stage:', name, flush=True)
        return
    run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-compile', '--upgrade',
         '--target', destination, '--python-version', '3.13', '--platform', 'win_amd64',
         '--implementation', 'cp', '--abi', 'cp313', '--only-binary=:all:',
         '--find-links', CACHE / 'wheels', '-r', lock])
    marker.write_text(lock_hash, encoding='ascii')


def install_browser():
    # Use the same official URLs as the installed Playwright package. Downloading
    # through urllib gives build-time socket timeouts and persistent cached ZIPs.
    from playwright._impl._driver import compute_driver_executable
    registry = json.loads((Path(compute_driver_executable()[0]).parent / 'package' / 'browsers.json').read_text())
    browser = next(b for b in registry['browsers'] if b['name'] == 'chromium')
    assets = [('chromium-' + browser['revision'],
               'https://cdn.playwright.dev/builds/cft/' + browser['browserVersion'] + '/win64/chrome-win64.zip')]
    for name in ('ffmpeg', 'winldd'):
        item = next(b for b in registry['browsers'] if b['name'] == name)
        filename = 'ffmpeg-win64.zip' if name == 'ffmpeg' else 'winldd-win64.zip'
        assets.append((name + '-' + item['revision'], 'https://cdn.playwright.dev/dbazure/download/playwright/builds/' + name + '/' + item['revision'] + '/' + filename))
    records = []
    for folder_name, url in assets:
        target = STAGE / 'runtime' / 'browsers' / folder_name
        # A successful Playwright installation is also a valid prepared input.
        if (target / 'INSTALLATION_COMPLETE').is_file() and any(target.rglob('*.exe')):
            records.append({'url':url,'prepared_by':'Playwright official installer'})
            continue
        package = CACHE / (folder_name + '.zip')
        if not package.is_file():
            print('Download official browser component:', url, flush=True)
            partial = package.with_suffix('.partial')
            with urlopen(url, timeout=60) as response, partial.open('wb') as target:
                downloaded = 0
                while block := response.read(1024 * 1024):
                    target.write(block); downloaded += len(block)
                    if downloaded % (20 * 1024 * 1024) == 0:
                        print(folder_name, downloaded // (1024 * 1024), 'MiB', flush=True)
            partial.replace(package)
        with zipfile.ZipFile(package) as archive:
            if archive.testzip() is not None:
                raise RuntimeError('Browser component ZIP is corrupt: ' + folder_name)
            for info in archive.infolist():
                if not (target / info.filename).resolve().is_relative_to(target.resolve()):
                    raise RuntimeError('Unsafe browser archive path')
            archive.extractall(target)
        (target / 'INSTALLATION_COMPLETE').touch()
        records.append({'url':url,'sha256':sha256(package),'bytes':package.stat().st_size})
    (BUILD / 'browser-assets.json').write_text(json.dumps(records,indent=2),encoding='utf-8')


def prepare():
    BUILD.mkdir(parents=True, exist_ok=True)
    python_url = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'
    python_zip = fetch(python_url, f'python-{PYTHON_VERSION}-embed-amd64.zip', PYTHON_HASH)
    # Compare the pinned hash with the publisher's digest as well as the bytes.
    with urlopen(python_url + '.sigstore', timeout=30) as response:
        metadata = json.load(response)
    assert base64.b64decode(metadata['messageSignature']['messageDigest']['digest']).hex() == PYTHON_HASH
    node_name = f'node-v{NODE_VERSION}-win-x64.zip'
    node_zip = fetch(f'https://nodejs.org/dist/v{NODE_VERSION}/{node_name}', node_name, NODE_HASH)
    with urlopen(f'https://nodejs.org/dist/v{NODE_VERSION}/SHASUMS256.txt', timeout=30) as response:
        assert NODE_HASH + '  ' + node_name in response.read().decode('utf-8')
    node_dir = STAGE / 'runtime' / 'node'
    node_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(node_zip) as archive:
        for basename in ('node.exe', 'LICENSE', 'README.md'):
            (node_dir / basename).write_bytes(archive.read(f'node-v{NODE_VERSION}-win-x64/' + basename))
    for name in ('main', 'crawler'):
        prepare_python(python_zip, name)
    (CACHE / 'wheels').mkdir(exist_ok=True)
    run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', CACHE / 'wheels',
         'jieba==0.42.1', 'PyExecJS==1.5.1'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(install_packages, 'main', ROOT / 'requirements-lock.txt'),
                   pool.submit(install_packages, 'crawler', ROOT / 'integrations' / 'mediacrawler-requirements-lock.txt'),
                   pool.submit(install_browser)]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    copy_app()
    smoke_runtimes()


def isolated_environment():
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        env.pop(key, None)
    system = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    env.update(PATH=str(STAGE / 'runtime' / 'node') + os.pathsep + str(system / 'System32'),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               VIDEO_DESK_NODE_EXE=str(STAGE / 'runtime' / 'node' / 'node.exe'), VIDEO_DESK_BUNDLED='1', VIDEO_DESK_VERSION=VERSION,
               VIDEO_DESK_DATA=str(BUILD / 'smoke-data'),
               VIDEO_DESK_CRAWLER_PYTHON=str(STAGE / 'runtime' / 'crawler' / 'python.exe'))
    return env


def smoke_runtimes():
    env = isolated_environment()
    run([STAGE / 'runtime' / 'main' / 'python.exe', '-c',
         'import fastapi,uvicorn,yt_dlp,yt_dlp_ejs,playwright,PIL,imageio_ffmpeg,imagehash,sherpa_onnx,rapidocr,onnxruntime; from integrations.video_sources import youtube_support; assert youtube_support()["ready"]; imagehash.phash(PIL.Image.new("RGB",(32,32))); print("Main embedded runtime OK")'], env=env, cwd=STAGE / 'app')
    run([STAGE / 'runtime' / 'crawler' / 'python.exe', STAGE / 'app' / 'integrations' / 'mediacrawler_bridge.py', '--check'],
        env=env, cwd=STAGE / 'app')
    run([STAGE / 'runtime' / 'node' / 'node.exe', '--version'], env=env)


def bundle():
    from prepare_vc_runtime import prepare as prepare_vc_runtime
    prepare_vc_runtime(STAGE)
    copy_app()
    smoke_runtimes()
    browsers = list((STAGE / 'runtime' / 'browsers').glob('chromium-*/chrome-win*/chrome.exe'))
    if not browsers:
        browsers = list((STAGE / 'runtime' / 'browsers').glob('chromium-*/chrome-win64/chrome.exe'))
    if not browsers:
        raise RuntimeError('Bundled browser not found')
    notices = (
        '映格 Video Desk ' + VERSION + ' / Windows x64\n\n'
        'MediaCrawler Copyright (c) 2024 relakkes@gmail.com.\n'
        'NON-COMMERCIAL LEARNING LICENSE 1.1. 仅限非商业学习研究。\n'
        '完整许可证：app/third_party/MediaCrawler/LICENSE。\n'
        'ImageHash © Johannes Buchner / BSD-2-Clause：app/third_party/ImageHash-LICENSE.txt。\n'
        'dsh-video-storyboard © SAOS-3624 / Apache-2.0：app/third_party/dsh-video-storyboard/LICENSE、NOTICE。\n'
        '固定提交 e124ae6fcfb31f3572ea24aad093a86897b556a3；本地适配说明 app/third_party/STORYBOARD-PATCHES.md。\n'
        'SenseVoice 上游 LICENSE / MIT：app/third_party/SenseVoice-LICENSE.txt；模型另行下载，遵守包内 LICENSE 及引用条款。\n'
        'sherpa-onnx、RapidOCR、ONNX Runtime 与内置 OCR 模型许可证随 Python 包保留。\n'
        'ffprobe / FFmpeg GPL：app/runtime-tools/LICENSE；对应源码 https://github.com/FFmpeg/FFmpeg/tree/n9.0.2。\n'
        'Python 的许可证在 runtime/main/LICENSE.txt 与 runtime/crawler/LICENSE.txt。\n'
        'Node.js 的许可证在 runtime/node/LICENSE。\n'
        '浏览器与 Python 第三方依赖的许可证随其目录和 dist-info 保留。\n'
        'Microsoft Visual C++ x64 CRT 原始签名可再分发文件：runtime/vc/README.txt 与 provenance.json。\n'
        'FFmpeg 随 imageio-ffmpeg 提供，其构建信息可用 ffmpeg -version 查看。\n'
    )
    (STAGE / 'THIRD-PARTY-NOTICES.txt').write_text(notices, encoding='utf-8-sig')
    shutil.copy2(ROOT / 'EXE-README.txt', STAGE / 'EXE-README.txt')
    # Preserve the FFmpeg publisher/build identification in addition to package licenses.
    ffmpeg = next((STAGE / 'runtime/main/Lib/site-packages/imageio_ffmpeg/binaries').glob('ffmpeg*.exe'))
    version_info = subprocess.check_output([str(ffmpeg), '-version'], text=True, encoding='utf-8', errors='replace')
    license_info = subprocess.check_output([str(ffmpeg), '-L'], text=True, encoding='utf-8', errors='replace')
    (STAGE / 'FFmpeg-LICENSE-AND-BUILD.txt').write_text(license_info + '\n' + version_info +
        '\nOriginal publisher: https://www.gyan.dev/ffmpeg/builds/\n'
        'FFmpeg source: https://github.com/FFmpeg/FFmpeg/tree/n7.1\n'
        'FFmpeg licensing: https://ffmpeg.org/legal.html\n', encoding='utf-8')
    files = sorted(p for p in STAGE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
                   and p.suffix != '.pyc' and '.links' not in p.parts and '__dirlock' not in p.parts and p.name != 'runtime-manifest.json')
    for path in files:
        relative = path.relative_to(STAGE)
        assert not set(relative.parts).intersection({'.local', '.git', 'browser_data', 'output', '.venv'})
    manifest = {'version':VERSION, 'python':PYTHON_VERSION, 'node':NODE_VERSION,
                'upstream_commit':UPSTREAM_COMMIT,'storyboard_commit':'e124ae6fcfb31f3572ea24aad093a86897b556a3', 'browser':browsers[0].relative_to(STAGE).as_posix(),
                'files':{p.relative_to(STAGE).as_posix():sha256(p) for p in files}}
    (STAGE / 'runtime-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    archive = BUILD / 'payload.zip'
    print('Compressing',len(files),'files, bytes:',sum(p.stat().st_size for p in files),flush=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as package:
        for path in files + [STAGE / 'runtime-manifest.json']:
            package.write(path, path.relative_to(STAGE).as_posix())
    compile_launcher(archive)


def compile_launcher(archive=BUILD/'payload.zip'):
    digest = sha256(archive)
    info = BUILD / 'BuildInfo.cs'
    info.write_text('internal static class BuildInfo { public const string Version="' + VERSION +
                    '"; public const string PayloadHash="' + digest + '"; }', encoding='utf-8-sig')
    output = ROOT / 'release' / ('VideoDesk-' + VERSION + '-Windows-x64.exe')
    output.parent.mkdir(exist_ok=True)
    compiler = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    run([compiler, '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001',
         '/win32icon:' + str(ROOT / 'static' / 'icon.ico'), '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
         '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll',
         '/reference:System.Web.Extensions.dll', '/win32manifest:' + str(ROOT / 'launcher' / 'app.manifest'),
         '/resource:' + str(archive) + ',VideoDesk.Payload', '/out:' + str(output),
         ROOT / 'launcher' / 'Program.cs', ROOT / 'launcher' / 'Compatibility.cs', info])
    checksum = sha256(output)
    output.with_suffix('.exe.sha256').write_text(checksum + '  ' + output.name + '\n', encoding='ascii')
    report = {'file':str(output), 'bytes':output.stat().st_size, 'sha256':checksum,
              'payload_sha256':digest,'files':len(json.loads((STAGE/'runtime-manifest.json').read_text(encoding='utf-8'))['files']),'version':VERSION}
    (BUILD / 'build-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','bundle','all','launcher'])
    args = parser.parse_args()
    if args.action in ('prepare','all'):prepare()
    if args.action in ('bundle','all'):bundle()
    if args.action=='launcher':compile_launcher()

"""Build a source installer ZIP using an explicit allowlist, never runtime data."""
from pathlib import Path
import hashlib
import json
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
DESTINATION = ROOT / 'release' / 'VideoDesk-2.5.0-source.zip'
PREFIX = 'VideoDesk-2.5.0'

FILES = [
    '.gitignore', '.gitattributes', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md',
    'docs/BUILD.md', 'docs/USAGE-HISTORY.md',
    'app.py', 'README.md', 'SHARE-README.txt',
    'requirements.txt', 'requirements-lock.txt',
    'setup.ps1', 'install.cmd', 'start.ps1', 'start.cmd', 'stop.ps1', 'stop.cmd',
    'static/index.html', 'static/style.css', 'static/app.js', 'static/dedup.js', 'static/icon.svg',
    'integrations/__init__.py', 'integrations/mediacrawler_bridge.py',
    'integrations/manual_gate.py', 'integrations/mediacrawler-requirements-lock.txt',
    'third_party/README.md', 'third_party/ImageHash-LICENSE.txt', 'integrations/image_dedup.py', 'scripts/package_share.py',
    'tests/test_pipeline.py', 'tests/test_image_dedup.py', 'tests/test_video_sources.py', 'integrations/video_sources.py', 'third_party/yt-dlp-ejs-LICENSE.txt', 'tests/test_manual_gate.py',
]


def main():
    entries = {}
    from build_windows_exe import APP_FILES
    additions=['EXE-README.txt','MERGE-PLAN.md','MERGE-TEST-REPORT.md','launcher/Program.cs','launcher/app.manifest',
        'launcher/Compatibility.cs','scripts/prepare_vc_runtime.py','tests/test_runtime_compatibility.py',
        'tests/CompatibilityHarness.cs','tests/CompatibilityFake.cs',
        'scripts/verify_compatibility.py','COMPATIBILITY-TEST-REPORT.md',
        'scripts/build_windows_exe.py','scripts/prepare_merge.py','scripts/verify_merge.py','scripts/verify_windows_exe.py',
        'scripts/verify_tray.py','scripts/verify_packaged_merge.py',
        'tests/test_storyboard.py','tests/test_dsh_adapter.mjs','tests/test_dedup_ui.mjs','scripts/verify_release25.py']
    for relative in set(FILES+APP_FILES+additions):
        # Public source distributions fetch the licensed media tool at setup.
        if relative == 'runtime-tools/ffprobe.exe':
            continue
        entries[relative] = (ROOT / relative).read_bytes()
    for folder in ('dsh-plugin','third_party/dsh-video-storyboard'):
        for path in (ROOT/folder).rglob('*'):
            relative=path.relative_to(ROOT/folder)
            if not path.is_file() or any(p.startswith('.') or p in ('node_modules','test','tests','screenshots') for p in relative.parts):
                continue
            # Upstream documentation media have separate, restricted reuse terms.
            if folder == 'third_party/dsh-video-storyboard' and path.suffix.lower() in ('.png','.jpg','.jpeg','.gif','.webp','.mp4','.webm'):
                continue
            entries[(Path(folder)/relative).as_posix()]=path.read_bytes()
    license_path = ROOT / 'third_party' / 'MediaCrawler' / 'LICENSE'
    if not license_path.is_file():
        license_path = ROOT / 'third_party' / 'MediaCrawler-LICENSE.txt'
    entries['third_party/MediaCrawler-LICENSE.txt'] = license_path.read_bytes()
    manifest = {
        'name': 'Video Desk', 'version': '2.5.0', 'type': 'source-installer',
        'runtime_data_included': False,
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(entries.items())},
    }
    entries['PACKAGE-MANIFEST.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    DESTINATION.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(DESTINATION, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(PREFIX + '/' + name, data)
    with zipfile.ZipFile(DESTINATION) as archive:
        assert archive.testzip() is None, 'ZIP integrity check failed'
        assert len(archive.namelist()) == len(entries)
        for name in archive.namelist():
            parts = Path(name).parts
            assert not set(parts).intersection({'.local', '.venv', '.git', 'output', '__pycache__'})
            relative = name.removeprefix(PREFIX + '/')
            assert archive.read(name) == entries[relative]
    checksum = hashlib.sha256(DESTINATION.read_bytes()).hexdigest()
    DESTINATION.with_suffix('.zip.sha256').write_text(checksum + '  ' + DESTINATION.name + '\n', encoding='ascii')
    print(json.dumps({'file': str(DESTINATION), 'bytes': DESTINATION.stat().st_size,
                      'files': len(entries), 'sha256': checksum}, ensure_ascii=False))
    plugin=ROOT/'release/dsh-video-desk-2.5.0.zip'
    with zipfile.ZipFile(plugin,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in (ROOT/'dsh-plugin').iterdir():
            if p.is_file():archive.write(p,'dsh-video-desk/'+p.name)
    artifacts=[]
    for path in (DESTINATION,plugin,ROOT/'release/VideoDesk-2.5.0-Windows-x64.exe',ROOT/'release/SenseVoice-offline.tar.bz2'):
        if not path.is_file():continue
        with path.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        path.with_name(path.name+'.sha256').write_text(checksum+'  '+path.name+'\n',encoding='ascii')
        artifacts.append({'name':path.name,'bytes':path.stat().st_size,'sha256':checksum})
    (ROOT/'release/VideoDesk-2.5.0-files.json').write_text(json.dumps({'version':'2.5.0','artifacts':artifacts},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()

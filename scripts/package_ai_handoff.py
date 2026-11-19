"""Package a private-data-free source handoff, confirmed requirements and sample.

Does not change the application's version or functionality and never starts it.
Run from an original checkout or the extracted handoff's source directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'VideoDesk-AI-Handoff'
SOURCE = '项目源码'
SAMPLE = '测试素材/2月21日.mp4'
DOCS = [
    '00_先读_交接说明.md', '01_最新修改需求_2.5待实施.md',
    '02_问题与测试记录.md', '03_给下一个AI的开始指令.txt',
]
EXCLUDED = {'.git', '.local', '.venv', 'venv', 'node_modules', '__pycache__',
            '.pytest_cache', 'output', 'release', 'browser_data', 'credentials'}
FORBIDDEN_SUFFIXES = {'.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '.log', '.har', '.pfx', '.pem', '.key'}
CODE_FOLDERS = ('integrations', 'static', 'launcher', 'scripts', 'tests', 'dsh-plugin', 'bin')
ROOT_FILES = (
    '.gitignore', 'app.py', 'README.md', 'SHARE-README.txt', 'EXE-README.txt',
    'requirements.txt', 'requirements-lock.txt', 'setup.ps1', 'install.cmd',
    'start.ps1', 'start.cmd', 'stop.ps1', 'stop.cmd', 'MERGE-PLAN.md',
)
TEXT_SUFFIXES = {'.py', '.js', '.mjs', '.ts', '.tsx', '.vue', '.cs', '.ps1', '.cmd',
                 '.json', '.toml', '.ini', '.yml', '.yaml', '.html', '.css', '.md', '.txt', '.example'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def permitted(relative):
    p = Path(relative)
    return (not p.is_absolute() and '..' not in p.parts
            and not set(p.parts).intersection(EXCLUDED)
            and p.suffix.lower() not in FORBIDDEN_SUFFIXES
            and (p.name != '.env' and not p.name.startswith('.env.') or p.name == '.env.example')
            and not p.name.lower().startswith(('.cookies-', 'cookies.', 'storage_state.')))


def checked_file(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Expected a regular source file: ' + str(path))
    for parent in [path, *path.parents]:
        if parent == ROOT.parent:
            break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Source links are excluded: ' + str(path))
    return path


def collect_source():
    files = {}

    def add(path, relative=None):
        name = relative or Path(path).relative_to(ROOT).as_posix()
        if permitted(name):
            files[f'{SOURCE}/{name}'] = checked_file(path)

    for name in ROOT_FILES:
        add(ROOT / name)
    for path in sorted(ROOT.glob('*TEST-REPORT.md')):
        add(path)
    for folder in CODE_FOLDERS:
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and permitted(path.relative_to(ROOT).as_posix()):
                add(path)
    for path in sorted((ROOT / 'runtime-tools').iterdir()):
        if path.name in ('ffprobe.exe', 'LICENSE', 'README.txt', 'media-tools.json'):
            add(path)
    for path in sorted((ROOT / 'third_party').iterdir()):
        if path.is_file() and path.suffix.lower() in ('.md', '.txt'):
            add(path)
    # MediaCrawler can contain user configuration in working directories. In
    # the original checkout use only upstream tracked files, with local edits
    # rejected. The exported source tree has no Git metadata; use its manifest.
    crawler = ROOT / 'third_party/MediaCrawler'
    if (crawler / '.git').exists():
        changed = subprocess.check_output(['git', '-C', str(crawler), 'status', '--porcelain', '--untracked-files=no'])
        if changed.strip():
            raise ValueError('Review modified MediaCrawler files before exporting')
        names = subprocess.check_output(['git', '-C', str(crawler), 'ls-files', '-z']).decode('utf-8').split('\0')
        for name in filter(None, names):
            if permitted(name):
                add(crawler / name)
    else:
        exported_manifest = ROOT.parent / 'PACKAGE-MANIFEST.json'
        if not exported_manifest.is_file():
            raise ValueError('MediaCrawler requires tracked-source provenance or a handoff manifest')
        prior = json.loads(exported_manifest.read_text('utf-8'))['files']
        prefix = SOURCE + '/third_party/MediaCrawler/'
        for name, meta in prior.items():
            if name.startswith(prefix):
                path = ROOT / name.removeprefix(SOURCE + '/')
                if digest(path) != meta['sha256']:
                    raise ValueError('Review modified exported MediaCrawler source: ' + name)
                add(path)
    add(crawler / 'LICENSE', 'third_party/MediaCrawler-LICENSE.txt')
    # The vendored storyboard is a source snapshot rather than an indexed Git
    # checkout. Explicit public-source roots include its tests and documentation.
    board = ROOT / 'third_party/dsh-video-storyboard'
    for path in sorted(board.rglob('*')):
        relative = path.relative_to(board)
        if path.is_file() and permitted(relative.as_posix()) and '.git' not in relative.parts:
            add(path)
    return files


def audit_content(entries):
    scanned = 0
    private = re.compile(r'(?:C:[\\/]+Users[\\/]+21657\b|wxid_[A-Za-z0-9_]+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}))', re.I)
    for name, data in entries.items():
        if not permitted(name):
            raise ValueError('Excluded file in handoff: ' + name)
        if Path(name).suffix.lower() not in TEXT_SUFFIXES:
            continue
        content = data.read_bytes() if isinstance(data, Path) else data
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            continue
        scanned += 1
        if private.search(text):
            raise ValueError('Review private path or credential-like content in: ' + name)
    return scanned


def baseline_identity(entries):
    old = ROOT / 'release/VideoDesk-2.4.1-source.zip'
    if not old.is_file():
        return {'comparison': 'baseline ZIP unavailable; source hashes are in this package manifest'}
    with zipfile.ZipFile(old) as archive:
        meta = json.loads(archive.read('VideoDesk-2.4.1/PACKAGE-MANIFEST.json'))
    changed = []
    for name, expected in meta['files'].items():
        item = entries.get(SOURCE + '/' + name)
        if item is None or digest(item) != expected:
            changed.append(name)
    if changed:
        raise ValueError('Baseline source changed; explicitly review before packaging: ' + ', '.join(changed))
    return {'version': '2.4.1', 'baseline_zip_sha256': digest(old),
            'baseline_files_compared': len(meta['files']), 'all_baseline_files_unchanged': True}


def verify_archive(path, expected):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError('ZIP CRC or duplicate-name failure')
        if set(names) != {PREFIX + '/' + name for name in expected}:
            raise ValueError('ZIP entry set differs from approved inputs')
        manifest = json.loads(archive.read(PREFIX + '/PACKAGE-MANIFEST.json'))
        for name, meta in manifest['files'].items():
            with archive.open(PREFIX + '/' + name) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != meta['sha256'] or archive.getinfo(PREFIX + '/' + name).file_size != meta['bytes']:
                raise ValueError('Archive content changed: ' + name)
        sums = archive.read(PREFIX + '/SHA256SUMS.txt').decode('utf-8')
        for line in sums.splitlines():
            checksum, name = line.split('  ', 1)
            with archive.open(PREFIX + '/' + name) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                    raise ValueError('Checksum list mismatch: ' + name)
        return len(names), len(manifest['files'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=ROOT / 'release/VideoDesk-2.4.1-to-2.5.0-AI-Handoff.zip')
    args = parser.parse_args()
    sample = args.sample.resolve(strict=True)
    if sample.is_symlink() or not sample.is_file():
        raise ValueError('Sample must be a real local file')
    sample_stat = sample.stat()
    sample_hash = digest(sample)
    probe = subprocess.run([str(ROOT / 'runtime-tools/ffprobe.exe'), '-v', 'error', '-show_entries',
        'format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate',
        '-of', 'json', str(sample)], capture_output=True, check=True, timeout=30)
    info = json.loads(probe.stdout)
    if not any(s.get('codec_type') == 'video' for s in info.get('streams', [])):
        raise ValueError('Sample has no readable video stream')
    docroot = ROOT / 'handoff' if (ROOT / 'handoff').is_dir() else ROOT.parent
    entries = collect_source()
    baseline = baseline_identity(entries)
    for name in DOCS:
        entries[name] = checked_file(docroot / name)
    entries[SAMPLE] = sample
    entries['测试配置.json'] = json_bytes({
        'baseline_version': '2.4.1', 'target_version': '2.5.0', 'target_implemented': False,
        'paths_relative_to': 'handoff root', 'source_directory': SOURCE,
        'sample': {'path': SAMPLE, 'bytes': sample_stat.st_size, 'sha256': sample_hash, 'probe': info},
        'default_test_network': 'local only; no upload of the supplied video',
        'isolated_data_directory': SOURCE + '/.local/handoff-dev/data',
        'isolated_output_directory': SOURCE + '/.local/handoff-dev/output',
        'rules_test': {'mode': 'seconds', 'value': 3, 'start': 0, 'duration': 0, 'limit': 2000,
                       'outputs': {'frames': True}, 'dedup': False},
        'ai_test': {'strategy': 'compact', 'pick': 'mid', 'transcript': 'auto',
                    'screen_text': True, 'scan_seconds': 1, 'max_cells_per_sheet': 4},
        'implemented_test_status': 'metadata probe only for this sample; full app processing not yet verified',
    })
    entries['第三方源码记录.json'] = json_bytes({
        'MediaCrawler': {'reference_commit': '8f583999006d5b692272c608440e193ca282e73b',
                          'license': SOURCE + '/third_party/MediaCrawler/LICENSE', 'git_history_included': False},
        'dsh-video-storyboard': {'reference_commit': 'e124ae6fcfb31f3572ea24aad093a86897b556a3',
                                 'license': SOURCE + '/third_party/dsh-video-storyboard/LICENSE',
                                 'notice': SOURCE + '/third_party/dsh-video-storyboard/NOTICE', 'git_history_included': False},
        'note': 'Reference commits identify upstream bases; shipped file hashes identify the exact adapted snapshot.',
    })
    text_count = audit_content(entries)
    created = datetime.now(timezone.utc).isoformat()
    entries['04_打包验收记录.json'] = json_bytes({
        'created_utc': created, 'package_type': 'source-and-requirements-handoff',
        'baseline': baseline, 'target_version': '2.5.0', 'target_implemented': False,
        'sample_metadata_readable': True, 'sample_bytes': sample_stat.st_size, 'sample_sha256': sample_hash,
        'sample_full_pipeline_tested': False,
        'content_scan': {'text_files_scanned': text_count, 'private_path_or_credential_marker_findings': 0,
                         'scope': 'allowlisted source and handoff documents, not a universal secret detector'},
        'personal_runtime_data_included': False, 'large_runtime_or_model_packages_included': False,
        'delivery_checks': ['ZIP CRC', 'no duplicate/traversal paths', 'every manifest file size and SHA256',
                            'checksums including manifest', 'sample hash and modification time unchanged'],
        'delivery_rule': 'Final ZIP filename is published only after all delivery checks pass; external .files.json records results.',
        'software_test_scope': 'See issue record; no claim that 2.5 was implemented or that this sample finished full processing.',
    })
    filemeta = {}
    for name, value in sorted(entries.items()):
        filemeta[name] = {'bytes': value.stat().st_size if isinstance(value, Path) else len(value),
                          'sha256': digest(value) if isinstance(value, Path) else hashlib.sha256(value).hexdigest()}
    manifest = {'type': 'ai-development-handoff', 'created_utc': created, 'baseline_version': '2.4.1',
                'planned_version': '2.5.0', 'planned_changes_implemented': False,
                'manifest_scope': 'All entries except PACKAGE-MANIFEST.json and SHA256SUMS.txt; SHA256SUMS also hashes the manifest.',
                'files': filemeta}
    entries['PACKAGE-MANIFEST.json'] = json_bytes(manifest)
    hashes = {name: meta['sha256'] for name, meta in filemeta.items()}
    hashes['PACKAGE-MANIFEST.json'] = hashlib.sha256(entries['PACKAGE-MANIFEST.json']).hexdigest()
    entries['SHA256SUMS.txt'] = ''.join(f'{checksum}  {name}\n' for name, checksum in sorted(hashes.items())).encode('utf-8')
    destination = args.destination.resolve()
    if destination == sample or destination.suffix.lower() != '.zip':
        raise ValueError('Choose a ZIP destination distinct from the sample')
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix('.zip.partial')
    with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, value in sorted(entries.items()):
            mode = zipfile.ZIP_STORED if name == SAMPLE else zipfile.ZIP_DEFLATED
            if isinstance(value, Path):
                archive.write(value, PREFIX + '/' + name, compress_type=mode)
            else:
                archive.writestr(PREFIX + '/' + name, value, compress_type=mode)
    count, verified = verify_archive(partial, entries)
    after = sample.stat()
    if digest(sample) != sample_hash or after.st_size != sample_stat.st_size or after.st_mtime_ns != sample_stat.st_mtime_ns:
        raise ValueError('Original sample changed during packaging')
    # Confirm all source and document bytes still match the reviewed snapshot.
    for name, meta in filemeta.items():
        value = entries[name]
        if isinstance(value, Path) and digest(value) != meta['sha256']:
            raise ValueError('Source changed during packaging: ' + name)
    partial.replace(destination)
    checksum = digest(destination)
    destination.with_name(destination.name + '.sha256').write_text(checksum + '  ' + destination.name + '\n', encoding='utf-8')
    result = {'file': destination.name, 'bytes': destination.stat().st_size, 'sha256': checksum,
              'archive_entries': count, 'manifest_files_verified': verified, 'zip_crc_passed': True,
              'all_manifest_hashes_passed': True, 'sample_hash_unchanged': True, 'sample_mtime_unchanged': True,
              'baseline_files_unchanged': baseline.get('all_baseline_files_unchanged'),
              'personal_runtime_data_included': False, 'baseline_version': '2.4.1',
              'target_version': '2.5.0', 'target_implemented': False}
    destination.with_suffix('.files.json').write_bytes(json_bytes(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

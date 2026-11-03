"""Local image deduplication using ImageHash (BSD-2-Clause).

Scanning never changes originals. All groups compare to one representative,
not a transitive chain. Exports copy files; quarantine uses journaled same-drive
renames and can be restored without overwriting existing files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid
import warnings

from PIL import Image, ImageOps
from . import runtime_support

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.gif', '.avif'}
QUARANTINE = '.video-desk-quarantine'


def save_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def read_report(folder):
    folder = Path(folder)
    report = json.loads((folder / 'dedup-report.json').read_text(encoding='utf-8'))
    for operation in report['operations']:
        journal = folder / '.dedup-journal' / (operation['id'] + '.jsonl')
        entries = {e['id']: e for e in operation['entries']}
        if journal.is_file():
            for line in journal.read_text(encoding='utf-8').splitlines():
                try:
                    entry = json.loads(line)
                    entries[entry['id']] = entry
                except (ValueError, KeyError):
                    continue  # A crash may leave the final journal row incomplete.
        operation['entries'] = list(entries.values())
    return report


def journal_entry(folder, operation, entry):
    directory = folder / '.dedup-journal'
    directory.mkdir(exist_ok=True)
    with (directory / (operation['id'] + '.jsonl')).open('a', encoding='utf-8') as stream:
        stream.write('\n' + json.dumps(entry, ensure_ascii=False) + '\n')
        stream.flush()
        if operation['action'] == 'quarantine':
            os.fsync(stream.fileno())


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def is_link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def inside(root, relative):
    """Require an ordinary path inside root, rejecting junctions and symlinks."""
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('图片路径不合法')
    current = root
    for part in rel.parts:
        current = current / part
        if is_link(current):
            raise ValueError('不处理符号链接或目录联接：' + str(relative))
    if not current.resolve().is_relative_to(root):
        raise ValueError('图片超出扫描目录')
    return current


def source_root(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('请选择存在的本机图片文件夹，或填写完整路径')
    return path.resolve()


def image_paths(root, recursive, excluded, check):
    def walk_error(exc):
        raise ValueError('无法读取部分目录：' + str(exc.filename)) from exc
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        check()
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and
                         not is_link(base / d) and not (base / d).resolve().is_relative_to(excluded)) if recursive else []
        for name in sorted(files):
            path = base / name
            if path.suffix.lower() in EXTENSIONS and not is_link(path):
                yield path


class HashIndex:
    """BK-tree of representative pHashes, using integer Hamming distance."""
    def __init__(self):
        self.root = None

    def add(self, value, group):
        node = (value, [group], {})
        if self.root is None:
            self.root = node
            return
        current = self.root
        while True:
            distance = (value ^ current[0]).bit_count()
            if distance == 0:
                current[1].append(group)
                return
            if distance not in current[2]:
                current[2][distance] = node
                return
            current = current[2][distance]

    def find(self, value, radius):
        stack = [self.root] if self.root else []
        while stack:
            current = stack.pop()
            distance = (value ^ current[0]).bit_count()
            if distance <= radius:
                yield from current[1]
            stack.extend(node for edge, node in current[2].items()
                         if distance - radius <= edge <= distance + radius)


def scan(source, folder, mode='exact', threshold=4, recursive=True, limit=20000,
         check=lambda: None, progress=lambda value, message: None):
    from importlib.metadata import version
    if mode == 'similar':
        runtime_support.require('dedup')
        import imagehash
        import numpy as np
    root, folder = source_root(source), Path(folder).resolve()
    if folder == root:
        raise ValueError('报告目录不能与扫描目录相同')
    thumbs = folder / '.dedup-thumbs'
    thumbs.mkdir(exist_ok=True)
    items, skipped, features = [], [], {}
    truncated = False
    for count, path in enumerate(image_paths(root, recursive, folder, check)):
        if count >= limit:
            truncated = True
            break
        check()
        if count % 10 == 0:
            progress(min(69, 5 + count / max(limit, 1) * 60), f'正在读取第 {count + 1} 张图片')
        try:
            before = path.stat()
            fingerprint = digest(path)
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as original:
                    animated = getattr(original, 'n_frames', 1) > 1
                    image = ImageOps.exif_transpose(original).convert('RGBA')
                    background = Image.new('RGBA', image.size, 'white')
                    image = Image.alpha_composite(background, image).convert('RGB')
                    width, height = image.size
                    image.thumbnail((512, 512))
                    phash = imagehash.phash(image) if mode == 'similar' and not animated else None
                    dhash = imagehash.dhash(image) if phash is not None else None
                    pixels = np.asarray(image.resize((32, 32)), dtype=np.int16) if phash is not None else None
                    image.thumbnail((300, 200))
                    iid = f'{len(items):08d}'
                    image.save(thumbs / (iid + '.jpg'), quality=82)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('读取期间文件发生变化，请重新扫描')
            item = {'id': iid, 'relative': path.relative_to(root).as_posix(), 'size': before.st_size,
                    'mtime_ns': before.st_mtime_ns, 'sha256': fingerprint, 'width': width,
                    'height': height, 'animated': animated}
            items.append(item)
            if phash is not None:
                features[iid] = (int(str(phash), 16), int(str(dhash), 16), pixels, width / height)
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            if len(skipped) < 500:
                skipped.append({'file': path.relative_to(root).as_posix(), 'reason': str(exc)[:300]})
    check()
    progress(72, '正在比较图片指纹')
    # Best resolution is the default representative, with deterministic tie breaks.
    ordered = sorted(items, key=lambda i: (-i['width'] * i['height'], -i['size'], i['relative']))
    clusters, exact, index = [], {}, HashIndex()
    for n, item in enumerate(ordered):
        check()
        if n % 50 == 0:
            progress(72 + n / max(len(items), 1) * 25, f'正在比对 {n + 1} / {len(items)} 张图片')
        group = exact.get(item['sha256'])
        feature = features.get(item['id'])
        if group is None and feature:
            ph, dh, pixels, ratio = feature
            for candidate in index.find(ph, threshold):
                rp, rd, reference, rr = features[candidate['keep']]
                if (abs(ratio / rr - 1) <= .03 and (dh ^ rd).bit_count() <= max(4, threshold * 2)
                        and float(np.abs(pixels - reference).mean()) <= 12 + threshold * 2):
                    group = candidate
                    break
        if group is None:
            group = {'id': f'g{len(clusters):08d}', 'keep': item['id'], 'members': [], 'kind': 'exact'}
            clusters.append(group)
            if feature:
                index.add(feature[0], group)
        elif item['sha256'] != next(i['sha256'] for i in group['members'][:1]):
            group['kind'] = 'similar'
        group['members'].append(item)
        exact[item['sha256']] = group
    groups = [{**g, 'members': [i['id'] for i in g['members']]} for g in clusters if len(g['members']) > 1]
    report = {'version': 1, 'engine': 'ImageHash ' + version('ImageHash'), 'source': str(root),
              'mode': mode, 'threshold': threshold, 'recursive': recursive,
              'items': items, 'groups': groups, 'skipped': skipped, 'truncated': truncated,
              'summary': {'images': len(items), 'groups': len(groups),
                          'duplicates': sum(len(g['members']) - 1 for g in groups),
                          'skipped': len(skipped)}, 'operations': []}
    save_json(folder / 'dedup-report.json', report)
    return report


def validated_source(root, item):
    path = inside(root, item['relative'])
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode) or digest(path) != item['sha256']:
        raise ValueError('文件已变化或不存在，请重新扫描：' + item['relative'])
    return path


def selected_duplicates(report, choices):
    groups = {g['id']: g for g in report['groups']}
    if set(choices) - set(groups):
        raise ValueError('包含未知重复分组')
    remove = set()
    for gid, keeper in choices.items():
        if keeper not in groups[gid]['members']:
            raise ValueError('保留图片不属于该分组')
        remove.update(i for i in groups[gid]['members'] if i != keeper)
    return remove


def operate(folder, action, choices, jid, check=lambda: None, progress=lambda p, m: None):
    folder = Path(folder).resolve()
    report = read_report(folder)
    root = source_root(report['source'])
    if action == 'restore':
        return restore(folder, report, root, progress)
    if any(o['action'] == 'quarantine' and o['state'] != 'restored' for o in report['operations']):
        raise ValueError('本次扫描已有隔离记录，请先恢复，或重新扫描后再处理')
    remove = selected_duplicates(report, choices)
    selected = [i for i in report['items'] if (i['id'] in remove) == (action == 'quarantine')]
    if not selected:
        raise ValueError('没有需要处理的图片')
    # Validate the whole plan before making any changes (including chosen keepers).
    for n, item in enumerate(report['items']):
        check()
        validated_source(root, item)
        if n % 50 == 0:
            progress(n / max(len(report['items']), 1) * 30, '正在核对原文件是否变化')
    opid = uuid.uuid4().hex[:12]
    target = (folder / ('unique-' + opid)) if action == 'export' else inside(root, QUARANTINE + '/' + jid + '-' + opid)
    target.mkdir(parents=True, exist_ok=False)
    operation = {'id': opid, 'action': action, 'state': 'pending', 'directory': str(target), 'entries': []}
    report['operations'].append(operation)
    save_json(folder / 'dedup-report.json', report)
    try:
        for n, item in enumerate(selected):
            check()
            source = validated_source(root, item)
            relative = item['relative'] if action == 'export' else item['id'] + Path(item['relative']).suffix.lower()
            dest = inside(target.resolve(), relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            entry = {'id': item['id'], 'source': item['relative'], 'destination': relative,
                     'sha256': item['sha256'], 'state': 'pending'}
            operation['entries'].append(entry)
            journal_entry(folder, operation, entry)
            if action == 'export':
                with source.open('rb') as reader, dest.open('xb') as writer:
                    shutil.copyfileobj(reader, writer)
                shutil.copystat(source, dest)
                if digest(dest) != item['sha256']:
                    raise ValueError('复制时文件发生变化，请重新扫描：' + item['relative'])
            else:
                if dest.exists():
                    raise ValueError('隔离目标已存在')
                os.rename(source, dest)  # Same source volume; journal exists before rename.
            entry['state'] = 'done'
            journal_entry(folder, operation, entry)
            progress(30 + (n + 1) / len(selected) * 69, f'已处理 {n + 1} / {len(selected)} 张图片')
        operation['state'] = 'done'
    except BaseException:
        operation['state'] = 'partial'
        raise
    finally:
        save_json(folder / 'dedup-report.json', report)
    return operation


def restore(folder, report, root, progress):
    operations = [o for o in report['operations'] if o['action'] == 'quarantine' and o['state'] != 'restored']
    if not operations:
        raise ValueError('没有需要恢复的隔离记录')
    restored = 0
    for operation in operations:
        target = Path(operation['directory'])
        if not target.is_relative_to(root / QUARANTINE):
            raise ValueError('隔离目录不合法')
        target = inside(root, target.relative_to(root)).resolve()
        # Preflight prevents a known collision from causing a partially restored plan.
        for entry in operation['entries']:
            if entry['state'] == 'restored':
                continue
            source, dest = inside(target, entry['destination']), inside(root, entry['source'])
            if source.is_file():
                if dest.exists():
                    raise ValueError('原位置已存在文件，未覆盖。请先另存该文件再恢复：' + entry['source'])
                if digest(source) != entry['sha256']:
                    raise ValueError('隔离文件已变化，停止恢复：' + entry['source'])
            elif entry['state'] == 'done' and not (dest.is_file() and digest(dest) == entry['sha256']):
                raise ValueError('隔离文件缺失：' + entry['source'])
        for entry in operation['entries']:
            if entry['state'] == 'restored':
                continue
            source, dest = inside(target, entry['destination']), inside(root, entry['source'])
            if source.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    raise ValueError('原位置已存在文件，未覆盖：' + entry['source'])
                os.rename(source, dest)
                restored += 1
            entry['state'] = 'restored'
            journal_entry(folder, operation, entry)
            progress(50, f'已恢复 {restored} 张图片')
        operation['state'] = 'restored'
        save_json(folder / 'dedup-report.json', report)
    return {'action': 'restore', 'count': restored}

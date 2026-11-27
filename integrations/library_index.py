"""A small local search index over completed AI evidence packages.

The package folder stays the source of truth.  This module only builds a
derived SQLite database so a large library can answer "which video said this?"
with a timestamp, without re-opening every transcript or re-uploading every
folder to an AI.

The index is intentionally incremental: an episode is replaced by job id when
it is regenerated.  FTS5 trigram handles Chinese phrases of three characters
or longer; a LIKE fallback covers one- and two-character queries and platforms
whose SQLite build lacks trigram support.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB_NAME = 'library-index.sqlite'
UPLOAD = '一起上传给AI'
PRIVATE = '无需上传'
_LOCK = threading.RLock()
_REBUILDING = threading.Lock()


def _path(a) -> Path:
    return Path(a.DATA) / DB_NAME


def _connect(a):
    path = _path(a)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=NORMAL')
    except sqlite3.OperationalError:
        pass
    return con


def _schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS packages (
            package_id TEXT PRIMARY KEY,
            job_id TEXT,
            title TEXT,
            source TEXT,
            directory TEXT,
            created REAL,
            updated REAL,
            duration REAL,
            transcript_rows INTEGER DEFAULT 0,
            visual_rows INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id TEXT NOT NULL,
            job_id TEXT,
            title TEXT,
            source TEXT,
            kind TEXT NOT NULL,
            video TEXT,
            start REAL,
            end REAL,
            original_start REAL,
            original_end REAL,
            text TEXT NOT NULL,
            visual_id TEXT,
            image TEXT,
            detail TEXT,
            confidence REAL,
            row_index INTEGER
        );
        CREATE INDEX IF NOT EXISTS segments_package ON segments(package_id);
        CREATE INDEX IF NOT EXISTS segments_kind_time ON segments(kind, start);
    """)
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(text, tokenize='trigram')")
    except sqlite3.OperationalError:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(text, tokenize='unicode61')")
    con.commit()


def _drop_package(con, package_id: str):
    ids = [row['id'] for row in con.execute(
        'SELECT id FROM segments WHERE package_id=?', (package_id,))]
    if ids:
        con.executemany('DELETE FROM segments_fts WHERE rowid=?', [(item,) for item in ids])
    con.execute('DELETE FROM segments WHERE package_id=?', (package_id,))
    con.execute('DELETE FROM packages WHERE package_id=?', (package_id,))


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return '\n'.join(str(x).strip() for x in value if str(x).strip())
    return str(value or '').strip()


def _number(value, fallback=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def upsert_package(a, jid: str, base, title: str = '') -> dict:
    """Index one completed package from its private combined source JSON."""
    base = Path(base)
    source_path = base / PRIVATE / '联合内容源.json'
    if not source_path.is_file():
        return {'ok': False, 'skipped': 'missing 联合内容源.json', 'package': str(base)}
    try:
        data = json.loads(source_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return {'ok': False, 'skipped': f'unreadable 联合内容源.json: {exc}', 'package': str(base)}
    job = {}
    try:
        job = a.job_by_id(jid)
    except Exception:
        pass
    package_title = str(data.get('title') or title or job.get('title') or base.name)
    source = str(job.get('source') or data.get('source') or '')
    created = _number(job.get('created'), time.time())
    left = _number(data.get('from'), 0) or 0
    rows = data.get('rows') or []
    visuals = data.get('visuals') or []
    duration = _number(data.get('to'), 0)
    package_id = str(jid)
    segments = []
    for index, row in enumerate(rows):
        text = _text(row.get('text'))
        if not text:
            continue
        segments.append({
            'kind': 'transcript', 'video': 'V01',
            'start': _number(row.get('from')), 'end': _number(row.get('to')),
            'original_start': _number(row.get('original_from'), _number(row.get('from'))),
            'original_end': _number(row.get('original_to'), _number(row.get('to'))),
            'text': text, 'visual_id': '', 'image': '', 'detail': '', 'confidence': None,
            'row_index': index,
        })
    for index, visual in enumerate(visuals):
        text = _text(visual.get('text'))
        if not text:
            continue
        at = _number(visual.get('at'), 0) or 0
        confidence = visual.get('confidence') or []
        try:
            confidence_min = min(float(x) for x in confidence)
        except (TypeError, ValueError):
            confidence_min = None
        segments.append({
            'kind': 'ocr', 'video': 'V01',
            'start': at - left, 'end': at - left,
            'original_start': at, 'original_end': at,
            'text': text, 'visual_id': visual.get('id') or '',
            'image': visual.get('sheet') or '', 'detail': visual.get('detail') or '',
            'confidence': confidence_min, 'row_index': index,
        })
    with _LOCK:
        con = _connect(a)
        try:
            _schema(con)
            _drop_package(con, package_id)
            con.execute(
                'INSERT INTO packages(package_id,job_id,title,source,directory,created,updated,duration,transcript_rows,visual_rows,warnings) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (package_id, jid, package_title, source, str(base), created, time.time(), duration,
                 len(rows), len(visuals), len(data.get('warnings') or [])))
            for item in segments:
                cur = con.execute(
                    'INSERT INTO segments(package_id,job_id,title,source,kind,video,start,end,original_start,original_end,text,visual_id,image,detail,confidence,row_index) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (package_id, jid, package_title, source, item['kind'], item['video'],
                     item['start'], item['end'], item['original_start'], item['original_end'],
                     item['text'], item['visual_id'], item['image'], item['detail'],
                     item['confidence'], item['row_index']))
                con.execute('INSERT INTO segments_fts(rowid,text) VALUES(?,?)', (cur.lastrowid, item['text']))
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    return {'ok': True, 'package': package_id, 'segments': len(segments),
            'transcript': len(rows), 'visuals': len(visuals), 'title': package_title}


def rebuild(a) -> dict:
    """Re-scan all completed packages known to the running app."""
    packages = []
    try:
        values = list(a.JOBS.values())
    except Exception:
        values = []
    for job in values:
        directory = job.get('directory')
        if not directory:
            continue
        base = Path(directory)
        if not (base / PRIVATE / '联合内容源.json').is_file():
            continue
        packages.append((job, base))
    with _REBUILDING:
        with _LOCK:
            con = _connect(a)
            try:
                _schema(con)
                con.execute('DELETE FROM segments_fts')
                con.execute('DELETE FROM segments')
                con.execute('DELETE FROM packages')
                con.commit()
            finally:
                con.close()
        results = []
        for job, base in packages:
            try:
                results.append(upsert_package(a, str(job.get('id') or ''), base, str(job.get('title') or '')))
            except Exception as exc:
                results.append({'ok': False, 'package': str(base), 'error': str(exc)})
    return {
        'ok': all(item.get('ok') for item in results) if results else True,
        'packages': sum(1 for item in results if item.get('ok')),
        'skipped': sum(1 for item in results if not item.get('ok')),
        'segments': sum(int(item.get('segments') or 0) for item in results),
        'errors': [item for item in results if not item.get('ok')],
        'db': str(_path(a)),
    }


def _snippet(text: str, query: str, width: int = 150) -> str:
    text = ' '.join(str(text or '').split())
    if not text:
        return ''
    low = text.casefold()
    q = query.casefold()
    pos = low.find(q)
    if pos < 0:
        return text[:width] + ('…' if len(text) > width else '')
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    return ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')


def _search_rows(con, query: str, limit: int, kind: str):
    if len(query) >= 3:
        phrase = '"' + query.replace('"', ' ') + '"'
        sql = ("SELECT s.*, p.title AS package_title, p.directory AS package_directory "
               "FROM segments_fts JOIN segments s ON s.id=segments_fts.rowid "
               "JOIN packages p ON p.package_id=s.package_id "
               "WHERE segments_fts MATCH ?")
        params = [phrase]
        if kind in ('transcript', 'ocr'):
            sql += " AND s.kind=?"
            params.append(kind)
        sql += " ORDER BY p.updated DESC, rank LIMIT ?"
        params.append(limit)
        try:
            rows = con.execute(sql, params).fetchall()
            if rows:
                return rows, 'fts'
        except sqlite3.OperationalError:
            pass
    sql = ("SELECT s.*, p.title AS package_title, p.directory AS package_directory "
           "FROM segments s JOIN packages p ON p.package_id=s.package_id "
           "WHERE s.text LIKE ? ESCAPE '\\'")
    if kind in ('transcript', 'ocr'):
        sql += " AND s.kind=?"
    sql += " ORDER BY p.updated DESC, s.start ASC, s.id ASC LIMIT ?"
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    params = ['%' + escaped + '%']
    if kind in ('transcript', 'ocr'):
        params.append(kind)
    params.append(limit)
    return con.execute(sql, params).fetchall(), 'like'


def search(a, query: str, limit: int = 20, kind: str = '') -> dict:
    query = ' '.join(str(query or '').split())
    if not query:
        return {'ok': True, 'query': '', 'items': [], 'engine': 'none'}
    limit = max(1, min(int(limit or 20), 100))
    if kind not in ('transcript', 'ocr'):
        kind = ''
    con = _connect(a)
    try:
        _schema(con)
        rows, engine = _search_rows(con, query, min(limit * 8, 500), kind)
        items = []
        seen = set()
        for row in rows:
            start = row['original_start'] if row['original_start'] is not None else row['start']
            end = row['original_end'] if row['original_end'] is not None else row['end']
            dedupe = (row['kind'], row['source'] or row['package_title'] or row['title'],
                      round(float(start or 0), 1), (row['text'] or '')[:60])
            if dedupe in seen:
                continue
            seen.add(dedupe)
            items.append({
                'package_id': row['package_id'], 'job_id': row['job_id'],
                'title': row['package_title'] or row['title'],
                'source': row['source'] or '',
                'kind': row['kind'], 'video': row['video'] or 'V01',
                'start': round(float(start), 3) if start is not None else None,
                'end': round(float(end), 3) if end is not None else None,
                'visual_id': row['visual_id'] or '', 'image': row['image'] or '',
                'detail': row['detail'] or '',
                'confidence': row['confidence'],
                'text': _snippet(row['text'], query),
                'directory': row['package_directory'] or '',
            })
            if len(items) >= limit:
                break
        return {'ok': True, 'query': query, 'engine': engine, 'count': len(items), 'items': items}
    finally:
        con.close()


def stats(a) -> dict:
    path = _path(a)
    if not path.is_file():
        return {'ok': True, 'packages': 0, 'segments': 0, 'transcript': 0, 'ocr': 0,
                'db': str(path), 'size': 0, 'updated': None}
    con = _connect(a)
    try:
        _schema(con)
        def count(sql):
            return int(con.execute(sql).fetchone()[0])
        updated = con.execute('SELECT MAX(updated) FROM packages').fetchone()[0]
        return {
            'ok': True,
            'packages': count('SELECT COUNT(*) FROM packages'),
            'segments': count('SELECT COUNT(*) FROM segments'),
            'transcript': count("SELECT COUNT(*) FROM segments WHERE kind='transcript'"),
            'ocr': count("SELECT COUNT(*) FROM segments WHERE kind='ocr'"),
            'db': str(path), 'size': path.stat().st_size,
            'updated': updated,
        }
    finally:
        con.close()


def install(app, a):
    @app.get('/api/library/stats')
    def library_stats():
        return stats(a)

    @app.get('/api/library/search')
    def library_search(q: str = '', limit: int = 20, kind: str = ''):
        return search(a, q, limit, kind)

    @app.post('/api/library/rebuild')
    def library_rebuild():
        return rebuild(a)

    def warm():
        try:
            if stats(a).get('packages', 0) == 0:
                rebuild(a)
        except Exception:
            pass

    threading.Thread(target=warm, name='library-warm', daemon=True).start()
    return {'search': lambda q, limit=20: search(a, q, limit),
            'stats': lambda: stats(a), 'rebuild': lambda: rebuild(a)}

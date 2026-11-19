"""Exploratory black-box probe of QA-27 isolated VideoDesk instance.

Writes raw request/response records to repro/evidence/api_probe.json.
This script only talks to the instance whose server.json lives in repro/qa/home.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_client import Client, OPENER  # noqa: E402
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "repro" / "evidence" / "api_probe.json"

c = Client()
records = []


def call(label, method, path, payload=None, headers=None, base=None):
    if base:
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
        try:
            with OPENER.open(req, timeout=20) as r:
                status, body = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            status, body = -1, f"{type(e).__name__}: {e}"
    else:
        resp = c.request(method, path, payload, headers=headers)
        status, body = resp.status, resp.text
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = None
    records.append({"label": label, "method": method, "path": path, "payload": payload,
                    "status": status, "body": parsed if parsed is not None else body[:2000]})
    return status, parsed if parsed is not None else body


print("== A: anchor normalization / identity ==")
URLS = [
    ("A01 live room", "https://live.douyin.com/7381928374650910000"),
    ("A02 same room https www /live/", "https://www.douyin.com/live/7381928374650910000"),
    ("A03 same room with tracking query", "https://live.douyin.com/7381928374650910000?utm_source=copy_link"),
    ("A04 same room trailing slash", "https://live.douyin.com/7381928374650910000/"),
    ("A05 same room webcast reflow", "https://webcast.amemv.com/douyin/webcast/reflow/7381928374650910000/"),
    ("A06 user homepage", "https://www.douyin.com/user/MS4wLjABAAAAqa_synth_anchor_0001"),
    ("A07 short share with text", "  8.86 复制打开抖音，看看主播 https://v.douyin.com/iQaSynth01/ 复制此链接，打开Dou音搜索 "),
    ("A08 leading/trailing spaces", "   https://live.douyin.com/7381928374650910099   "),
    ("A09 illegal link", "not a url"),
    ("A10 non-douyin", "https://www.example.com/live/123456"),
    ("A11 douyin published video", "https://www.douyin.com/video/7381928374650910000"),
    ("A12 19-digit exact room id", "https://live.douyin.com/7381928374650910213"),
    ("A13 name blank", None),
]
for label, url in URLS:
    payload = {"name": "", "url": url} if url is not None else {"name": "", "url": "https://live.douyin.com/9999000011112222"}
    call(label, "POST", "/api/live/anchors", payload)

call("A20 state", "GET", "/api/live")

print("== B: settings boundaries ==")
for label, payload in [
    ("B01 concurrency 0", {"concurrency": 0, "segment_minutes": 30}),
    ("B02 concurrency 9", {"concurrency": 9, "segment_minutes": 30}),
    ("B03 concurrency 1", {"concurrency": 1, "segment_minutes": 30}),
    ("B04 concurrency 8", {"concurrency": 8, "segment_minutes": 30}),
    ("B05 segment 0", {"concurrency": 4, "segment_minutes": 0}),
    ("B06 segment 121", {"concurrency": 4, "segment_minutes": 121}),
    ("B07 output relative", {"concurrency": 4, "segment_minutes": 30, "output": "relative/dir"}),
    ("B08 output missing absolute", {"concurrency": 4, "segment_minutes": 30, "output": "E:/VideoDesk/repro/qa/no-such-parent-xyz/sub"}),
]:
    call(label, "POST", "/api/live/settings", payload)

print("== C: general settings / auth ==")
call("C01 settings relative", "POST", "/api/settings", {"output": "relative"})
call("C02 settings QA absolute", "POST", "/api/settings", {"output": "E:/VideoDesk/repro/qa/output"})
call("C03 no token", "POST", "/api/settings", {"output": "E:/VideoDesk/repro/qa/output"}, headers={"x-app-token": "WRONG"})
call("C04 cross origin", "POST", "/api/settings", {"output": "E:/VideoDesk/repro/qa/output"}, headers={"Origin": "http://evil.example"})
call("C05 state", "GET", "/api/state")

print("== D: translation / hardware / phone ==")
call("D01 hardware", "GET", "/api/hardware")
call("D02 components", "GET", "/api/translation/components")
call("D03 services", "GET", "/api/translation/services")
call("D04 translate missing file", "POST", "/api/translation", {"paths": ["E:/VideoDesk/repro/qa/materials/nope.mp4"], "output": "E:/VideoDesk/repro/qa/output"})
call("D05 translate relative path", "POST", "/api/translation", {"paths": ["relative.mp4"]})
call("D06 phone state", "GET", "/api/phone")
call("D07 phone record unknown anchor", "POST", "/api/phone/record", {"anchor_id": "doesnotexist", "action": "record"})
call("D08 phone decision unknown", "POST", "/api/phone/decision", {"event_id": "nope", "target": "pc"})
call("D09 phone state repeat", "GET", "/api/phone")

print("== E: job/file boundary ==")
call("E01 unknown job files", "GET", "/api/jobs/deadbeef000/files/a.txt")
call("E02 unknown job cleanup", "GET", "/api/materials/deadbeef000/cleanup")
call("E03 cleanup preview empty", "POST", "/api/materials/cleanup-preview", {"ids": []})
call("E04 cleanup preview unknown", "POST", "/api/materials/cleanup-preview", {"ids": ["deadbeef000"]})
call("E05 cleanup batch unknown", "POST", "/api/materials/cleanup-batch", {"ids": ["deadbeef000"]})
call("E06a live replays", "GET", "/api/live/replays")
call("E08 replay preview bad jid", "POST", "/api/live/replays/deadbeef000/preview/0", {})
call("E09 extract-local nonexistent", "POST", "/api/extract-local", {"path": "E:/VideoDesk/repro/qa/materials/nope.mp4"})
call("E10 extract-local zero byte", "POST", "/api/extract-local", {"path": "E:/VideoDesk/repro/qa/materials/sentinel_zero_byte.mp4"})
call("E11 extract-local broken", "POST", "/api/extract-local", {"path": "E:/VideoDesk/repro/qa/materials/sentinel_broken.mp4"})
call("E12 local preview nonexistent", "POST", "/api/local-video/preview", {"path": "E:/VideoDesk/repro/qa/materials/nope.mp4"})
call("E13 download empty urls", "POST", "/api/download", {"urls": []})
call("E14 dedup unknown job", "GET", "/api/dedup/deadbeef000")

OUT.write_text(json.dumps({"instance": c.url, "installation_id": c.installation_id,
                           "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nwrote {OUT} with {len(records)} records")
for r in records:
    print(f"{r['label'][:42]:44s} {r['method']:6s} {r['status']:>4}  {str(r['body'])[:150]}")

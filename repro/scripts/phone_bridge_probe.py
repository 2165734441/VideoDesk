"""Probe the PC-side phone companion bridge of the QA-27 isolated instance.

The bridge is a loopback HTTP server started by the app itself. We discover its
port from listening sockets owned by the instance PID (from data/server.json),
and authenticate using the key generated in the ISOLATED QA home only.
No adb reverse, no APK install, no interaction with the user's real phone.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_client import Client, OPENER, ROOT  # noqa: E402

server = json.loads((ROOT / "repro/qa/home/data/server.json").read_text(encoding="utf-8"))
pid = str(server["pid"])
key = json.loads((ROOT / "repro/qa/home/data/credentials/mobile-pair.json").read_text(encoding="utf-8"))["key"]

netstat = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True).stdout.decode("utf-8", "replace")
ports = [int(m.group(1)) for line in netstat.splitlines()
         if "LISTENING" in line and line.split()[-1] == pid
         for m in [re.search(r"127\.0\.0\.1:(\d+)", line)] if m]


def bridge_request(port, method, path, payload=None, auth=True):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["X-Phone-Key"] = key
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=15) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, json.loads(body) if body.startswith(("{", "[")) else body[:200]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body[:200]
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


bridge = None
for port in ports:
    code, body = bridge_request(port, "GET", "/v1/config")
    if code == 200 and isinstance(body, dict) and "anchors" in body:
        bridge = port
        break
print("candidate ports:", ports, "-> bridge:", bridge)
if bridge is None:
    raise SystemExit("bridge not found")

c = Client()
records = []


def log(label, code, body):
    records.append({"label": label, "status": code, "body": body})
    print(f"{label[:52]:54s} {str(code):>4}  {str(body)[:170]}")


def ev(label, method, path, payload=None, auth=True):
    code, body = bridge_request(bridge, method, path, payload, auth)
    log(label, code, body)
    return code, body


# C1 auth boundary
ev("C-01 unauthenticated config rejected", "GET", "/v1/config", auth=False)
ev("C-02 unauthenticated event rejected", "POST", "/v1/events",
   {"anchor_id": "x", "state": "online"}, auth=False)
ev("C-03 wrong key rejected", "POST", "/v1/events", {"anchor_id": "x", "state": "online"}, auth=True)
# force a wrong key by direct call
wrong = urllib.request.Request(f"http://127.0.0.1:{bridge}/v1/config", headers={"X-Phone-Key": "WRONG-KEY"})
try:
    with OPENER.open(wrong, timeout=5) as r:
        log("C-03b explicitly wrong key", r.status, r.read()[:100].decode())
except urllib.error.HTTPError as e:
    log("C-03b explicitly wrong key", e.code, e.read()[:100].decode())

ev("C-04 unknown state rejected", "POST", "/v1/events", {"anchor_id": "x", "state": "bogus"})
ev("C-05 event for unknown anchor", "POST", "/v1/events", {"anchor_id": "nosuchanchor", "state": "online"})
ev("C-06 malformed / oversized handled", "POST", "/v1/events", {"anchor_id": 123, "state": "online"})
ev("C-07 config after auth", "GET", "/v1/config")

# Build two synthetic anchors in the ISOLATED instance (no real platform calls).
anchors = {}
for label, url in [("alpha", "https://live.douyin.com/7000000000000000001"),
                   ("beta", "https://live.douyin.com/7000000000000000002")]:
    r = c.post("/api/live/anchors", {"name": label, "url": url})
    anchors[label] = r.json()["id"]
print("synthetic anchors:", anchors)

a = anchors["alpha"]
ev("C-10 alpha online event", "POST", "/v1/events", {"anchor_id": a, "state": "online", "message": "synthetic"})
r = c.get("/api/phone").json()
online_events = [e for e in r["events"] if e["anchor_id"] == a and e["state"] == "online"]
eid = online_events[0]["id"] if online_events else None
print("alpha event id:", eid)

ev("C-11 duplicate online within 300s", "POST", "/v1/events", {"anchor_id": a, "state": "online", "message": "synthetic-2"})
ev("C-12 beta online event", "POST", "/v1/events",
   {"anchor_id": anchors["beta"], "state": "online", "message": "synthetic"})
ev("C-13 dismiss alpha", "POST", "/api/phone/decision", {"event_id": eid, "target": "dismiss"}) if False else None
r = c.post("/api/phone/decision", {"event_id": eid, "target": "dismiss"})
log("C-13 dismiss alpha", r.status, r.json() if r.status < 500 else r.text[:200])
r = c.post("/api/phone/decision", {"event_id": eid, "target": "phone"})
log("C-14 repeat decision after dismiss (idempotency)", r.status, r.json() if r.status < 500 else r.text[:200])
r = c.post("/api/phone/decision", {"event_id": eid, "target": "bogus"})
log("C-15 invalid decision target", r.status, r.json() if r.status < 500 else r.text[:200])
r = c.post("/api/phone/decision", {"event_id": "missing", "target": "pc"})
log("C-16 decision unknown event", r.status, r.json() if r.status < 500 else r.text[:200])

ev("C-20 alpha offline event", "POST", "/v1/events", {"anchor_id": a, "state": "offline"})
r = c.get("/api/phone").json()
log("C-21 phone state after offline", 200, {"events": [(e["anchor_id"], e["state"], e["decision"]) for e in r["events"]],
                                            "connected": r["connected"], "devices": r["devices"]})
r = c.post("/api/phone/record", {"anchor_id": a, "action": "record"})
log("C-22 record command while companion not connected", r.status, r.json() if r.status < 500 else r.text[:200])
r = c.post("/api/phone/record", {"anchor_id": a, "action": "bogus"})
log("C-23 invalid record action", r.status, r.json() if r.status < 500 else r.text[:200])

out = ROOT / "repro/evidence/phone_bridge_probe.json"
out.write_text(json.dumps({"bridge_port": bridge, "instance": c.url,
                           "note": "synthetic anchors/events only; no adb reverse, no APK install, no real phone write",
                           "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", out)

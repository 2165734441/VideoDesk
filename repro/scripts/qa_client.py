"""Minimal HTTP client for the isolated QA-27 VideoDesk instance.

The service URL is ALWAYS read from the instance's own data/server.json
(never hardcoded). All requests bypass the ambient HTTP proxy because the
service is bound to 127.0.0.1.

Usage:
    from qa_client import Client
    c = Client()
    c.get('/api/state')
    c.post('/api/live/settings', {...})
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_JSON = ROOT / "repro" / "qa" / "home" / "data" / "server.json"

# Bypass any HTTP(S)_PROXY in the testing shell; this is a loopback-only call.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Response:
    def __init__(self, status: int, body: bytes, headers):
        self.status = status
        self.body = body
        self.headers = headers

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)

    def __repr__(self) -> str:
        return f"Response(status={self.status}, body={self.text[:300]!r})"


class Client:
    def __init__(self, server_json: Path = SERVER_JSON):
        info = json.loads(server_json.read_text(encoding="utf-8"))
        self.url = info["url"].rstrip("/")
        self.installation_id = info.get("installation_id", "")
        self.server_json = str(server_json)
        self.token = self.get("/api/state").json()["token"]

    def request(self, method: str, path: str, payload=None, headers=None, timeout=30) -> Response:
        data = None
        hdrs = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if method not in ("GET", "HEAD", "OPTIONS"):
            hdrs["x-app-token"] = self.token
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(self.url + path, data=data, headers=hdrs, method=method)
        try:
            with OPENER.open(req, timeout=timeout) as res:
                return Response(res.status, res.read(), dict(res.headers))
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.read(), dict(exc.headers))
        except urllib.error.URLError as exc:
            return Response(-1, str(exc).encode("utf-8"), {})

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload=None, **kw) -> Response:
        return self.request("POST", path, payload, **kw)

    def delete(self, path: str, **kw) -> Response:
        return self.request("DELETE", path, **kw)

    def health(self) -> bool:
        r = self.get("/api/state")
        return r.status == 200 and r.json().get("installation_id") == self.installation_id


if __name__ == "__main__":
    c = Client()
    print(json.dumps({"url": c.url, "installation_id": c.installation_id, "health": c.health()}, indent=2))

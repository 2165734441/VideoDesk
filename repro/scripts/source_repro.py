"""Deterministic source-level repros for QA-27 (run with the release-bundled Python).

These exercise the EXACT modules shipped inside VideoDesk-2.7.0-Windows-x64.exe
(verified byte-identical to E:/VideoDesk source by SHA256, see evidence/payload_match.json).

Run:
  PY=E:/VideoDesk/repro/qa/home/versions/2.7.0-e5df71c0b1/runtime/main/python.exe
  VIDEO_DESK_DATA=E:/VideoDesk/repro/qa/source-tests-data "$PY" repro/scripts/source_repro.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrations import following, live, live_browser  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "repro" / "evidence" / "source_repro.json"
results = {}


class Browser:
    """Minimal stand-in exposing the attributes observe() reads."""
    def __init__(self):
        self.live_following = {}
        self.live_following_profile = {}


def record(name, value):
    results[name] = value
    print(f"--- {name}\n{json.dumps(value, ensure_ascii=False, indent=2)[:1400]}")


# ---------------------------------------------------------------- A2 account switch
def account_switch_case():
    b = Browser()
    users_a = [{"url": "https://www.douyin.com/user/SEC_AAAAAAAA", "name": "alice"},
               {"url": "https://www.douyin.com/user/SEC_BBBBBBBB", "name": "bob"}]
    b.live_following_profile = {"sec_uid": "SEC_OWNER_AAAA", "uid": "111", "total": 2}
    following.observe(b, "https://www.douyin.com/aweme/v1/web/user/following/list/?sec_user_id=SEC_OWNER_AAAA&cursor=0",
                      {"data": {"followings": [], "has_more": True, "total": 2}}, lambda p: users_a)
    after_a = following.summary(b.live_following)

    users_b = [{"url": "https://www.douyin.com/user/SEC_CCCCCCCC", "name": "charlie"}]
    # A different signed-in account whose following responses do NOT carry an
    # owner query parameter (a common shape for the web following endpoint).
    b.live_following_profile = {"sec_uid": "SEC_OWNER_BBBB", "uid": "222", "total": 1}
    following.observe(b, "https://www.douyin.com/aweme/v1/web/user/following/list/?cursor=50&count=20",
                      {"data": {"followings": [], "has_more": False, "total": 1}}, lambda p: users_b)
    after_b = following.summary(b.live_following)
    record("A2_account_switch_without_owner_param", {
        "step1_account_a": {"users": after_a["users"], "loaded": after_a["loaded_count"]},
        "step2_account_b": {"users": after_b["users"], "loaded": after_b["loaded_count"],
                            "expected": after_b["expected_count"], "complete": after_b["complete"],
                            "message": after_b["message"]},
        "leaked_account_a_users": [u["name"] for u in after_b["users"] if u["name"] in ("alice", "bob")],
        "owner_after_switch": b.live_following.get("owner"),
    })


# ------------------------------------------------------- A3 completeness disclosure
def completeness_case():
    b = Browser()
    users = [{"url": f"https://www.douyin.com/user/SEC_{i:06d}", "name": f"user{i}"} for i in range(120)]
    following.observe(b, "https://www.douyin.com/aweme/v1/web/user/following/list/?sec_user_id=SEC_OWNER&cursor=0",
                      {"data": {"followings": [], "has_more": True, "total": 500}}, lambda p: users)
    s = following.summary(b.live_following)
    record("A3_partial_list_message", {
        "loaded_count": s["loaded_count"], "expected_count": s["expected_count"],
        "complete": s["complete"], "message": s["message"],
        "contains_explicit_unread_difference": "380" in s["message"] or "未读取" in s["message"],
    })


# --------------------------------------------- B2 unknown status claimed as online
def status_case():
    payload = {
        "data": {
            "stream_url": {
                "flv_pull_url": {"origin": "https://pull.example.invalid/live.flv"},
                "hls_pull_url_map": {},
            },
            "owner": {"nickname": "synthetic"},
            "id_str": "7381928374650910213",
            # NOTE: no 'status' field at all -> live status is UNKNOWN.
        }
    }
    out = live_browser.room_data(payload, "origin")
    record("B2_missing_status_claimed_online", {
        "input_has_status_field": "status" in payload["data"],
        "room_data": out,
        "claims_online": bool(out and out.get("online")),
    })
    # Control: explicit offline status is honoured.
    payload2 = json.loads(json.dumps(payload))
    payload2["data"]["status"] = 4
    out2 = live_browser.room_data(payload2, "origin")
    record("B2_control_explicit_offline", {"room_data": out2, "claims_online": bool(out2 and out2.get("online"))})


# ------------------------------------------------- A4/A7 canonical link identity
def normalize_case():
    rid = "7381928374650910213"
    forms = [
        f"https://live.douyin.com/{rid}",
        f"https://www.douyin.com/live/{rid}",
        f"https://webcast.amemv.com/douyin/webcast/reflow/{rid}/",
        f"http://webcast.amemv.com/douyin/webcast/reflow/{rid}",
        f"https://webcast.amemv.com/douyin/webcast/reflow/{rid}/?from=share",
        f"https://v.douyin.com/iQaSynth01/",
    ]
    normalized = []
    for u in forms:
        try:
            normalized.append({"input": u, "normalized": live_browser.normalize(u)})
        except Exception as exc:
            normalized.append({"input": u, "error": str(exc)})
    unique = {item.get("normalized") for item in normalized if "normalized" in item}
    record("A7_link_normalization", {
        "forms": normalized,
        "distinct_normalized_values": len(unique),
        "same_room_id_in_all_inputs": rid,
    })


# ------------------------------------------------------------ B4 cross-midnight
def window_case():
    ZONE = live.ZONE
    cases = [
        ("Mon 23:30 in Mon 23:00-01:00", datetime(2026, 11, 16, 23, 30, tzinfo=ZONE), True),
        ("Tue 00:30 after Mon 23:00-01:00", datetime(2026, 11, 17, 0, 30, tzinfo=ZONE), True),
        ("Mon 00:30 before Mon 23:00-01:00", datetime(2026, 11, 16, 0, 30, tzinfo=ZONE), False),
        ("Tue 12:00 outside", datetime(2026, 11, 17, 12, 0, tzinfo=ZONE), False),
    ]
    windows = [{"days": [0], "start": "23:00", "end": "01:00"}]
    rows = []
    for label, now, expected in cases:
        actual = live.in_window(windows, now)
        rows.append({"case": label, "expected": expected, "actual": actual, "match": actual == expected})
    record("B4_cross_midnight_window", {"rows": rows, "all_match": all(r["match"] for r in rows)})


# --------------------------------------------- A7 19-digit room id stays a string
def precision_case():
    rid = "7381928374650910213"
    out = live_browser.normalize(f"https://live.douyin.com/{rid}")
    record("A7_19_digit_room_id", {
        "input": rid, "normalized": out,
        "digits_preserved": rid in out,
        "js_number_would_be": float(rid),
    })


for fn in (account_switch_case, completeness_case, status_case, normalize_case, window_case, precision_case):
    try:
        fn()
    except Exception as exc:
        record(fn.__name__ + "_ERROR", {"type": type(exc).__name__, "message": str(exc)})

OUT.write_text(json.dumps({"python": sys.version, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", OUT)

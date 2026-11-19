"""Verify the QA-27 protection sentinels were not modified/deleted.

Usage:  python repro/scripts/verify_sentinels.py
Exit 0 = all sentinels intact; exit 1 = at least one changed/missing.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "repro" / "evidence" / "sentinels.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    data = json.loads(RECORD.read_text(encoding="utf-8"))
    bad = []
    for item in data["files"]:
        path = ROOT / item["file"]
        if not path.is_file():
            bad.append({"file": item["file"], "problem": "MISSING"})
            continue
        actual = sha256(path)
        if actual != item["sha256"]:
            bad.append(
                {
                    "file": item["file"],
                    "problem": "CHANGED",
                    "expected": item["sha256"],
                    "actual": actual,
                }
            )
    result = {"checked": len(data["files"]), "violations": bad, "ok": not bad}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())

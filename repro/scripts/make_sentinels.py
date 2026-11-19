"""Create protection sentinels for QA-27 isolated testing.

Sentinels are deliberately placed in the QA output/materials tree so that any
cleanup / cancel / retry path that touches them is detectable by hash drift.

Usage:  python repro/scripts/make_sentinels.py
Output: repro/evidence/sentinels.json (relative paths + SHA256 + byte size)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT / "repro" / "qa"
EVIDENCE = ROOT / "repro" / "evidence"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# name -> (relative dir, content, purpose)
FILES = {
    "sentinel_original.mp4": (
        "materials",
        b"QA27-SENTINEL-ORIGINAL-ORIGINAL-MEDIA-DO-NOT-DELETE\n" * 8,
        "original user media (must never be deleted)",
    ),
    "sentinel_user_added.txt": (
        "materials",
        b"QA27-SENTINEL-USER-ADDED-FILE-DO-NOT-DELETE\n" * 8,
        "file added by user after generation (must never be deleted)",
    ),
    "sentinel_shared.dat": (
        "materials",
        b"QA27-SENTINEL-SHARED-BY-OTHER-JOB-DO-NOT-DELETE\n" * 8,
        "file shared by more than one job (must never be deleted)",
    ),
    "sentinel_derived_copy.bin": (
        "output",
        b"QA27-SENTINEL-GENERATED-DERIVED-COPY-MAY-BE-RECYCLED\n" * 8,
        "generated derived copy (recyclable only if ownership is certain)",
    ),
    "sentinel_zero_byte.mp4": (
        "materials",
        b"",
        "zero-byte media edge case",
    ),
    "sentinel_broken.mp4": (
        "materials",
        b"QA27-NOT-A-REAL-MP4\x00\xff\xfe" * 4,
        "corrupted media edge case",
    ),
}


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    records = []
    for name, (subdir, content, purpose) in FILES.items():
        directory = QA / subdir
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(content)
        records.append(
            {
                "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
                "purpose": purpose,
            }
        )
    # A long path / Chinese+space+comma filename sentinel for F regressions.
    # A moderately long path / Chinese+space+comma filename sentinel for F regressions.
    # Kept under the classic MAX_PATH so the fixture itself is portable; the
    # extended-length (>260) probe is created separately with the \\?\ prefix.
    special = QA / "materials" / "QA27 中文,空格 与 长名字" / ("子目录 " * 3).strip()
    special.mkdir(parents=True, exist_ok=True)
    long_name = special / ("QA27_中文,空格_长文件名_" + ("x" * 40) + ".mp4")
    long_name.write_bytes(b"QA27-LONG-CHINESE-COMMA-SPACE-PATH\n" * 4)
    records.append(
        {
            "file": str(long_name.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(long_name),
            "bytes": long_name.stat().st_size,
            "purpose": "long / Chinese / space / comma path sentinel",
        }
    )
    payload = {
        "generated_by": "repro/scripts/make_sentinels.py",
        "note": "Re-run to recreate; verify_sentinel.py checks hashes are unchanged.",
        "files": records,
    }
    (EVIDENCE / "sentinels.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

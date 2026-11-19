"""Generate tiny deterministic synthetic media samples for QA-27.

Uses the FFmpeg bundled in the isolated self-test install (no system changes).
All content is synthetic / freely redistributable; no real user media is used.
Writes repro/evidence/samples.json with SHA256 of each generated sample.

Usage: python repro/scripts/make_media.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "repro" / "samples"
EVIDENCE = ROOT / "repro" / "evidence"
FF = next((ROOT / "repro/qa/home/versions").glob(
    "*/runtime/main/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg*.exe"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(args):
    p = subprocess.run([str(FF), "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(f"ffmpeg failed: {p.stderr[-400:]}")
    return p


def main():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    created = []

    def register(path: Path, description: str, params: str):
        created.append({"file": str(path.relative_to(ROOT)).replace("\\", "/"),
                        "sha256": sha256(path), "bytes": path.stat().st_size,
                        "description": description, "generation": params})

    # 6s colour bars + 440 Hz tone, 320x180, 25 fps, H.264 + AAC.
    audio_video = SAMPLES / "qa_av_6s.mp4"
    run(["-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=6",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "64k", "-shortest", str(audio_video)])
    register(audio_video, "6s synthetic video with audible 440 Hz tone",
             "ffmpeg -f lavfi -i testsrc=320x180:25:6 -f lavfi -i sine=440:44100:6 -c:v libx264 -c:a aac -shortest")

    # Same duration, video only (no audio track).
    silent = SAMPLES / "qa_silent_4s.mp4"
    run(["-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=4",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
         "-an", str(silent)])
    register(silent, "4s synthetic video with NO audio track",
             "ffmpeg -f lavfi -i testsrc=320x180:25:4 -c:v libx264 -an")

    # Very short clip (0.4s) for boundary testing.
    tiny = SAMPLES / "qa_tiny_04s.mp4"
    run(["-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=0.4",
         "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100:duration=0.4",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(tiny)])
    register(tiny, "0.4s very short clip",
             "ffmpeg -f lavfi -i testsrc=...:0.4 -f lavfi -i sine=330:0.4 -shortest")

    # Vertical (portrait) 180x320 clip.
    portrait = SAMPLES / "qa_portrait_3s.mp4"
    run(["-f", "lavfi", "-i", "testsrc=size=180x320:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=520:sample_rate=44100:duration=3",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(portrait)])
    register(portrait, "3s vertical/portrait clip",
             "ffmpeg -f lavfi -i testsrc=180x320:25:3 -f lavfi -i sine=520:3 -shortest")

    # Chinese + spaces + comma filename copy.
    odd = SAMPLES / "QA27 中文,空格 样本.mp4"
    odd.write_bytes(audio_video.read_bytes())
    register(odd, "byte-identical copy with Chinese/space/comma filename",
             "byte copy of qa_av_6s.mp4")

    # Zero-byte and corrupt media.
    zero = SAMPLES / "qa_zero_byte.mp4"
    zero.write_bytes(b"")
    register(zero, "zero-byte media", "write_bytes(b'')")
    broken = SAMPLES / "qa_broken.mp4"
    broken.write_bytes(b"QA27-NOT-A-MEDIA-FILE\x00\xff\xfe" * 8)
    register(broken, "corrupted media", "write_bytes(synthetic garbage)")

    payload = {"ffmpeg": str(FF.relative_to(ROOT)).replace("\\", "/"),
               "note": "All synthetic; safe to redistribute. Re-run to recreate.",
               "samples": created}
    (EVIDENCE / "samples.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

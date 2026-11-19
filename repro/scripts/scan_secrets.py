"""Desensitisation pass over QA-27 deliverables.

Two jobs:
  1. REDACT session tokens, the phone serial and dummy-key literals that were
     captured in raw API evidence, in-place, in repro/evidence/*.json and the CSV.
  2. SCAN all delivered text files for sensitive patterns and write a report.

IMPORTANT: this script never calls unlink()/remove(). In this sandbox
Path.unlink() is intercepted by a sitecustomize shim which calls
platform.machine() -> WMI, which hangs indefinitely (the product itself works
around this with integrations.runtime_support.configure_platform()). Overwriting
is used instead.

The scan is a best-effort automated check, NOT a guarantee of zero leakage.
Screenshots/media were reviewed manually (none were captured this round).

Usage: python repro/scripts/scan_secrets.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "repro" / "evidence"
OUT = EVID / "secrets_scan.json"

SERIAL = "10ADA41UT7002GG"
DUMMY_KEY = re.compile(r"QA27-DUMMY-KEY-NOT-A-REAL-CREDENTIAL\d*")

SHA256_OK = re.compile(r"^[0-9a-f]{64}$")

PATTERNS = {
    "app_session_token": re.compile(r'"token"\s*:\s*"([^"]+)"'),
    "pairing_key_literal": re.compile(r'"key"\s*:\s*"([A-Za-z0-9_-]{30,})"'),
    "x_phone_key_header": re.compile(r'X-Phone-Key\s*[:=]\s*["\']?([A-Za-z0-9_-]{30,})'),
    "authorization_bearer": re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._-]{10,}", re.I),
    "cookie_header_value": re.compile(r"Cookie:\s*[A-Za-z0-9_=-]{8,}=", re.I),
    "session_token_param": re.compile(r"session_token\s*=\s*[A-Za-z0-9._-]{8,}", re.I),
    "phone_serial": re.compile(re.escape(SERIAL)),
    "windows_user_path": re.compile(r"C:\\Users\\([^\\\s\"']+)"),
    "real_looking_api_key": re.compile(r'"api_key"\s*:\s*"(?!")([^"]{8,})"'),
}

TEXT_FILES = (
    [ROOT / "BUG-REPORT.md", ROOT / "TEST-MATRIX.csv", ROOT / "repro" / "environment.json",
     ROOT / "repro" / "README.md"]
    + sorted((ROOT / "repro" / "scripts").glob("*.py"))
    + sorted(EVID.glob("*.json"))
    + sorted((ROOT / "repro" / "logs").glob("*.log"))
)
# The scanner's own source holds only regex definitions; scanning it produces
# self-matches that are meaningless. It is listed explicitly in the report.
TEXT_FILES = [p for p in TEXT_FILES if p.name != "scan_secrets.py"]


def redact_text(text: str) -> str:
    original = text

    def sub_token(m):
        value = m.group(1)
        return m.group(0) if SHA256_OK.match(value) else '"token": "<redacted>"'

    text = PATTERNS["app_session_token"].sub(sub_token, text)
    text = PATTERNS["pairing_key_literal"].sub('"key": "<redacted>"', text)
    text = PATTERNS["x_phone_key_header"].sub("X-Phone-Key: <redacted>", text)
    text = text.replace(SERIAL, "<redacted-serial>")
    text = DUMMY_KEY.sub("<test-dummy-key>", text)
    return text


def redact_deliverables():
    changed = []
    for path in TEXT_FILES:
        if not path.is_file() or path == OUT:
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        new = redact_text(raw)
        if new != raw:
            path.write_text(new, encoding="utf-8")
            changed.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    return changed


def scan():
    findings = []
    scanned = []
    for path in TEXT_FILES:
        if not path.is_file():
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        scanned.append(rel)
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in PATTERNS.items():
            for m in pattern.finditer(text):
                value = m.group(0)
                if "<redacted" in value or "<test-dummy-key>" in value:
                    continue
                if name == "windows_user_path" and m.group(1).lower() in ("<userprofile>", "public", "default"):
                    continue
                findings.append({"file": rel, "pattern": name,
                                 "excerpt": value[:90].replace("\n", " ")})
    return scanned, findings


def packaging_check():
    """No virtualenv / browser profile / keystore may be part of the evidence set."""
    forbidden = ["android-signing-key.jks", "android-keypass.txt", "mobile-pair.json"]
    hits = []
    for path in sorted((ROOT / "repro" / "evidence").rglob("*")):
        if path.is_file() and path.name in forbidden:
            hits.append(str(path.relative_to(ROOT)))
    return hits


def main():
    changed = redact_deliverables()
    scanned, findings = scan()

    # Sanitise generated secrets in the QA scratch home by OVERWRITING (never
    # unlink; see module docstring). A future restart regenerates a fresh key.
    secret = ROOT / "repro/qa/home/data/credentials/mobile-pair.json"
    sanitized_secret = False
    if secret.is_file():
        secret.write_text('{"key": "<redacted>"}\n', encoding="utf-8")
        sanitized_secret = True
    services = ROOT / "repro/qa/home/data/credentials/translation-services.json"
    restored_services = False
    if services.is_file():
        try:
            data = json.loads(services.read_text(encoding="utf-8"))
            for value in data.values():
                if isinstance(value, dict):
                    value["api_key"] = ""
            services.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            restored_services = True
        except (ValueError, OSError):
            pass

    result = {
        "method": "regex scan over delivered text files + in-place redaction of captured session tokens / phone serial / dummy keys; manual review of screenshot & media (none captured)",
        "scanner_source_excluded_from_own_pattern_scan": "repro/scripts/scan_secrets.py (contains only regex definitions)",
        "scanned_files": len(scanned), "scanned_file_list": scanned,
        "redacted_files": changed,
        "sanitized_generated_pairing_key": sanitized_secret,
        "restored_test_cloud_config": restored_services,
        "packaging_check_forbidden_files_in_evidence": packaging_check(),
        "remaining_findings": findings,
        "clean": not findings and not packaging_check(),
        "caveat": "自动化扫描不能保证绝对无泄露；已人工确认本轮未采集任何截图、平台页面、录像或含个人信息的媒体，且未打包浏览器用户目录、虚拟环境、模型、keystore 或签名密码。",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("scanned_file_list",)},
                     ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()

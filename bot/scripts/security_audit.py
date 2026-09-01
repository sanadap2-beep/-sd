#!/usr/bin/env python3
"""فحص أسرار سريع قبل بيع/تسليم نسخة البوت.

يفحص الملفات النصية بحثاً عن مفاتيح أو توكنات محتملة، ويتجاهل ملفات Git
والبيئات المحلية وقواعد البيانات والـ venv.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".zip"}
SKIP_FILES = {".env", "local.env", "01a035c5-50e1-7e16-b581-a4b063e5c6be (2).patch"}
PATTERNS = [
    ("Telegram Bot Token", re.compile(r"\b\d{5,15}:[A-Za-z0-9_-]{25,}\b")),
    # Literal committed secret values only. Runtime reads like settings.API_KEY are OK.
    ("API/Secret literal", re.compile(r"(?i)(api[_-]?key|secret[_-]?key|bot[_-]?token|password)\s*=\s*['\"][A-Za-z0-9_\-:.]{20,}['\"]")),
    ("Private key", re.compile(r"-----BEGIN (RSA |OPENSSH |EC |)PRIVATE KEY-----")),
]


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def main() -> int:
    findings = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for name, pattern in PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                value = match.group(0)
                # تجاهل أمثلة الاختبار الواضحة.
                if "TEST_TOKEN" in value or "change_me" in value or "example" in str(path):
                    continue
                findings.append((str(path.relative_to(ROOT)), line, name, value[:80]))
    if findings:
        print("⚠️ Findings:")
        for file, line, name, sample in findings:
            print(f"{file}:{line}: {name}: {sample}")
        return 1
    print("✅ No obvious secrets found in tracked source files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

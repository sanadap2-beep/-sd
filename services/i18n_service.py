"""Lightweight file-based i18n with Arabic fallback."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"


class I18nService:
    @staticmethod
    def normalize_language(language_code: str | None) -> str:
        return "en" if (language_code or "ar").lower().startswith("en") else "ar"

    @staticmethod
    @lru_cache(maxsize=4)
    def _load(language: str) -> dict[str, str]:
        path = _LOCALES_DIR / f"{I18nService.normalize_language(language)}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def t(key: str, language: str | None = None, **values) -> str:
        language = I18nService.normalize_language(language)
        text = I18nService._load(language).get(key)
        if text is None:
            text = I18nService._load("ar").get(key, key)
        try:
            return text.format(**values)
        except (KeyError, ValueError):
            return text

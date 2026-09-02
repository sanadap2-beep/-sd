"""Telegram Mini App initData authentication."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from config import settings


class TelegramAuthError(HTTPException):
    def __init__(self, detail: str = "Telegram authentication failed"):
        super().__init__(status_code=401, detail=detail)


def validate_init_data(init_data: str) -> dict:
    """Validate Telegram WebApp initData and return its user object."""
    if not init_data or len(init_data) > 4096:
        raise TelegramAuthError()
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    auth_date = pairs.get("auth_date")
    if not received_hash or not auth_date or not settings.BOT_TOKEN:
        raise TelegramAuthError()
    try:
        if abs(int(time.time()) - int(auth_date)) > 86_400:
            raise TelegramAuthError("Telegram session expired")
    except ValueError as exc:
        raise TelegramAuthError() from exc

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret = hmac.new(
        b"WebAppData",
        settings.BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise TelegramAuthError()
    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise TelegramAuthError() from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise TelegramAuthError()
    return user


async def current_telegram_user(
    authorization: str | None = Header(default=None),
) -> dict:
    if not authorization or not authorization.startswith("tma "):
        raise TelegramAuthError("Use Authorization: tma <initData>")
    return validate_init_data(authorization[4:].strip())

"""Resolve the live Telegram bot username for deep links.

Referral links of the form ``https://t.me/{BOT_USERNAME}?start=ref_...`` fail
with Telegram's «لم يتم العثور على اسم المستخدم» when ``BOT_USERNAME`` in the
environment is empty, still has a leading ``@``, or was pasted as a full
``t.me`` URL. The live ``getMe`` username is the source of truth; the env
value is only a fallback after it has been normalised.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from config import settings

logger = logging.getLogger(__name__)

_TME_PREFIXES = (
    "https://t.me/",
    "http://t.me/",
    "https://telegram.me/",
    "http://telegram.me/",
    "t.me/",
    "telegram.me/",
)

# Telegram bot usernames: 5–32 chars, Latin letters / digits / underscore.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

_cached_username: str | None = None


def normalize_bot_username(raw: str | None) -> str:
    """Strip @, t.me prefixes, query strings and whitespace."""
    value = (raw or "").strip()
    if not value:
        return ""
    value = value.lstrip("@")
    lowered = value.lower()
    for prefix in _TME_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.split("/")[0].split("?")[0].split("#")[0].strip().lstrip("@")
    if not _USERNAME_RE.match(value):
        return ""
    return value


async def resolve_bot_username(bot=None) -> str:
    """Return the bot's @-less username, preferring Telegram's getMe result.

    If a previous call cached the environment fallback, a later call that has a
    live ``bot`` object must still query ``get_me()``.  This prevents stale or
    malformed ``BOT_USERNAME`` values from leaking into public channel links.
    """
    global _cached_username

    if bot is not None:
        try:
            me = await bot.get_me()
            username = normalize_bot_username(getattr(me, "username", None))
            if username:
                _cached_username = username
                return username
        except Exception as exc:  # noqa: BLE001 - env fallback must still work
            logger.warning("تعذّر جلب يوزرنيم البوت من Telegram: %s", exc)
            if _cached_username:
                return _cached_username

    if _cached_username:
        return _cached_username

    username = normalize_bot_username(getattr(settings, "BOT_USERNAME", None))
    if username:
        _cached_username = username
    return username


def referral_start_link(username: str, telegram_id: int) -> str:
    """Build a deep link that Telegram actually opens."""
    clean = normalize_bot_username(username)
    if not clean:
        return ""
    return f"https://t.me/{clean}?start=ref_{int(telegram_id)}"


def tg_ready_start_link(username: str, country_key: str | None = None) -> str:
    """رابط شراء جلسة تلجرام جاهزة من القناة العامة.

    ``start=tgready`` يفتح القائمة، و``start=tgr_{country}`` يفتح دولة بعينها.
    حمولة ``start`` محدودة بـ 64 بايت.
    """
    clean = normalize_bot_username(username)
    if not clean:
        return ""
    key = re.sub(r"[^A-Za-z0-9_]", "", str(country_key or ""))
    payload = f"tgr_{key}" if key else "tgready"
    if len(payload.encode("utf-8")) > 64:
        payload = "tgready"
    return f"https://t.me/{clean}?start={payload}"


def number_buy_start_link(username: str, service_code: str, country_id: int) -> str:
    """Build a buy deep link for the live availability channel.

    Telegram's ``start`` payload is limited to 64 bytes, so the country
    travels as its integer id (codes can exceed the limit). Old links
    carrying codes still resolve via fallback.
    """
    clean = normalize_bot_username(username)
    if not clean:
        return ""
    service = re.sub(r"[^A-Za-z0-9_\-]", "_", str(service_code or "")).strip("_")
    try:
        country = str(int(country_id))
    except (TypeError, ValueError):
        return ""
    if not service or not country:
        return ""
    return f"https://t.me/{clean}?start=buy_{service}__{country}"


def referral_share_url(link: str, share_text: str = "") -> str:
    """Telegram share-to-chat URL for the referral link."""
    if not link:
        return ""
    encoded_link = quote(link, safe="")
    encoded_text = quote(share_text, safe="") if share_text else ""
    if encoded_text:
        return f"https://t.me/share/url?url={encoded_link}&text={encoded_text}"
    return f"https://t.me/share/url?url={encoded_link}"


def reset_bot_username_cache() -> None:
    """Test helper."""
    global _cached_username
    _cached_username = None

"""
عميل «جسر واتساب» — يبني القسم الأول (هذا البوت) مع البوت الثاني
الذي يدير جلسات واتساب المستخدمين.

الجسر: خدمة HTTP صغيرة تشغلها **بجانب البوت الثاني** (ملف
wa_bridge/bridge.py) وتفتح 4 نقاط:

    POST /link/start   {phone}                 → {link_code, instructions}
    GET  /link/status                        → {state: pending|linked|expired, ...}
    GET  /menu                               → {status_text, menu:[{id,label,data?}]}
    POST /action       {action, payload}      → {text, menu?}

كل طلب يحمل:
    Authorization: Bearer <WA_BRIDGE_SECRET>
    X-TG-User-Id: <telegram user id>

مصدر الإعداد (الأولوية):
1) لوحة الأدمن: wa_bridge_url / wa_bridge_secret (settings).
2) ملف البيئة: WA_BRIDGE_URL / WA_BRIDGE_SECRET.

عند أي فشل يرفع WaBridgeError — لا يُخصم رصيد المستخدم على طلب
فشل، والواجهة تعرض خطأً مع زر تحديث.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from config import settings
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 45


class WaBridgeError(Exception):
    """فشل الجسر (غير مضبوط / شبكة / كود خطأ / رد غير صالح)."""


async def _bridge_config() -> tuple[str, str]:
    url = (
        (await SettingsService.get("wa_bridge_url") or "").strip()
        or settings.WA_BRIDGE_URL
    )
    secret = (
        (await SettingsService.get("wa_bridge_secret") or "").strip()
        or settings.WA_BRIDGE_SECRET
    )
    return url.rstrip("/"), secret


async def configured() -> bool:
    url, secret = await _bridge_config()
    return bool(url and secret)


async def _request(
    method: str, path: str, tg_user_id: int, json_body: dict | None = None
) -> dict:
    base_url, secret = await _bridge_config()
    if not base_url or not secret:
        raise WaBridgeError(
            "لم يُضبط الجسر بعد. من لوحة الأدمن (📱 إدارة واتساب ← 🔌 الجسر) "
            "أو في ملف البيئة: WA_BRIDGE_URL و WA_BRIDGE_SECRET."
        )
    headers = {
        "Authorization": f"Bearer {secret}",
        "X-TG-User-Id": str(tg_user_id),
        "Content-Type": "application/json",
    }
    url = f"{base_url}{path}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
        ) as http:
            async with http.request(
                method, url, json=json_body, headers=headers
            ) as response:
                if response.status == 401:
                    raise WaBridgeError("الجسر رفض المفتاح (WA_BRIDGE_SECRET غير مطابق).")
                if response.status != 200:
                    body = (await response.text())[:200]
                    logger.warning("جسر واتساب أرجع %s: %s", response.status, body)
                    raise WaBridgeError(f"الجسر أرجع كود {response.status}.")
                data = await response.json()
    except WaBridgeError:
        raise
    except (aiohttp.ClientError, ValueError) as exc:
        raise WaBridgeError(f"تعذّر الوصول إلى جسر واتساب: {exc}") from exc

    if not isinstance(data, dict):
        raise WaBridgeError("رد الجسر غير صالح.")
    return data


# ══════════════ نقاط الجسر الأربع ══════════════


async def start_link(tg_user_id: int, phone: str) -> dict[str, Any]:
    """يفتح جلسة واتساب بالرقم ويرجع كود الربط.

    يرجع: {link_code: str, instructions?: str}
    """
    data = await _request("POST", "/link/start", tg_user_id, {"phone": phone})
    link_code = data.get("link_code")
    if not link_code:
        raise WaBridgeError("الجسر لم يرجع كود ربط.")
    return {"link_code": str(link_code), "instructions": data.get("instructions") or ""}


async def link_status(tg_user_id: int) -> dict[str, Any]:
    """حالة الربط: {state: pending|linked|expired, connected_since?: str}"""
    data = await _request("GET", "/link/status", tg_user_id)
    state = data.get("state")
    if state not in ("pending", "linked", "expired", "none"):
        raise WaBridgeError("حالة الربط من الجسر غير معروفة.")
    return data


async def menu(tg_user_id: int) -> dict[str, Any]:
    """قائمة الأزرار الحالية: {status_text?: str, menu: [{id,label,data?}]}"""
    data = await _request("GET", "/menu", tg_user_id)
    items = data.get("menu")
    if not isinstance(items, list):
        raise WaBridgeError("قائمة الأزرار من الجسر غير صالحة.")
    return {"status_text": data.get("status_text") or "", "menu": items}


async def action(
    tg_user_id: int, action_id: str, payload: dict | None = None
) -> dict[str, Any]:
    """ينفذ أمر البوت الثاني ويرجع: {text, menu?}"""
    data = await _request(
        "POST", "/action", tg_user_id, {"action": action_id, "payload": payload or {}}
    )
    return {"text": data.get("text") or "", "menu": data.get("menu")}


async def ping() -> bool:
    """اختبار اتصال من لوحة الأدمن."""
    try:
        data = await _request("GET", "/ping", 0)
        return bool(data.get("ok", True))
    except WaBridgeError as exc:
        if "WA_BRIDGE" in str(exc):
            raise
        logger.warning("فشل ping الجسر: %s", exc)
        return False

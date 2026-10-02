"""
عميل «جسر واتساب» — يصل هذا البوت (SD) بالبوت الثاني الذي يدير جلسات واتساب
المستخدمين.

الجسر: خدمة HTTP صغيرة تُشغَّل **بجانب البوت الثاني** (``wa_bridge/bridge.py``
في هذا المستودع — وقابل للنسخ لمشروع البوت الثاني) وتفتح النقاط التالية
(الإصدار 2 من العقد؛ كل الحقول الاختيارية متجاهَلة عند جسر قديم):

    GET  /ping           → {ok, version, adapter, capabilities}
    POST /link/start     {phone}                → {link_code, instructions}
    GET  /link/status    → {state: none|pending|linked|expired, connected_since}
    POST /link/unlink    → {ok}
    GET  /menu?page=N    → {status_text, menu:[item], page, pages, awaiting_input}
    POST /action         {action, payload}      → {text, files, buttons, menu,
                                                   page, pages, awaiting_input, alert}
    POST /input          {text, payload}        → نفس شكل /action

حيث item = {id, label, data?, kind?: action|input|url|back|refresh, prompt?, url?}.

كل طلب يحمل:
    Authorization: Bearer <WA_BRIDGE_SECRET>
    X-TG-User-Id: <telegram user id>

مصدر الإعداد (الأولوية):
1) لوحة الأدمن: wa_bridge_url / wa_bridge_secret (جدول settings).
2) ملف البيئة: WA_BRIDGE_URL / WA_BRIDGE_SECRET.

عند أي فشل يرفع WaBridgeError — لا يُخصم رصيد المستخدم على طلب فشل، والواجهة
تعرض الخطأ مع زر تحديث. كما يُحسب عدد الإخفاقات المتتالية
(``consecutive_failures()``) لتستعمله مراقبة الأدمن في bot.py.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from config import settings
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 45
# عدد الإخفاقات المتتالية الذي يستحق تنبيه الأدمن (يراجعه wa_bridge_health_cycle).
_ALERT_AFTER_FAILURES = 3

consecutive_failures_count = 0
last_error: str = ""


class WaBridgeError(Exception):
    """فشل الجسر (غير مضبوط / شبكة / سر غير مطابق / رد غير صالح).

    ``status`` = كود HTTP عند وجوده (تستعمله unlink لتجاهل الجسر القديم)،
    و``not_configured`` تميّز حالة «لم يُضبط الجسر» حتى لا تُبتلع في ping.
    """

    def __init__(
        self, message: str, status: int | None = None, not_configured: bool = False
    ):
        super().__init__(message)
        self.status = status
        self.not_configured = not_configured


def _mark_failure(message: str) -> None:
    global consecutive_failures_count, last_error
    consecutive_failures_count += 1
    last_error = message


def _mark_success() -> None:
    global consecutive_failures_count, last_error
    if consecutive_failures_count:
        logger.info("جسر واتساب استجاب بعد %s محاولة فاشلة.", consecutive_failures_count)
    consecutive_failures_count = 0
    last_error = ""


def consecutive_failures() -> int:
    return consecutive_failures_count


def last_bridge_error() -> str:
    return last_error


def reset_failure_counter() -> None:
    _mark_success()


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


async def get_config() -> dict[str, str]:
    """العنوان والسر الفعليان (مقنّعان) — لعرضهما في لوحة الأدمن."""
    url, secret = await _bridge_config()
    return {"url": url, "secret_set": bool(secret)}


async def configured() -> bool:
    url, secret = await _bridge_config()
    return bool(url and secret)


async def _request(
    method: str,
    path: str,
    tg_user_id: int,
    json_body: dict | None = None,
    params: dict | None = None,
    timeout: int = _TIMEOUT_SECONDS,
) -> dict:
    base_url, secret = await _bridge_config()
    if not base_url or not secret:
        raise WaBridgeError(
            "لم يُضبط الجسر بعد. من لوحة الأدمن (📱 إدارة واتساب ← 🔌 الجسر) "
            "أو في ملف البيئة: WA_BRIDGE_URL و WA_BRIDGE_SECRET.",
            not_configured=True,
        )
    headers = {
        "Authorization": f"Bearer {secret}",
        "X-TG-User-Id": str(tg_user_id),
        "Content-Type": "application/json",
    }
    url = f"{base_url}{path}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as http:
            async with http.request(
                method, url, json=json_body, headers=headers, params=params
            ) as response:
                text = await response.text()
                if response.status == 401:
                    _mark_failure("الجسر رفض المفتاح")
                    raise WaBridgeError(
                        "الجسر رفض المفتاح (سر الجسر غير مطابق لسر بوت SD).",
                        status=401,
                    )
                if response.status >= 400:
                    detail = _extract_detail(text) or f"كود {response.status}"
                    _mark_failure(f"{path}: {detail}")
                    raise WaBridgeError(
                        f"جسر واتساب: {detail}", status=response.status
                    )
                data = _loads(text)
    except WaBridgeError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        _mark_failure(f"تعذّر الوصول: {exc}")
        raise WaBridgeError(f"تعذّر الوصول إلى جسر واتساب ({exc.__class__.__name__}).") from exc
    except ValueError as exc:
        _mark_failure(f"رد غير صالح: {exc}")
        raise WaBridgeError("جسر واتساب أرجع رداً غير صالح (ليس JSON).") from exc

    if not isinstance(data, dict):
        _mark_failure("رد غير صالح")
        raise WaBridgeError("رد الجسر غير صالح.")
    _mark_success()
    return data


def _loads(text: str) -> Any:
    return json.loads(text)


def _extract_detail(text: str) -> str:
    """يستخرج رسالة الجسر (FastAPI تضعها في detail) لعرضها للمستخدم."""
    try:
        payload = _loads(text)
    except (ValueError, TypeError):
        return (text or "").strip()[:200]
    if isinstance(payload, dict):
        for key in ("detail", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
    return ""


# ══════════════ نقاط الجسر ══════════════


async def start_link(tg_user_id: int, phone: str) -> dict[str, Any]:
    """يفتح جلسة واتساب بالرقم ويرجع كود الربط.

    يرجع: {link_code: str, instructions: str}
    """
    data = await _request("POST", "/link/start", tg_user_id, {"phone": phone})
    link_code = data.get("link_code")
    if not link_code:
        _mark_failure("الجسر لم يرجع link_code")
        raise WaBridgeError("الجسر لم يرجع كود ربط.")
    return {"link_code": str(link_code), "instructions": data.get("instructions") or ""}


async def link_status(tg_user_id: int) -> dict[str, Any]:
    """حالة الربط: {state: none|pending|linked|expired, connected_since?: str}.

    ``expired`` عند الجسر = الجلسة ماتت عنده ولا يمكن استرجاعها إلا بإعادة ربط.
    """
    data = await _request("GET", "/link/status", tg_user_id)
    state = data.get("state")
    if state not in ("pending", "linked", "expired", "none"):
        raise WaBridgeError("حالة الربط من الجسر غير معروفة.")
    return data


async def unlink(tg_user_id: int) -> bool:
    """يطلب إنهاء الجلسة عند البوت الثاني (نقطة اختيارية في العقد).

    الجسر القديم (بلا ``/link/unlink``) يرجع 404/405 — تُعتبر نجاحاً صامتاً
    لأن بوت SD يلغي الربط محلياً على أي حال.
    """
    try:
        data = await _request("POST", "/link/unlink", tg_user_id, {})
    except WaBridgeError as exc:
        if exc.status in (404, 405, 501):
            return False
        raise
    _mark_success()
    return bool(data.get("ok"))


async def menu(tg_user_id: int, page: int = 0) -> dict[str, Any]:
    """قائمة أزرار البوت الثانية الحالية.

    {status_text, menu: [{id,label,data?,kind?,prompt?,url?}], page, pages,
     awaiting_input}
    """
    data = await _request("GET", "/menu", tg_user_id, params={"page": max(0, int(page))})
    items = data.get("menu")
    if not isinstance(items, list):
        raise WaBridgeError("قائمة الأزرار من الجسر غير صالحة.")
    return {
        "status_text": str(data.get("status_text") or "")[:1000],
        "menu": [i for i in items if isinstance(i, dict) and (i.get("id") or i.get("label"))],
        "page": int(data.get("page") or 0),
        "pages": max(1, int(data.get("pages") or 1)),
        "awaiting_input": data.get("awaiting_input")
        if isinstance(data.get("awaiting_input"), dict)
        else None,
    }


def parse_result(data: dict) -> dict[str, Any]:
    """يطبّع رد ``/action`` و``/input`` (يتجاهل الحقول التي لا يعرفها جسر قديم)."""
    result: dict[str, Any] = {
        "text": str(data.get("text") or ""),
        "alert": bool(data.get("alert")),
        "files": [],
        "buttons": [],
        "menu": None,
        "page": int(data.get("page") or 0),
        "pages": max(1, int(data.get("pages") or 1)),
        "awaiting_input": None,
    }
    for raw in (data.get("files") or [])[:5]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "file")[:64]
        if raw.get("url"):
            result["files"].append({"name": name, "url": str(raw["url"])[:600]})
        elif raw.get("content_b64"):
            result["files"].append(
                {"name": name, "content_b64": str(raw["content_b64"])[:4_000_000]}
            )
    for raw in (data.get("buttons") or [])[:3]:
        if isinstance(raw, dict) and raw.get("text") and str(raw.get("url", "")).startswith("https://"):
            result["buttons"].append({"text": str(raw["text"])[:32], "url": str(raw["url"])[:500]})
    if isinstance(data.get("menu"), list):
        result["menu"] = [
            i
            for i in data["menu"]
            if isinstance(i, dict) and (i.get("id") or i.get("label"))
        ]
    awaiting = data.get("awaiting_input")
    if isinstance(awaiting, dict) and (awaiting.get("prompt") or awaiting.get("action")):
        result["awaiting_input"] = {
            "prompt": str(awaiting.get("prompt") or "اكتب الرد")[:300],
            "action": str(awaiting.get("action") or "")[:190],
        }
    return result


async def action(
    tg_user_id: int, action_id: str, payload: dict | None = None
) -> dict[str, Any]:
    """ينفذ أمر البوت الثاني ويرجع الرد مطبّعاً (انظر parse_result)."""
    data = await _request(
        "POST", "/action", tg_user_id, {"action": action_id, "payload": payload or {}}
    )
    return parse_result(data)


async def send_input(
    tg_user_id: int, text: str, payload: dict | None = None
) -> dict[str, Any]:
    """يرسل نصاً كتبه المستخدم رداً على زر ``kind:"input"`` عند البوت الثاني."""
    data = await _request(
        "POST", "/input", tg_user_id, {"text": text[:4000], "payload": payload or {}}
    )
    return parse_result(data)


async def ping() -> bool:
    """اختبار اتصال سريع من لوحة الأدمن (نقاط /ping لا تحتاج X-TG-User-Id حقيقياً)."""
    try:
        data = await _request("GET", "/ping", 0)
    except WaBridgeError as exc:
        if exc.not_configured:
            raise
        logger.warning("فشل ping الجسر: %s", exc)
        return False
    return bool(data.get("ok", True))


async def probe() -> dict[str, Any]:
    """ ping موسّع للوحة الأدمن: {ok, latency_ms, version, adapter, capabilities}."""
    import time

    started = time.monotonic()
    try:
        data = await _request("GET", "/ping", 0, timeout=15)
    except WaBridgeError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    return {
        "ok": bool(data.get("ok", True)),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "version": data.get("version"),
        "adapter": data.get("adapter"),
        "capabilities": data.get("capabilities") or [],
    }

"""جسر واتساب — الخدمة الصغيرة التي تعمل **بجانب البوت الثاني**.

البوت الأول (SD) لا يستطيع استقبال ضغطات على أزرار بوت آخر، لذلك يقرأ منه هذا
الجسر قائمة الأزرار الحقيقية وينفّذ أوامره عبر HTTP. كل المنطق يبقى عند البوت
الثاني: أنت تكتب ``BridgeAdapter`` واحداً (`wa_bridge/adapter.py`) يقرأ قوائمك
ال الحالية وينفّذ دوالك، والجسر يحوّلها إلى نقاط REST.

التشغيل::

    export BRIDGE_SECRET="$(openssl rand -hex 32)"
    export WA_BRIDGE_ADAPTER="mybot.wa_adapter:MyWaAdapter"
    uvicorn wa_bridge.bridge:app --host 127.0.0.1 --port 8090

وانتبه: الجسر يثق بالـ ``X-TG-User-Id``، فمن يسرق السر يستطيع التصرف باسم أي
مستخدم. لا تفتحه على الإنترنت المكشوف (loopback/شبكة داخلية فقط، أو TLS +
IP allowlist).

النقاط (عقد متوافق مع ``services/wa_bridge_client.py`` في بوت SD)::

    GET  /ping            → {ok, adapter, capabilities, version}
    POST /link/start      {phone}          → {link_code, instructions}
    GET  /link/status                      → {state, connected_since}
    POST /link/unlink                        → {ok}
    GET  /menu?page=N                      → {status_text, menu, page, pages, awaiting_input}
    POST /action        {action, payload}  → {text, files, buttons, menu, page, pages, awaiting_input, alert}
    POST /input         {text, payload}    → نفس شكل /action
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request

from wa_bridge.adapter import BridgeAdapter, BridgeError, load_adapter

logger = logging.getLogger("wa_bridge")
VERSION = "2.0"

VALID_KINDS = {"action", "input", "url", "back", "refresh"}
VALID_STATES = {"none", "pending", "linked", "expired"}
_LABEL_MAX = 48
_ID_MAX = 190  # callback_data عند بوت SD = "wa:act:<token>:<idx>" — المعرّف يُمرَّر للجسر لا لتليجرام


def _clean_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    action_id = str(raw.get("id") or "").strip()
    if not action_id:
        return None
    kind = str(raw.get("kind") or "action").strip().lower()
    if kind not in VALID_KINDS:
        kind = "action"
    item: dict[str, Any] = {
        "id": action_id[:_ID_MAX],
        "label": str(raw.get("label") or action_id)[:_LABEL_MAX],
        "kind": kind,
    }
    if isinstance(raw.get("data"), dict):
        item["data"] = raw["data"]
    if raw.get("prompt"):
        item["prompt"] = str(raw["prompt"])[:300]
    if raw.get("url"):
        url = str(raw["url"])
        if not url.lower().startswith(("https://", "tg://")):
            logger.warning("تجاهل زر برابط غير https: %s", url[:80])
        else:
            item["url"] = url[:500]
            item["kind"] = "url"
    if raw.get("disabled"):
        item["disabled"] = True
    return item


def _clean_result(data: Any) -> dict[str, Any]:
    """تطبيع رد الـ adapter حتى لا ينفجر جانب بوت SD على شكل غير متوقع."""
    if isinstance(data, str):
        return {"text": data}
    if not isinstance(data, dict):
        return {"text": ""}
    out: dict[str, Any] = {"text": str(data.get("text") or "")[:3900]}
    if data.get("alert"):
        out["alert"] = True
    files = []
    for raw in data.get("files") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "file.txt")
        name = re.sub(r'[\\/:*?"<>|]', "_", name)[:64]
        entry: dict[str, Any] = {"name": name}
        if raw.get("url"):
            entry["url"] = str(raw["url"])[:600]
        elif raw.get("content_b64"):
            if len(str(raw["content_b64"])) > 4_000_000:
                logger.warning("ملف أكبر من 3MB تم تجاهله: %s", name)
                continue
            entry["content_b64"] = str(raw["content_b64"])
        else:
            continue
        files.append(entry)
    if files:
        out["files"] = files[:5]
    buttons = [
        {"text": str(b.get("text"))[:32], "url": str(b["url"])[:500]}
        for b in (data.get("buttons") or [])
        if isinstance(b, dict)
        and b.get("text")
        and str(b.get("url", "")).startswith("https://")
    ]
    if buttons:
        out["buttons"] = buttons[:3]
    if isinstance(data.get("menu"), list):
        items = [i for i in (_clean_item(raw) for raw in data["menu"]) if i]
        out["menu"] = items
        out.setdefault("page", int(data.get("page") or 0))
        out.setdefault("pages", max(1, int(data.get("pages") or 1)))
    awaiting = data.get("awaiting_input")
    if isinstance(awaiting, dict) and (awaiting.get("prompt") or awaiting.get("action")):
        out["awaiting_input"] = {
            "prompt": str(awaiting.get("prompt") or "اكتب الرد")[:300],
            "action": str(awaiting.get("action") or "")[:_ID_MAX],
        }
    return out


def _clean_menu(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise HTTPException(502, "الـ adapter رجع شكلاً غير صالح لـ menu")
    items = [i for i in (_clean_item(raw) for raw in (data.get("menu") or [])) if i]
    return {
        "status_text": str(data.get("status_text") or "")[:1000],
        "menu": items,
        "page": int(data.get("page") or 0),
        "pages": max(1, int(data.get("pages") or 1)),
        "awaiting_input": (
            _clean_result({"awaiting_input": data["awaiting_input"]}).get("awaiting_input")
            if isinstance(data.get("awaiting_input"), dict)
            else None
        ),
    }


class _Auth:
    """يتحقق من ``Authorization: Bearer <secret>`` بوقت ثابت."""

    def __init__(self, secret: str):
        self._secret = secret.encode()

    def __call__(self, authorization: str | None = Header(None)) -> None:
        if not authorization:
            raise HTTPException(401, "مفتاح الجسر مفقود")
        provided = authorization.removeprefix("Bearer ").removeprefix("bearer ").strip().encode()
        if not secrets.compare_digest(provided, self._secret):
            raise HTTPException(401, "مفتاح الجسر غير مطابق")


def _user_id(x_tg_user_id: str | None = Header(None)) -> int:
    try:
        value = int((x_tg_user_id or "").strip())
    except (TypeError, ValueError):
        raise HTTPException(400, "X-TG-User-Id مطلوب ويجب أن يكون رقماً") from None
    if value <= 0:
        raise HTTPException(400, "X-TG-User-Id غير صالح")
    return value


def create_app(
    adapter: BridgeAdapter | None = None, secret: str | None = None
) -> FastAPI:
    """مصنع التطبيق (يسهّل الاختبار: مرّر adapter وسرّاً خاصين بك)."""
    bridge_secret = (secret or os.environ.get("BRIDGE_SECRET") or "").strip()
    if not bridge_secret:
        raise RuntimeError(
            "BRIDGE_SECRET غير مضبوط — لا يشغّل الجسر بلا سر. "
            "أنشئ سراً: openssl rand -hex 32"
        )
    active_adapter = adapter or load_adapter()
    auth = _Auth(bridge_secret)
    app = FastAPI(title="SD ↔ WhatsApp bridge", version=VERSION, docs_url=None, redoc_url=None)

    async def _call(func, *args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except BridgeError as exc:
            raise HTTPException(400, str(exc)) from exc
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 — لا نسرّب تفاصيل داخلية لبوت SD
            logger.exception("فشل داخلي في الجسر")
            raise HTTPException(500, "خطأ داخلي في جسر واتساب") from None

    @app.get("/ping")
    async def ping(_: None = Depends(auth)) -> dict[str, Any]:
        describe = getattr(active_adapter, "describe", None)
        info = describe() if callable(describe) else {}
        return {"ok": True, "version": VERSION, **info}

    async def _body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001 — جسم الطلب اختياري في هذا العقد
            return {}
        return data if isinstance(data, dict) else {}

    @app.post("/link/start")
    async def link_start(
        request: Request,
        tg_id: int = Depends(_user_id),
        _: None = Depends(auth),
    ) -> dict[str, Any]:
        payload = await _body(request)
        phone = str(payload.get("phone") or "").strip()
        if not re.fullmatch(r"\+?\d{8,15}", phone):
            raise HTTPException(400, "رقم غير صالح — يجب أن يبدأ بـ + ويتبعه 8-15 رقماً")
        result = await _call(active_adapter.start_link, tg_id, phone)
        if not isinstance(result, dict) or not result.get("link_code"):
            raise HTTPException(502, "الـ adapter لم يرجع link_code")
        return {
            "link_code": str(result["link_code"])[:64],
            "instructions": str(result.get("instructions") or "")[:1500],
        }

    @app.get("/link/status")
    async def link_status(
        tg_id: int = Depends(_user_id), _: None = Depends(auth)
    ) -> dict[str, Any]:
        result = await _call(active_adapter.link_status, tg_id) or {}
        state = str(result.get("state") or "none")
        if state not in VALID_STATES:
            logger.warning("adapter أرجع حالة غير معروفة %r — اعتُمدت none", state)
            state = "none"
        return {"state": state, "connected_since": result.get("connected_since")}

    @app.post("/link/unlink")
    async def link_unlink(
        tg_id: int = Depends(_user_id), _: None = Depends(auth)
    ) -> dict[str, Any]:
        result = await _call(active_adapter.unlink, tg_id) or {}
        return {"ok": bool(result.get("ok"))}

    @app.get("/menu")
    async def menu(
        page: int = Query(default=0, ge=0, le=500),
        tg_id: int = Depends(_user_id),
        _: None = Depends(auth),
    ) -> dict[str, Any]:
        return _clean_menu(await _call(active_adapter.menu, tg_id, page))

    async def _dispatch(
        tg_id: int, body: dict[str, Any], *, direct_input: bool
    ) -> dict[str, Any]:
        raw_payload = body.get("payload")
        body_payload = raw_payload if isinstance(raw_payload, dict) else {}
        if direct_input:
            text = str(body.get("text") or "")
            if not text.strip():
                raise HTTPException(400, "نص فارغ")
            result = await _call(active_adapter.input, tg_id, text[:4000], body_payload)
        else:
            action_id = str(body.get("action") or "").strip()
            if not action_id:
                raise HTTPException(400, "حقل action مطلوب")
            result = await _call(
                active_adapter.action, tg_id, action_id[:_ID_MAX], body_payload
            )
        return _clean_result(result)

    @app.post("/action")
    async def action(
        request: Request,
        tg_id: int = Depends(_user_id),
        _: None = Depends(auth),
    ) -> dict[str, Any]:
        return await _dispatch(tg_id, await _body(request), direct_input=False)

    @app.post("/input")
    async def submit_input(
        request: Request,
        tg_id: int = Depends(_user_id),
        _: None = Depends(auth),
    ) -> dict[str, Any]:
        return await _dispatch(tg_id, await _body(request), direct_input=True)

    return app


def _guard_public_bind() -> None:
    host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "::") and os.environ.get("WA_BRIDGE_ALLOW_PUBLIC") != "1":
        logger.warning(
            "⚠️ الجسر يستمع على %s — أي من يعرف المنفذ والسر يستطيع التصرف باسم "
            "أي مستخدم (X-TG-User-Id غير موقّع). استعمل 127.0.0.1 أو شبكة داخلية، "
            "أو اضبط WA_BRIDGE_ALLOW_PUBLIC=1 لو كان خلف TLS + IP allowlist.",
            host,
        )


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:  # PEP 562 — يبني التطبيق عند أول استخدام فقط
    """يبني ``app`` عند أول استيراد لـ uvicorn، حتى يستورد الاختبار الملف بلا env."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:  # pragma: no cover — نقطة تشغيل يدوية
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _guard_public_bind()
    import uvicorn

    uvicorn.run(
        "wa_bridge.bridge:app",
        host=os.environ.get("BRIDGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("BRIDGE_PORT", "8090")),
        log_level=os.environ.get("BRIDGE_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":  # pragma: no cover
    main()

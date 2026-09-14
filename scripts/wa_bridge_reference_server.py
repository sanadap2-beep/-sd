"""
سيرفر مرجعي — جسر واتساب v1 (للبوت الثاني).

هذا الملف هو «الجهة الأخرى» من docs/whatsapp_bridge_v1_ar.md:
سيرفر FastAPI صغير يطبّق العقد كاملاً (health / sessions / command / delete)
بحيث يكفي تشغيله بجانب البوت الثاني وربط منطق الجلسات الحقيقي بداخله.

التشغيل:
    pip install fastapi uvicorn
    BRIDGE_KEY=my-secret-key uvicorn --host 0.0.0.0 --port 8090 \
        wa_bridge_reference_server:app

ثم في لوحة البوت الرئيسي:
    🤖 أقسام الذكاء الاصطناعي ← 📱 قسم واتساب
    الرابط: http://<سيرفرك>:8090   المفتاح: my-secret-key

⚠️ نقطة الدمج الوحيدة المطلوبة منك:
    دوال `_engine_*` أسفل الملف — استبدل محتواها بمنطق بوتك الثاني
    (Baileys / whatsapp-web.js / قاعدة بياناتك). البنية هنا احتياطية
    في الذاكرة فقط ليثبت العقد قبل الدمج.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="WhatsApp Bridge v1 (reference)", version="1.0")

BRIDGE_KEY = os.environ.get("BRIDGE_KEY", "change-me-please")

# ── مخزن احتياطي في الذاكرة — استبدله بمخزن دائم عند الدمج ──
SESSIONS: dict[str, dict[str, Any]] = {}

STATUS_VALID = {"pending", "linked", "expired", "disconnected"}

MENU = {
    "text": "🧭 أهلاً بك في قائمة بوت واتساب:\nاختر الأمر من الأزرار بالأسفل.",
    "buttons": [
        {"text": "🛒 شراء اشتراك", "action": "buy"},
        {"text": "📊 حسابي", "action": "account"},
        {"text": "🛠 الأدوات", "action": "tools"},
        {"text": "ℹ️ مساعدة", "action": "help"},
    ],
}


def _auth(x_bridge_key: str | None) -> None:
    if not x_bridge_key or not secrets.compare_digest(x_bridge_key, BRIDGE_KEY):
        raise HTTPException(status_code=401, detail="invalid bridge key")


# ══════════════ العقد v1 ══════════════


@app.get("/health")
async def health(x_bridge_key: str | None = Header(default=None)):
    _auth(x_bridge_key)
    return {
        "ok": True,
        "engine": "reference-in-memory",
        "active_sessions": sum(1 for s in SESSIONS.values() if s["status"] == "linked"),
    }


@app.post("/sessions")
async def create_session(request: Request, x_bridge_key: str | None = Header(default=None)):
    _auth(x_bridge_key)
    body = await request.json()
    phone = str(body.get("phone") or "").strip()
    if not phone.replace("+", "").isdigit():
        raise HTTPException(status_code=400, detail="invalid phone")

    session_id = "sess_" + secrets.token_hex(6)
    # ── نقطة الدمج: اطلب كود اقتران حقيقي من محرك واتساب عندك هنا ──
    pairing_code = await _engine_start_pairing(session_id, phone)
    SESSIONS[session_id] = {
        "session_id": session_id,
        "phone": phone,
        "status": "pending",
        "pairing_code": pairing_code,
        "created_at": time.time(),
    }
    return {
        "session_id": session_id,
        "status": "pending",
        "pairing_code": pairing_code,
        "expires_in": 120,
    }


@app.get("/sessions/{session_id}")
async def session_status(session_id: str, x_bridge_key: str | None = Header(default=None)):
    _auth(x_bridge_key)
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    # ── نقطة الدمج: اسأل المحرك الحقيقي عن الحالة هنا ──
    session["status"] = await _engine_session_status(session)
    return {
        "session_id": session_id,
        "phone": session["phone"],
        "status": session["status"],
    }


@app.post("/sessions/{session_id}/command")
async def run_command(session_id: str, request: Request, x_bridge_key: str | None = Header(default=None)):
    _auth(x_bridge_key)
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session["status"] != "linked":
        raise HTTPException(status_code=409, detail=f"session is {session['status']}, not linked")

    body = await request.json()
    action = str(body.get("action") or "menu").strip()
    text = str(body.get("text") or "").strip()

    # ── نقطة الدمج: مرّر الأمر لمنطق بوتك الثاني هنا ──
    return await _engine_command(session, action, text)


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, x_bridge_key: str | None = Header(default=None)):
    _auth(x_bridge_key)
    session = SESSIONS.pop(session_id, None)
    if session is not None:
        # ── نقطة الدمج: افصل جلسة واتساب فعلياً عند المحرك هنا ──
        await _engine_logout(session)
    return {"ok": True}


# ══════════════ نقاط الدمج — استبدلها بمنطق بوتك الثاني ══════════════


async def _engine_start_pairing(session_id: str, phone: str) -> str:
    """
    TODO(البوت الثاني): أنشئ جلسة واتساب حقيقية للرقم عبر محركك
    (مثلاً Baileys: sock = makeWASocket({ printQRInTerminal: false })
    ثم استخدم requestPairingCode(phone)).
    القيمة هنا محاكاة: كود من 8 خانات.
    """
    return "-".join(secrets.token_hex(2).upper() for _ in range(2))


async def _engine_session_status(session: dict) -> str:
    """
    TODO(البوت الثاني): ارجع حالة الاتصال الحقيقية من المحرك.
    للتجربة: يتحول pending → linked تلقائياً بعد 20 ثانية من الإنشاء.
    """
    if session["status"] == "pending" and time.time() - session["created_at"] > 20:
        return "linked"
    return session["status"]


async def _engine_command(session: dict, action: str, text: str) -> dict:
    """
    TODO(البوت الثاني): نفّذ أمر بوتك الثاني الحقيقي وأرجع
    {"text": str, "buttons": [{"text": str, "action": str}] | None}.
    القوائم هنا محاكاة كاملة العقد.
    """
    if action == "menu" or not action:
        return dict(MENU)
    if action == "buy":
        return {
            "text": "🛒 اختر الاشتراك:",
            "buttons": [
                {"text": " Netflix شهر — $5", "action": "buy:netflix"},
                {"text": " Spotify شهر — $3", "action": "buy:spotify"},
                {"text": "🔙 الرئيسية", "action": "menu"},
            ],
        }
    if action.startswith("buy:"):
        product = action.split(":", 1)[1]
        return {
            "text": f"✅ تم تسجيل طلب «{product}» للرقم {session['phone']}. سيتم التواصل معك.",
            "buttons": [{"text": "🔙 الرئيسية", "action": "menu"}],
        }
    if action == "account":
        return {
            "text": f"📊 حسابك: {session['phone']} — لا طلبات سابقة.",
            "buttons": [{"text": "🔙 الرئيسية", "action": "menu"}],
        }
    if action == "tools":
        return {
            "text": "🛠 الأدوات المتاحة: أدوات الجملة، التحويلات، التقارير.",
            "buttons": [
                {"text": "🧾 تقرير يومي", "action": "tools:daily"},
                {"text": "🔙 الرئيسية", "action": "menu"},
            ],
        }
    if action == "text":
        return {
            "text": f"📥 وصلت رسالتك: {text[:200]}",
            "buttons": [{"text": "🔙 الرئيسية", "action": "menu"}],
        }
    return {
        "text": f"تم تنفيذ «{action}».",
        "buttons": [{"text": "🔙 الرئيسية", "action": "menu"}],
    }


async def _engine_logout(session: dict) -> None:
    """TODO(البوت الثاني): افصل جلسة واتساب الحقيقية (sock.end())."""
    return None

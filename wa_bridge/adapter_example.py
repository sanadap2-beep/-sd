"""مثال adapter حقيقي للبوت الثاني (wa_bridge/adapter_example.py) — انسخه إلى مشروعك ووصّل الدوال الثلاث.

الفكرة: لا تكتب منطقاً جديداً للجسر؛ البوت الثاني يملك أصلاً
«خدمات» (services) تنفّذ أوامره، فكل ما يحتاجه الـ adapter هو:

1. ``menu(tg_id)``  → يبني نفس الأزرار التي يبنيها البوت الثاني للمستخدم،
   لكن كقواميس ``{id,label}`` بدل InlineKeyboardMarkup.
2. ``action(tg_id, action_id, payload)`` → ينفّذ نفس الدالة التي ينفذها
   الـ callback handler عندك (يفضّل استدعاء طبقة الخدمة مباشرة).
3. ``start_link / link_status / unlink`` → جلسة واتساب عند مزودك.

الأزرار التي تحتاج **كتابة نص** (رسالة، رقم مستلم، …) ضع لها
``"kind": "input"`` مع ``prompt`` — بوت SD يلتقط النص ويرسله إلى ``input``.

الاستخدام عندك::

    # myapp/wa_bridge_adapter.py
    from wa_bridge.adapter import BridgeAdapter
    from myapp.services.whatsapp import WaService        # منطقك الموجود
    from myapp.db import session_scope                   # جلسة قاعدة بياناتك

    class MyWaAdapter(BridgeAdapter):
        name = "mywa"
        capabilities = ("menu", "action", "input", "files", "link", "unlink", "paging")

        async def menu(self, tg_id, page=0):
            async with session_scope() as session:
                items = await WaService.build_menu(session, tg_id)
            return self.paginate_and_wrap(items, page)   # انظر الملاحظة أسفل الملف

        async def action(self, tg_id, action_id, payload=None):
            async with session_scope() as session:
                text, new_items = await WaService.run(session, tg_id, action_id, payload or {})
            return {"text": text, "menu": new_items}

        async def input(self, tg_id, text, payload=None):
            async with session_scope() as session:
                out = await WaService.handle_text(session, tg_id, text)
            return {"text": out}

    WA_BRIDGE_ADAPTER="myapp.wa_bridge_adapter:MyWaAdapter"

ملاحظة: ``BridgeAdapter`` لا يوفّر ``paginate_and_wrap`` — استخدم ``paginate``::

    page_items, page_no, pages = self.paginate(items, page)
    return {"status_text": status_text, "menu": page_items, "page": page_no, "pages": pages}
"""

from __future__ import annotations

import base64
from typing import Any

from wa_bridge.adapter import BridgeAdapter, BridgeError

# ══════════════════════════════════════════════════════════════════════
# هذا الملف **قابل للتشغيل** كما هو لاختبار شكل العقد (بلا قاعدة بيانات):
# المخزون في الذاكرة، وكل أمر يردّ بنص. استبدل bodies الدوال بمنطقك.
# ══════════════════════════════════════════════════════════════════════


class ExampleWaAdapter(BridgeAdapter):
    name = "example-wa"
    capabilities = ("menu", "action", "input", "files", "link", "unlink", "paging")
    page_size = 12

    def __init__(self, **_ignored: Any):
        # user id → حالة الجلسة. عندك: جدول sessions في قاعدة بيانات البوت الثاني.
        self._sessions: dict[int, dict[str, Any]] = {}
        self._outbox: dict[int, list[str]] = {}

    # ── الربط ──

    async def start_link(self, tg_id: int, phone: str) -> dict[str, Any]:
        code = f"{100000 + (tg_id % 899999)}"
        self._sessions[tg_id] = {"phone": phone, "state": "pending", "code": code}
        return {
            "link_code": code,
            "instructions": (
                f"افتح لوحة مزود الواتساب عندك ← إضافة جهاز ← أدخل الكود {code}. "
                "ثم اضغط «فحص الربط» في بوت SD."
            ),
        }

    async def link_status(self, tg_id: int) -> dict[str, Any]:
        session = self._sessions.get(tg_id)
        if not session:
            return {"state": "none", "connected_since": None}
        # هنا تسأل مزودك فعلاً: هل الجلسة متصلة؟
        return {
            "state": session.get("state", "pending"),
            "connected_since": session.get("connected_since"),
        }

    async def unlink(self, tg_id: int) -> dict[str, Any]:
        self._sessions.pop(tg_id, None)
        return {"ok": True}

    # ── الأزرار ──

    async def menu(self, tg_id: int, page: int = 0) -> dict[str, Any]:
        session = self._sessions.get(tg_id) or {}
        state = session.get("state", "none")
        status_text = (
            f"🟢 الجلسة مربوطة على {session.get('phone', '؟')}"
            if state == "linked"
            else "⏳ بانتظار إدخال كود الربط عند المزود"
            if state == "pending"
            else "🔴 لا توجد جلسة مربوطة"
        )
        items: list[dict[str, Any]] = [
            {"id": "outbox", "label": f"📬 الصادر ({len(self._outbox.get(tg_id, []))})"},
            {
                "id": "send",
                "label": "📮 إرسال رسالة",
                "kind": "input",
                "prompt": "اكتب:  <رقم المستلم> | <النص>\nمثال: +9665xxxxxxxx | مرحباً",
            },
            {"id": "broadcast", "label": "📣 بث للجميع", "kind": "input", "prompt": "اكتب نص البث"},
            {"id": "contacts", "label": "👥 جهات الاتصال"},
            {"id": "groups", "label": "👥 المجموعات"},
            {"id": "qr", "label": "🔳 إعادة ربط (كود جديد)"},
            {"id": "kill", "label": "🚫 إنهاء الجلسة"},
            {"id": "export", "label": "📄 تنزيل سجل الصادر"},
        ]
        page_items, page_no, pages = self.paginate(items, page)
        return {
            "status_text": status_text,
            "menu": page_items,
            "page": page_no,
            "pages": pages,
        }

    async def action(
        self, tg_id: int, action_id: str, payload: dict | None = None
    ) -> dict[str, Any]:
        payload = payload or {}
        if action_id == "kill":
            await self.unlink(tg_id)
            return {"text": "🚫 أُنهيت الجلسة. أعد الربط من «أرسل رقم واتساب»."}
        if action_id == "qr":
            res = await self.start_link(
                tg_id, (self._sessions.get(tg_id) or {}).get("phone", "")
            )
            return {
                "text": (
                    f"🔑 كود الربط الجديد: <code>{res['link_code']}</code>\n"
                    f"{res.get('instructions', '')}"
                )
            }
        if action_id == "export":
            lines = "\n".join(self._outbox.get(tg_id, [])) or "لا شيء بعد"
            return {
                "text": "📄 تفضل سجل الصادر",
                "files": [
                    {"name": "outbox.txt", "content_b64": base64.b64encode(lines.encode()).decode()}
                ],
            }
        if action_id == "contacts":
            return {"text": "👥 لديك 3 جهات اتصال (مثال)."}
        if action_id == "groups":
            return {"text": "👥 لديك 0 مجموعات (مثال)."}
        if action_id == "outbox":
            sent = self._outbox.get(tg_id, [])
            return {"text": f"📬 أرسلت {len(sent)} رسالة.\n" + "\n".join(sent[-5:])}
        if action_id in {"send", "broadcast"}:
            # الأزرار ذات kind=input لا تصل هنا عادةً — تنتظر نصاً في input().
            raise BridgeError("اكتب النص أولاً ثم أرسله.")
        raise BridgeError(f"أمر غير معروف عند الجسر: {action_id}")

    async def input(
        self, tg_id: int, text: str, payload: dict | None = None
    ) -> dict[str, Any]:
        session = self._sessions.get(tg_id) or {}
        if session.get("state") != "linked":
            return {"text": "🔴 لا توجد جلسة مفعّلة — اربط رقمك أولاً."}
        body = text.strip()
        if "|" in body:
            target, _, message = body.partition("|")
            self._outbox.setdefault(tg_id, []).append(f"{target.strip()}: {message.strip()[:120]}")
            # ← هنا تستدعي مزودك فعلياً: WaService.send(tg_id, target, message)
            return {"text": f"📤 أُرسلت رسالة إلى {target.strip()}."}
        self._outbox.setdefault(tg_id, []).append(f"broadcast: {body[:120]}")
        return {"text": "📣 بدأ البث (مثال بلا مزود حقيقي)."}

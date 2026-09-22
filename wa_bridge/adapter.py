"""طبقة الوصل بين جسر واتساب وبوتك الثاني.

الجسر (``wa_bridge/bridge.py``) لا يعرف شيئاً عن منطق بوت الواتساب — هو ينفّذ
طلبات HTTP ويحوّلها إلى استدعاءات على **adapter** واحد، وأنت تكتب الـ adapter
لتقرأ قوائم بوتك وتنّفذ أوامره كما هي.

الاتفاقية (كل الدوال async وترجع قواميس بسيطة):

    menu(tg_id)        → {"status_text": str, "menu": [item...],
                          "page": int, "pages": int,
                          "awaiting_input": {"prompt": str, "action": str} | None}
    action(tg_id, id, payload) → {"text": str, "alert": bool,
                                  "files": [{"name","url"|"content_b64"}],
                                  "buttons": [{"text","url"}],
                                  "menu": [item...], "page": int, "pages": int,
                                  "awaiting_input": {...} | None}
    input(tg_id, text) → نفس شكل action (رد على نص كتبه المستخدم)
    start_link(tg_id, phone) → {"link_code": str, "instructions": str}
    link_status(tg_id)  → {"state": "none|pending|linked|expired", "connected_since": str|None}
    unlink(tg_id)       → {"ok": True}

شكل العنصر (item) في القائمة:

    {"id": "sess:1", "label": "📮 إرسال رسالة", "data": {...},
     "kind": "action|input|url|back|refresh",  # اختياري، الافتراضي action
     "prompt": "اكتب نص الرسالة",             # مطلوب مع kind=input
     "url": "https://..."}                     # مطلوب مع kind=url

``kind=input`` هو ما يجعل الأزرار التي تحتاج كتابة تعمل داخل بوت SD بلا أي
تحويلة: الجسر يبلّغ بوت SD «انتظر نصاً»، فيرسله إليك في ``input``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

logger = logging.getLogger("wa_bridge.adapter")

MENU_PAGE_SIZE_DEFAULT = 12

# القدرات التي يفهمها بوت SD؛ أعلن ما تنفذه منه فقط.
CAPABILITIES_ALL = ("menu", "action", "input", "files", "links", "link", "unlink", "paging")


class BridgeError(Exception):
    """خطأ قابل للعرض للمستخدم (يرجعه الجسر كنص توضيحي)."""


class BridgeAdapter:
    """الواجهة الافتراضية — أعد تعريف ما تحتاجه في adapter ابني عليها."""

    name = "base"
    capabilities: tuple[str, ...] = ("menu", "action", "link")
    page_size = MENU_PAGE_SIZE_DEFAULT

    async def menu(self, tg_id: int, page: int = 0) -> dict[str, Any]:
        raise BridgeError("هذا البوت لا يوفّر قائمة أزرار (نفّذ menu في الـ adapter).")

    async def action(
        self, tg_id: int, action_id: str, payload: dict | None = None
    ) -> dict[str, Any]:
        raise BridgeError("هذا البوت لا ينفّذ أوامر (نفّذ action في الـ adapter).")

    async def input(self, tg_id: int, text: str, payload: dict | None = None) -> dict[str, Any]:
        result = await self.action(tg_id, f"input:{text[:500]}", payload)
        return result

    async def start_link(self, tg_id: int, phone: str) -> dict[str, Any]:
        raise BridgeError("هذا البوت لا يدعم ربط الأرقام (نفّذ start_link في الـ adapter).")

    async def link_status(self, tg_id: int) -> dict[str, Any]:
        return {"state": "none", "connected_since": None}

    async def unlink(self, tg_id: int) -> dict[str, Any]:
        return {"ok": False, "error": "unlink غير مدعوم"}

    # ── أدوات مساعدة للـ adapters الابنة ──

    def paginate(self, items: list[dict], page: int) -> tuple[list[dict], int, int]:
        """يرجع (عناصر_الصفة, رقم_الصفة, عدد_الصفحات) مع قصّ الأزرار للحجم المسموح."""
        size = max(1, min(int(self.page_size), 40))
        total_pages = max(1, -(-len(items) // size))
        page = min(max(0, int(page or 0)), total_pages - 1)
        return items[page * size : (page + 1) * size], page, total_pages

    def describe(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "capabilities": list(self.capabilities),
            "page_size": self.page_size,
        }


class StaticJsonAdapter(BridgeAdapter):
    """Adapter تجريبي يقرأ قائمة الأزرار من ملف JSON — لتجربة الربط كاملاً
    قبل أن تبرمج الـ adapter الحقيقي (يصلح لـ smoke test فقط).

    ملف JSON المتوقع::

        {
          "status_text": "🟢 جلسة واحدة نشطة",
          "menu": [{"id": "top", "label": "⭐ المتصدرون"},
                   {"id": "send", "label": "📮 إرسال رسالة", "kind": "input",
                    "prompt": "اكتب نص الرسالة"}]
        }
    """

    name = "static-json"
    capabilities = ("menu", "action", "input", "links", "link", "unlink", "paging")

    def __init__(self, path: str | None = None, **_ignored: Any):
        self.path = path or os.environ.get(
            "WA_BRIDGE_MENU_FILE",
            os.path.join(os.path.dirname(__file__), "menu.example.json"),
        )
        self._pending: dict[int, dict[str, Any]] = {}

    def _load(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            raise BridgeError(f"ملف القائمة غير موجود عند الجسر: {self.path}") from None
        except json.JSONDecodeError as exc:
            raise BridgeError(f"ملف القائمة JSON غير صالح: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("menu"), list):
            raise BridgeError("ملف القائمة يجب أن يكون {\"menu\": [...]}")
        return data

    async def menu(self, tg_id: int, page: int = 0) -> dict[str, Any]:
        data = self._load()
        items = [i for i in data["menu"] if isinstance(i, dict) and i.get("id")]
        page_items, page_no, pages = self.paginate(items, page)
        return {
            "status_text": data.get("status_text", ""),
            "menu": page_items,
            "page": page_no,
            "pages": pages,
            "awaiting_input": self._pending.get(tg_id, {}).get("awaiting_input"),
        }

    async def start_link(self, tg_id: int, phone: str) -> dict[str, Any]:
        self._pending[tg_id] = {"phone": phone, "state": "pending"}
        return {
            "link_code": str(100000 + (abs(tg_id) % 899999)),
            "instructions": f"(تجريبي) رقمك {phone} — أدخل الكود عند مزودك ثم اضغط «فحص الربط».",
        }

    async def link_status(self, tg_id: int) -> dict[str, Any]:
        state = self._pending.get(tg_id, {}).get("state", "none")
        return {"state": state, "connected_since": None}

    async def unlink(self, tg_id: int) -> dict[str, Any]:
        self._pending.pop(tg_id, None)
        return {"ok": True}

    async def action(
        self, tg_id: int, action_id: str, payload: dict | None = None
    ) -> dict[str, Any]:
        data = self._load()
        item = next(
            (i for i in data["menu"] if isinstance(i, dict) and i.get("id") == action_id),
            None,
        )
        if item and item.get("kind") == "input":
            self._pending[tg_id] = {
                "awaiting_input": {
                    "prompt": item.get("prompt") or "اكتب الرد",
                    "action": action_id,
                }
            }
            return {
                "text": item.get("prompt") or "اكتب الرد:",
                "awaiting_input": {"prompt": "اكتب الرد", "action": action_id},
            }
        self._pending.setdefault(tg_id, {})["state"] = "linked"
        result = {
            "text": f"✅ (تجريبي) نفّذ الجسر الأمر `{action_id}` للـ user {tg_id}.",
            "buttons": [{"text": "🌐 لوحة المزود", "url": "https://example.com"}],
        }
        demo = (item or {}).get("data") if isinstance(item, dict) else None
        if isinstance(demo, dict) and demo.get("demo") == "file":
            # يثبت أن بوت SD يستطيع إرجاع ملفات البوت الثاني كما هي.
            result["files"] = [
                {
                    "name": "bridge-demo.txt",
                    "content_b64": base64.b64encode(
                        f"ملف تجريبي من جسر واتساب للمستخدم {tg_id}".encode()
                    ).decode(),
                }
            ]
        return result

    async def input(self, tg_id: int, text: str, payload: dict | None = None) -> dict[str, Any]:
        state = self._pending.pop(tg_id, {}) or {}
        action_id = (state.get("awaiting_input") or {}).get("action", "input")
        return {
            "text": (
                f"✅ (تجريبي) استلم الجسر نصاً للأمر `{action_id}`:\n\n"
                f"<code>{text[:200]}</code>"
            )
        }


def load_adapter(spec: str | None = None) -> BridgeAdapter:
    """يبني الـ adapter من ``WA_BRIDGE_ADAPTER`` بصيغة ``module.path:ClassName``.

    ``WA_BRIDGE_ADAPTER_CONFIG`` (JSON اختياري) يُمرَّر kwargs لـ ``__init__``.
    """
    raw = (spec or os.environ.get("WA_BRIDGE_ADAPTER") or "").strip()
    if not raw:
        logger.warning("لم يُضبط WA_BRIDGE_ADAPTER — الجسر سيعمل بالـ adapter التجريبي.")
        return StaticJsonAdapter()
    module_name, _, class_name = raw.partition(":")
    if not module_name or not class_name:
        raise RuntimeError(
            "WA_BRIDGE_ADAPTER يجب أن يكون بصيغة 'my_pkg.my_module:MyAdapter'"
        )
    import importlib

    config: dict[str, Any] = {}
    raw_config = os.environ.get("WA_BRIDGE_ADAPTER_CONFIG", "").strip()
    if raw_config:
        try:
            config = json.loads(raw_config)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"WA_BRIDGE_ADAPTER_CONFIG ليس JSON صالحاً: {exc}") from exc

    module = importlib.import_module(module_name)
    adapter_cls = getattr(module, class_name, None)
    if adapter_cls is None:
        raise RuntimeError(f"الصنف {class_name} غير موجود في {module_name}")
    adapter = adapter_cls(**config)
    if not isinstance(adapter, BridgeAdapter):
        raise RuntimeError("الـ adapter يجب أن يرث BridgeAdapter من wa_bridge.adapter")
    logger.info("جسر واتساب يستخدم adapter=%s قدرات=%s", adapter.name, adapter.capabilities)
    return adapter

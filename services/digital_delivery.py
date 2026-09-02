"""
تنسيق محتوى التوصيل الرقمي (ggsoma وغيره) ليعرض للمشتري فور الشراء.

المنتجات الرقمية اللحظية (LINK / COUPON / READY_ACCOUNT) تأتي من المزود
بتسليم فوري في نفس استجابة إنشاء الطلب. هذا الملف يوحّد طريقة استخراج
ذلك المحتوى من ``result_data`` / ردود المزود وعرضه بأمان:

- ``format_delivery_html``  → نص جاهز لرسائل تيليجرام (HTML مؤمَّن).
- ``format_delivery_text``  → نص خام عادي للواجهات (WebApp/السلة).

ملاحظة أمنية: محتوى ``content`` (حسابات READY_ACCOUNT) حساس ولا يُسجَّل
في أي مكان — يُرسل للمشتري فقط داخل رسالته الخاصة.
"""

from __future__ import annotations

import json
from html import escape

__all__ = ["format_delivery_html", "format_delivery_text"]


def _as_dict(raw) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _first_str(container: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = container.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


_DELIVERY_MARKERS = ("link", "url", "redeemUrl", "content", "credentials", "instructions")


def _looks_like_digital_delivery(group: dict) -> bool:
    """هل الكائن توصيل رقمي (LINK/كوبون/حساب)؟ أيٌّ من علاماته المميزة يكفي.

    كائنات الرد القديمة التي تحمل code/sms/phone فقط (رسائل/أرقام) لا
    تُعد توصيلاً رقمياً حتى يبقى مسارها القديم (رقم + كود) كما هو.
    """
    return any(group.get(field) for field in _DELIVERY_MARKERS)


def _delivery(container: dict) -> dict | None:
    """يبحث عن كائن التوصيل: delivery مباشرة أو داخل data/order/result."""
    for key in ("delivery", "delivery_info"):
        value = container.get(key)
        if isinstance(value, dict) and value:
            return value
    for group_key in ("data", "order", "result"):
        group = container.get(group_key)
        if isinstance(group, dict):
            for key in ("delivery", "delivery_info"):
                value = group.get(key)
                if isinstance(value, dict) and value:
                    return value
            # بعض المزودين يضعون حقول التوصيل على مستوى group مباشرة.
            if _looks_like_digital_delivery(group):
                return group
    return None


def _delivery_parts(delivery: dict) -> list[tuple[str, str]]:
    """يعيد [ (وسم, قيمة) ] مرتبة حسب أولوية العرض: رابط → كوبون → حساب."""
    parts: list[tuple[str, str]] = []
    link = _first_str(delivery, ("link", "url", "redeemUrl"))
    if link:
        parts.append(("link", link))
    code = _first_str(delivery, ("code", "coupon", "couponCode", "promoCode", "voucher"))
    if code:
        parts.append(("code", code))
    content = _first_str(
        delivery,
        ("content", "credentials", "account", "accountData", "login"),
    )
    if content:
        parts.append(("content", content))
    instructions = _first_str(delivery, ("instructions", "note"))
    if instructions:
        parts.append(("instructions", instructions))
    return parts


def format_delivery_html(raw) -> str:
    """نص تسليم HTML-مؤمَّن لإشعار تيليجرام (فارغ إن لم يوجد تسليم)."""
    payload = _as_dict(raw)
    if payload is None:
        return ""
    parts: list[str] = []
    delivery = _delivery(payload)
    if delivery is not None:
        for kind, value in _delivery_parts(delivery):
            if kind == "link":
                parts.append(f"\n🔗 الرابط: <code>{escape(value)}</code>")
            elif kind == "code":
                parts.append(f"\n🎟 الكود: <code>{escape(value)}</code>")
            elif kind == "content":
                parts.append(f"\n🔑 بيانات الحساب:\n<code>{escape(value)}</code>")
            elif kind == "instructions":
                parts.append(f"\n📄 {escape(value)}")
    if not parts:
        # مزودو رسائل/أكواد قديمون: رقم وكود في الرد مباشرة.
        nested = payload.get("data")
        nested = nested if isinstance(nested, dict) else {}
        phone = _first_str(nested, ("phone", "number")) or _first_str(
            payload, ("phone", "number")
        )
        code = (
            _first_str(nested, ("code", "sms", "sms_code"))
            or _first_str(payload, ("code", "sms"))
        )
        if phone:
            parts.append(f"\n📞 الرقم: <code>{escape(phone)}</code>")
        if code:
            parts.append(f"\n🔑 الكود: <code>{escape(code)}</code>")
    return "".join(parts)


def format_delivery_text(raw) -> str | None:
    """نص عادي (بدون HTML) لعرض WebApp/السلة؛ None إن لم يوجد تسليم."""
    payload = _as_dict(raw)
    if payload is None:
        return None
    delivery = _delivery(payload)
    if delivery is None:
        return None
    lines: list[str] = []
    for kind, value in _delivery_parts(delivery):
        if kind == "link":
            lines.append(f"الرابط: {value}")
        elif kind == "code":
            lines.append(f"الكود: {value}")
        elif kind == "content":
            lines.append(f"بيانات الحساب:\n{value}")
        elif kind == "instructions":
            lines.append(f"ملاحظات: {value}")
    return "\n".join(lines) if lines else None

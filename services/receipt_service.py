"""إيصالات موحّدة للطلبات (البوت، تطبيق الويب، واجهة الموزعين).

نفس هوية رسالة تأكيد الطلب: تفاصيل كاملة، حالة مقروءة بالعربية، بيانات
التسليم إن كان الطلب قد سُلّم، وسطر «سيتم إشعارك عند اكتمال الخدمة» ما دام
الطلب لم يكتمل بعد.
"""

from __future__ import annotations

import json
from html import escape

SEPARATOR = "━━━━━━━━━━━━━━━━━━"

STATUS_LABELS: dict[str, str] = {
    "pending": "🕐 قيد الانتظار",
    "processing": "⚙️ جارٍ التنفيذ",
    "completed": "✅ مكتمل",
    "failed": "❌ فاشل",
    "refunded": "↩️ مُسترجع",
    "partial": "🟡 منفَّذ جزئياً",
    "code_received": "📩 تم استلام الكود",
    "expired": "⌛ منتهي",
    "cancelled": "🚫 ملغي",
}

FINAL_STATUSES = {"completed", "failed", "refunded", "cancelled", "expired", "code_received"}


def _status_label(order) -> str:
    raw = getattr(order, "status", None)
    value = getattr(raw, "value", raw)
    return STATUS_LABELS.get(str(value), str(value or "—"))


def _is_finished(order) -> bool:
    raw = getattr(order, "status", None)
    value = str(getattr(raw, "value", raw) or "")
    return value in FINAL_STATUSES


def _money(value) -> str:
    from decimal import Decimal, InvalidOperation

    try:
        return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}$"
    except (InvalidOperation, ValueError, TypeError):
        return f"{value}$"


def _delivery_block(order) -> str:
    """بيانات التسليم المحفوظة للطلبات المكتملة فورياً (إن وُجدت)."""
    raw = getattr(order, "result_data", None)
    if not raw or not _is_finished(order):
        return ""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    try:
        from services.digital_delivery import format_delivery_html

        html = format_delivery_html(payload)
    except Exception:  # noqa: BLE001 — نص تجميلي داخل الإيصال
        return ""
    return f"\n\n🎁 <b>بيانات التسليم:</b>{html}" if html else ""


class ReceiptService:
    @staticmethod
    def unified_text(order, user, product=None) -> str:
        product_name = product.name_ar if product else "—"
        lines = [
            "🧾 <b>إيصال عملية شراء</b>",
            SEPARATOR,
            "",
            f"🆔 <b>رقم الطلب:</b> <code>#{order.id}</code>",
            f"📦 <b>الخدمة:</b> {escape(str(product_name))}",
            f"📊 <b>الحالة:</b> {_status_label(order)}",
            f"💰 <b>المبلغ:</b> {_money(order.price_usd)}",
        ]
        if getattr(order, "quantity", 1) and int(order.quantity or 1) > 1:
            lines.append(f"🔢 <b>الكمية:</b> {order.quantity}")
        if getattr(order, "target", None):
            lines.append(f"🎯 <b>الهدف:</b> <code>{escape(str(order.target))}</code>")
        created = getattr(order, "created_at", None)
        if created is not None:
            lines.append(f"📅 <b>التاريخ:</b> {created.strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"👤 <b>العميل:</b> <code>{escape(str(user.telegram_id))}</code>")
        delivery = _delivery_block(order)
        if delivery:
            lines.append(delivery)
        lines.extend(["", SEPARATOR])
        lines.append(
            "شكراً لاستخدامك المتجر."
            if _is_finished(order)
            else "🔔 <b>سيتم إشعارك عند اكتمال الخدمة</b>"
        )
        return "\n".join(lines)

    @staticmethod
    def number_text(order, user) -> str:
        lines = [
            "🧾 <b>إيصال شراء رقم</b>",
            SEPARATOR,
            "",
            f"🆔 <b>رقم الطلب:</b> <code>#{order.id}</code>",
            f"📱 <b>الرقم:</b> <code>{escape(str(order.phone_number))}</code>",
            f"📲 <b>الخدمة:</b> {escape(str(order.service))}",
            f"🌍 <b>الدولة:</b> {escape(str(order.country_code))}",
            f"📊 <b>الحالة:</b> {_status_label(order)}",
            f"💰 <b>المبلغ:</b> {_money(order.price_sell_usd)}",
        ]
        purchased = getattr(order, "purchased_at", None)
        if purchased is not None:
            lines.append(f"📅 <b>التاريخ:</b> {purchased.strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"👤 <b>العميل:</b> <code>{escape(str(user.telegram_id))}</code>")
        lines.extend(["", SEPARATOR])
        lines.append(
            "شكراً لاستخدامك المتجر."
            if _is_finished(order)
            else "🔔 <b>سيتم إشعارك عند اكتمال الخدمة</b>"
        )
        return "\n".join(lines)

    @staticmethod
    def filename(order_id: int, kind: str = "order") -> str:
        return f"receipt-{kind}-{order_id}.txt"

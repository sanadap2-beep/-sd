"""Consistent receipts for orders across bot, Mini App and reseller API."""

from __future__ import annotations

from html import escape


class ReceiptService:
    @staticmethod
    def unified_text(order, user, product=None) -> str:
        product_name = product.name_ar if product else "—"
        return (
            "🧾 <b>إيصال عملية شراء</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🆔 رقم الطلب: <code>#{order.id}</code>\n"
            f"📦 المنتج: {escape(product_name)}\n"
            f"📊 الحالة: {escape(order.status.value)}\n"
            f"💰 المبلغ: <b>{order.price_usd}$</b>\n"
            f"📊 الكمية: {order.quantity}\n"
            f"🎯 الهدف: <code>{escape(order.target or '—')}</code>\n"
            f"📅 التاريخ: {order.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"👤 العميل: <code>{user.telegram_id}</code>\n"
            "━━━━━━━━━━━━━━\n"
            "شكراً لاستخدامك المتجر."
        )

    @staticmethod
    def number_text(order, user) -> str:
        return (
            "🧾 <b>إيصال شراء رقم SMS</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🆔 رقم الطلب: <code>#{order.id}</code>\n"
            f"📱 الرقم: <code>{escape(order.phone_number)}</code>\n"
            f"📲 الخدمة: {escape(order.service)}\n"
            f"🌍 الدولة: {escape(order.country_code)}\n"
            f"📊 الحالة: {escape(order.status.value)}\n"
            f"💰 المبلغ: <b>{order.price_sell_usd}$</b>\n"
            f"📅 التاريخ: {order.purchased_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"👤 العميل: <code>{user.telegram_id}</code>\n"
            "━━━━━━━━━━━━━━\n"
            "شكراً لاستخدامك المتجر."
        )

    @staticmethod
    def filename(order_id: int, kind: str = "order") -> str:
        return f"receipt-{kind}-{order_id}.txt"

"""Premium custom emoji for catalog entities (categories/subs/products).

التدفق: زر «✨ إيموجي مميز» في صفحة التفاصيل → الأدمن يرسل الإيموجي
المميز كرسالة → نستخرج custom_emoji_id من entities ونخزنه.
الإزالة برسالة «مسح». معاينة فورية تثبت أنه يعمل.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import Category, Product, SubCategory
from services.premium_emoji import answer_rendered, extract_custom_emoji_id
from states.states import AdminPremiumStates

logger = logging.getLogger(__name__)
router = Router(name="admin_premium_emoji")

_KINDS = {
    "cat": (Category, "admin:cat_view:{id}"),
    "sub": (SubCategory, "admin:subcat_view:{id}"),
    "prod": (Product, "admin:prod_view:{id}"),
}
_KIND_NAMES = {"cat": "القسم", "sub": "القسم الفرعي", "prod": "المنتج"}


def _back_kb(kind: str, entity_id: int) -> InlineKeyboardMarkup:
    _, back_tpl = _KINDS[kind]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 رجوع", callback_data=back_tpl.format(id=entity_id))]
        ]
    )


@router.callback_query(F.data.startswith("admin:prem:"))
async def premium_start(callback: CallbackQuery, state: FSMContext, session):
    """admin:prem:{kind}:{id} — طلب إرسال الإيموجي المميز."""
    try:
        _, _, kind, entity_id = callback.data.split(":")
        entity_id = int(entity_id)
    except (ValueError, IndexError):
        await callback.answer("غير صالح.", show_alert=True)
        return
    if kind not in _KINDS:
        await callback.answer("غير صالح.", show_alert=True)
        return
    model, _ = _KINDS[kind]
    entity = await session.get(model, entity_id)
    if entity is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    await state.update_data(prem_kind=kind, prem_id=entity_id)
    await state.set_state(AdminPremiumStates.waiting_emoji)
    current = getattr(entity, "custom_emoji_id", None)
    await callback.message.answer(
        f"✨ <b>إيموجي مميز لـ{_KIND_NAMES[kind]} «{getattr(entity, 'name_ar', '')}»</b>\n\n"
        "أرسل الإيموجي المميز ⭐ كرسالة (من أي حزمة Premium).\n"
        f"الحالي: {'مضبوط ✅' if current else 'لا يوجد'}\n"
        "أرسل «مسح» للإزالة.",
    )
    await callback.answer()


@router.message(AdminPremiumStates.waiting_emoji)
async def premium_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    kind = data.get("prem_kind")
    entity_id = data.get("prem_id")
    if kind not in _KINDS or not entity_id:
        await message.answer("⚠️ جلسة منتهية.")
        await state.clear()
        return
    model, _ = _KINDS[kind]
    entity = await session.get(model, entity_id)
    if entity is None:
        await message.answer("⚠️ غير موجود.")
        await state.clear()
        return

    text = (message.text or "").strip()
    if text in ("مسح", "حذف", "-", "remove"):
        entity.custom_emoji_id = None
        await session.commit()
        await state.clear()
        await message.answer("✅ أُزيل الإيموجي المميز.", reply_markup=_back_kb(kind, entity_id))
        return

    custom_id = extract_custom_emoji_id(message)
    if not custom_id:
        await message.answer(
            "⚠️ لم أجد إيموجي مميزاً في رسالتك.\n"
            "أرسل إيموجي ⭐ من حزمة Premium (اضغط عليه من لوحة الإيموجي) — "
            "وليس حرفاً عادياً ولا ملصقاً."
        )
        return

    entity.custom_emoji_id = custom_id
    if kind in ("cat", "sub") and not getattr(entity, "emoji", None):
        entity.emoji = "✨"
    await session.commit()
    await state.clear()
    name = getattr(entity, "name_ar", "")
    # معاينة حية تثبت أنه يعمل
    await answer_rendered(
        message,
        ("e", custom_id, "✨"),
        ("t", " "),
        ("b", f"تم! هكذا سيظهر بجانب «{name}»"),
        reply_markup=_back_kb(kind, entity_id),
    )

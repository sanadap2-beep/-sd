"""
لوحة الأدمن: قسم واتساب (الجسر مع البوت الثاني).

من هنا: تفعيل القسم، رابط سيرفر الجسر، مفتاح المصادقة، سعر اليوم،
وصف القسم الذي يظهر للمستخدمين، اختبار الاتصال، وقائمة المشتركين.

العقد التقني للجسر مشروح بالكامل في docs/whatsapp_bridge_v1_ar.md
والتنفيذ المرجعي في scripts/wa_bridge_reference_server.py.
"""

from __future__ import annotations

import html as html_module
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from sqlalchemy import desc, select

from database.models import User, WhatsAppSubscription
from filters.admin_filter import IsAdmin
from keyboards.ai_sections_admin import wa_admin_home_kb
from services.audit_service import AuditAction, AuditService
from services.settings_service import SettingsService
from services.whatsapp_bridge_service import (
    WABridgeClient,
    WABridgeError,
    WASettings,
)
from states.states import AdminWhatsAppStates

router = Router(name="admin_wa_bridge")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _render_home(callback: CallbackQuery) -> None:
    enabled = await WASettings.enabled()
    configured = await WASettings.configured()
    base_url = await WASettings.base_url()
    api_key = await WASettings.api_key()
    price = await WASettings.daily_price()
    masked = f"{api_key[:5]}…{api_key[-3:]}" if len(api_key or "") > 10 else ("مضبوط" if api_key else "—")
    bot_username = await WASettings.bot_username()

    lines = [
        "📱 <b>إدارة قسم واتساب</b>",
        "",
        f"الحالة: {'🟢 مفعّل' if enabled else '🔴 معطّل'} | الجسر: {'✅ مضبوط' if configured else '❌ غير مضبوط'}",
        f"🌐 الرابط: <code>{html_module.escape(base_url or '—')}</code>",
        f"🔑 المفتاح: <code>{masked}</code>",
        f"💵 سعر اليوم: <b>${price}</b>",
        f"🤖 البوت الثاني: @{bot_username or '—'}",
        "",
        "🧠 <b>كيف يعمل؟</b> هذا البوت يتصل بسيرفر جسر يديره البوت الثاني: "
        "يرسل رقم المستخدم ← يستلم كود اقتران ← يعرضه ← وبعد الربط يعرض "
        "أزرار البوت الثاني هنا ويمرر كل ضغطة إليه.",
        "📄 عقد الربط: docs/whatsapp_bridge_v1_ar.md",
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=wa_admin_home_kb(enabled, configured))
    await callback.answer()


@router.callback_query(F.data == "admin:wa")
async def wa_admin_home(callback: CallbackQuery):
    await _render_home(callback)


@router.callback_query(F.data == "admin:wa:toggle")
async def wa_admin_toggle(callback: CallbackQuery, session, db_user: User):
    new_value = not await WASettings.enabled()
    await SettingsService.set(session, "wa_bridge_enabled", "true" if new_value else "false")
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="setting",
        entity_name="wa_bridge_enabled",
        new_value={"enabled": new_value},
        description="تفعيل/تعطيل قسم واتساب",
        session=session,
    )
    await callback.answer("تم ✅")
    await _render_home(callback)


# ══════════════ إدخال القيم النصية ══════════════


async def _ask_value(
    callback: CallbackQuery, state: FSMContext, field_state, prompt: str, current: str
):
    await state.set_state(field_state)
    await callback.message.edit_text(f"{prompt}\n\nالقيمة الحالية: <code>{current}</code>\nأرسل <code>إلغاء</code> للتراجع.")
    await callback.answer()


@router.callback_query(F.data == "admin:wa:url")
async def wa_admin_url(callback: CallbackQuery, state: FSMContext):
    await _ask_value(
        callback, state, AdminWhatsAppStates.waiting_base_url,
        "🌐 أرسل رابط سيرفر الجسر (البوت الثاني) بدون / بالنهاية:\n"
        "<i>مثال: https://wa.example.com</i>",
        await WASettings.base_url(),
    )


@router.callback_query(F.data == "admin:wa:key")
async def wa_admin_key(callback: CallbackQuery, state: FSMContext):
    api_key = await WASettings.api_key()
    masked = f"{api_key[:5]}…{api_key[-3:]}" if len(api_key or "") > 10 else "—"
    await _ask_value(
        callback, state, AdminWhatsAppStates.waiting_api_key,
        "🔑 أرسل مفتاح مصادقة الجسر (يجب أن يطابق ما في سيرفر البوت الثاني).",
        masked,
    )


@router.callback_query(F.data == "admin:wa:price")
async def wa_admin_price(callback: CallbackQuery, state: FSMContext):
    await _ask_value(
        callback, state, AdminWhatsAppStates.waiting_daily_price,
        "💵 أرسل سعر يوم الاشتراك بالدولار:\n<i>مثال: 1</i>",
        str(await WASettings.daily_price()),
    )


@router.callback_query(F.data == "admin:wa:desc")
async def wa_admin_desc(callback: CallbackQuery, state: FSMContext):
    await _ask_value(
        callback, state, AdminWhatsAppStates.waiting_description,
        "📝 أرسل وصف قسم واتساب ومعلوماته (يظهر للمستخدمين في شاشة القسم).\nأرسل <code>-</code> لإزالة الوصف.",
        (await WASettings.description())[:200] or "—",
    )


@router.callback_query(F.data == "admin:wa:bot")
async def wa_admin_bot(callback: CallbackQuery, state: FSMContext):
    await _ask_value(
        callback, state, AdminWhatsAppStates.waiting_bot_username,
        "🤖 أرسل يوزر البوت الثاني (بدون @) ليظهر كزر احتياطي يفتح البوت مباشرة.\nأرسل <code>-</code> للإزالة.",
        await WASettings.bot_username() or "—",
    )


@router.message(AdminWhatsAppStates.waiting_base_url, F.text)
async def wa_save_url(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    if raw == "إلغاء":
        await state.clear()
        await message.answer("❌ تم التراجع.")
        return
    if not raw.startswith(("http://", "https://")):
        await message.answer("⚠️ الرابط يجب أن يبدأ بـ http:// أو https://")
        return
    await state.clear()
    await SettingsService.set(session, "wa_bridge_base_url", raw.rstrip("/"))
    await AuditService.log(admin_id=db_user.id, action=AuditAction.UPDATE, entity_type="setting",
                           entity_name="wa_bridge_base_url", description="تحديث رابط جسر واتساب", session=session)
    await message.answer("✅ تم حفظ الرابط.")


@router.message(AdminWhatsAppStates.waiting_api_key, F.text)
async def wa_save_key(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    if raw == "إلغاء":
        await state.clear()
        await message.answer("❌ تم التراجع.")
        return
    await state.clear()
    await SettingsService.set(session, "wa_bridge_api_key", raw)
    await AuditService.log(admin_id=db_user.id, action=AuditAction.UPDATE, entity_type="setting",
                           entity_name="wa_bridge_api_key", description="تحديث مفتاح جسر واتساب", session=session)
    await message.answer("✅ تم حفظ المفتاح.")


@router.message(AdminWhatsAppStates.waiting_daily_price, F.text)
async def wa_save_price(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip().replace("$", "")
    try:
        price = Decimal(raw)
        if price <= 0 or not price.is_finite():
            raise InvalidOperation
    except (InvalidOperation, TypeError):
        await message.answer("⚠️ أرسل رقماً موجباً. مثال: 1")
        return
    await state.clear()
    await SettingsService.set(session, "wa_daily_price", str(price))
    await AuditService.log(admin_id=db_user.id, action=AuditAction.UPDATE, entity_type="setting",
                           entity_name="wa_daily_price", new_value={"price": str(price)},
                           description="تحديث سعر يوم قسم واتساب", session=session)
    await message.answer(f"✅ سعر اليوم أصبح ${price}.")


@router.message(AdminWhatsAppStates.waiting_description, F.text)
async def wa_save_desc(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw == "إلغاء":
        await state.clear()
        await message.answer("❌ تم التراجع.")
        return
    await state.clear()
    await SettingsService.set(session, "wa_description", "" if raw == "-" else raw[:2000])
    await message.answer("✅ تم حفظ الوصف.")


@router.message(AdminWhatsAppStates.waiting_bot_username, F.text)
async def wa_save_bot(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip().lstrip("@")
    if raw == "إلغاء":
        await state.clear()
        await message.answer("❌ تم التراجع.")
        return
    await state.clear()
    await SettingsService.set(session, "wa_bot_username", "" if raw == "-" else raw[:64])
    await message.answer("✅ تم الحفظ.")


# ══════════════ اختبار الاتصال والمشتركون ══════════════


@router.callback_query(F.data == "admin:wa:test")
async def wa_admin_test(callback: CallbackQuery):
    if not await WASettings.configured():
        await callback.answer("اضبط الرابط والمفتاح أولاً.", show_alert=True)
        return
    try:
        health = await WABridgeClient.health()
    except WABridgeError as exc:
        await callback.answer(f"❌ {exc}", show_alert=True)
        return
    extra = {k: v for k, v in health.items() if k != "ok"}
    await callback.answer(
        f"✅ الجسر يستجيب! {extra if extra else ''}".strip(), show_alert=True
    )


@router.callback_query(F.data == "admin:wa:subs")
async def wa_admin_subs(callback: CallbackQuery, session):
    result = await session.execute(
        select(WhatsAppSubscription, User)
        .join(User, User.id == WhatsAppSubscription.user_id)
        .order_by(desc(WhatsAppSubscription.paid_until))
        .limit(20)
    )
    rows = result.all()
    if not rows:
        await callback.answer("لا يوجد مشتركون بعد.", show_alert=True)
        return
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lines = ["👥 <b>آخر 20 مشترك في قسم واتساب</b>\n"]
    for sub, user in rows:
        state_icon = "🟢" if sub.paid_until > now else "🔴"
        lines.append(
            f"{state_icon} {html_module.escape(user.full_name or '')} "
            f"(<code>{user.telegram_id}</code>) — حتى {sub.paid_until:%Y-%m-%d %H:%M} | دفع ${sub.total_paid}"
        )
    await callback.message.edit_text("\n".join(lines))
    await callback.answer()

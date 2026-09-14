"""
إدارة قسم واتساب من لوحة الأدمن.

- 📝 الوصف والسعر: وصف القسم (يظهر للمستخدم) + سعر اليوم + الباقات.
- 🔌 الجسر: عنوان الجسر + سره + اختبار اتصال.
- 👥 المشتركون: حالة كل مستخدم (رقم/ربط/انتهاء/تجديد تلقائي).
- 📊 إحصاءات: نشطين/مربوطين/إيراد 30 يوم.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import desc, select

from database.models import User, WaSubscription
from filters.admin_filter import IsAdmin
from services import wa_bridge_client
from services.feature_service import FeatureService
from services.settings_service import SettingsService
from services.whatsapp_section_service import WhatsAppSectionService
from states.states import AdminWaStates

router = Router(name="admin_whatsapp")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_PAGE_RE = re.compile(r"^admin:wa_users:(\d+)$")


def _mask(value: str) -> str:
    return f"{'*' * 6}{value[-4:]}" if value and len(value) > 8 else ("**" if value else "—")


async def _wa_configured() -> bool:
    url = (await SettingsService.get("wa_bridge_url") or "").strip()
    secret = (await SettingsService.get("wa_bridge_secret") or "").strip()
    return bool(url and secret)


# ══════════════ القائمة ══════════════


@router.callback_query(F.data == "admin:wa_sections")
async def wa_menu(callback: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="📝 الوصف والسعر", callback_data="admin:wa_desc", style="primary")
    b.button(text="🔌 الجسر (URL + سر)", callback_data="admin:wa_bridge")
    b.button(text="👥 المشتركون", callback_data="admin:wa_users:0")
    b.button(text="📊 الإحصاءات", callback_data="admin:wa_stats")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(2, 2, 1)
    await callback.message.edit_text(
        "📱 <b>إدارة قسم واتساب</b>\n\n"
        "هذا القسم مربوط ببوتك الثاني عبر «الجسر» (wa_bridge/bridge.py "
        "الذي يعمل بجانبه). من هنا تكتب وصف القسم وسعر الباقات، "
        "وتضبط عنوان الجسر وسره، وتراجع المشتركين.",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


# ══════════════ الوصف والسعر ══════════════


@router.callback_query(F.data == "admin:wa_desc")
async def wa_desc_start(callback: CallbackQuery, state: FSMContext):
    current = (await SettingsService.get("wa_section_description", "")) or "(لا يوجد)"
    await callback.message.edit_text(
        "📝 <b>وصف قسم واتساب</b>\n\n"
        "هذا النص يظهر للمستخدم داخل القسم — اشرح ما يقدّمه القسم و"
        "كيف يربط رقمه وماذا يفعل بعد الربط.\n"
        f"الحالي: {current}\n\n"
        "أرسل الوصف الجديد (أو <code>-</code> للإبقاء):"
    )
    await state.set_state(AdminWaStates.waiting_description)
    await callback.answer()


@router.message(AdminWaStates.waiting_description)
async def wa_desc_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        if len(raw) > 2000:
            await message.answer("⚠️ الوصف أطول من 2000 حرف — خلاصه وأعد الإرسال.")
            return
        await SettingsService.set(session, "wa_section_description", raw or "")
    await state.set_state(AdminWaStates.waiting_price)
    daily = await WhatsAppSectionService.daily_price()
    await message.answer(
        f"✓ الوصف حُفظ.\n\n"
        f"أرسل <b>سعر اليوم</b> بالدولار (الحالي: {daily:g}$ — يُستخدم "
        "للتجديد التلقائي وباقة اليوم):"
    )


@router.message(AdminWaStates.waiting_price)
async def wa_price_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw == "-":
        raw = "1"
    try:
        price = Decimal(raw)
        if price <= 0 or price > Decimal("100"):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ أرسل رقماً موجباً بالدولار (مثال: 1).")
        return
    await FeatureService.set_option(session, "whatsapp_section", "price_per_day_usd", float(price))
    await state.set_state(AdminWaStates.waiting_packages)
    await message.answer(
        "✓ السعر حُفظ.\n\n"
        "أرسل <b>الباقات</b> بصيغة JSON (قائمة أيام + سعر)، أو <code>-</code> "
        "للإبقاء على الحالية:\n\n"
        "<code>"
        + json.dumps(
            [
                {"days": 1, "price_usd": 1.0},
                {"days": 3, "price_usd": 2.85},
                {"days": 7, "price_usd": 6.30},
                {"days": 30, "price_usd": 25.50},
            ],
            ensure_ascii=False,
        )
        + "</code>"
    )


@router.message(AdminWaStates.waiting_packages)
async def wa_packages_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        try:
            data = json.loads(raw)
            if not isinstance(data, list) or not data:
                raise ValueError("قائمة فارغة")
            for item in data:
                int(item["days"])
                Decimal(str(item["price_usd"]))
        except (ValueError, KeyError, TypeError) as exc:
            await message.answer(f"⚠️ JSON غير صالح: {exc}\nأعد الإرسال أو أرسل - للإبقاء.")
            return
        await FeatureService.set_option(session, "whatsapp_section", "packages_json", data)
    await state.clear()
    packages = WhatsAppSectionService.packages()
    lines = ["📦 <b>الباقات الحالية</b>:\n"]
    for pkg in packages:
        lines.append(f"• {pkg['days']} يوم = {pkg['price_usd']:g}$")
    lines.append("\n✓ تم حفظ كل شيء.")
    b = InlineKeyboardBuilder()
    b.button(text="🔙", callback_data="admin:wa_sections")
    await message.answer("\n".join(lines), reply_markup=b.as_markup())


# ══════════════ الجسر ══════════════


@router.callback_query(F.data == "admin:wa_bridge")
async def wa_bridge_screen(callback: CallbackQuery):
    from config import settings as cfg

    url = (await SettingsService.get("wa_bridge_url") or "").strip() or cfg.WA_BRIDGE_URL
    secret = (await SettingsService.get("wa_bridge_secret") or "").strip() or cfg.WA_BRIDGE_SECRET
    b = InlineKeyboardBuilder()
    b.button(text="✏️ عنوان الجسر (URL)", callback_data="admin:wa_bridge_url")
    b.button(text="🔑 سر الجسر (SECRET)", callback_data="admin:wa_bridge_secret")
    b.button(
        text="🧪 اختبار الاتصال",
        callback_data="admin:wa_bridge_test",
        style="success" if url and secret else "danger",
    )
    b.button(text="🔙", callback_data="admin:wa_sections")
    b.adjust(1)
    await callback.message.edit_text(
        "🔌 <b>جسر واتساب</b>\n\n"
        f"العنوان: <code>{url or '—'}</code>\n"
        f"السّر: <code>{_mask(secret)}</code>\n\n"
        "الجسر هو الخدمة التي تعمل <b>بجانب البوت الثاني</b> "
        "(wa_bridge/bridge.py في هذا المستودع). القيم هنا (لوحة الأدمن) "
        "تسبق قيم ملف البيئة WA_BRIDGE_URL / WA_BRIDGE_SECRET.",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:wa_bridge_url")
async def wa_bridge_url_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "✏️ أرسل عنوان الجسر (مثال: <code>http://10.0.0.5:8090</code>)\n"
        "أرسل <code>-</code> للإبقاء على الحالي:"
    )
    await state.set_state(AdminWaStates.waiting_bridge_url)
    await callback.answer()


@router.message(AdminWaStates.waiting_bridge_url)
async def wa_bridge_url_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        if not re.match(r"^https?://[\w.-]+(:\d+)?(/[\w./-]*)?$", raw):
            await message.answer("⚠️ أرسل رابطاً صحيحاً (http/https + منفذ اختياري).")
            return
        await SettingsService.set(session, "wa_bridge_url", raw.rstrip("/"))
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="🔙", callback_data="admin:wa_bridge")
    await message.answer("✓ تم.", reply_markup=b.as_markup())


@router.callback_query(F.data == "admin:wa_bridge_secret")
async def wa_bridge_secret_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔑 أرسل سر الجسر (يستخدم في Authorization: Bearer ...).\n"
        "يجب أن يكون مطابقاً لـ BRIDGE_SECRET في خدمة الجسر.\n"
        "أرسل <code>-</code> للإبقاء على الحالي:"
    )
    await state.set_state(AdminWaStates.waiting_bridge_secret)
    await callback.answer()


@router.message(AdminWaStates.waiting_bridge_secret)
async def wa_bridge_secret_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        if len(raw) < 8:
            await message.answer("⚠️ السّر قصير — استخدم قيمة عشوائية أطول (16+ حرفاً).")
            return
        await SettingsService.set(session, "wa_bridge_secret", raw)
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="🔙", callback_data="admin:wa_bridge")
    await message.answer("✓ تم حفظ السّر.", reply_markup=b.as_markup())


@router.callback_query(F.data == "admin:wa_bridge_test")
async def wa_bridge_test(callback: CallbackQuery):
    url = (await SettingsService.get("wa_bridge_url") or "").strip()
    if not url:
        from config import settings as cfg

        url = cfg.WA_BRIDGE_URL
    if not url:
        await callback.answer("ضع عنوان الجسر أولاً.", show_alert=True)
        return
    await callback.answer("جارٍ الاختبار...")
    try:
        ok = await wa_bridge_client.ping()
    except wa_bridge_client.WaBridgeError as exc:
        await callback.message.answer(f"❌ <b>فشل الاختبار</b>\n\n{exc}")
        return
    if ok:
        await callback.message.answer("✅ <b>الجسر يرد</b> — الربط جاهز.")
    else:
        await callback.message.answer("❌ <b>الجسر لا يرد</b> — تأكد من التشغيل والعنوان.")


# ══════════════ المشتركون ══════════════


@router.callback_query(_PAGE_RE)
async def wa_users(callback: CallbackQuery, session):
    page = int(callback.data.split(":")[2])
    per_page = 15
    result = await session.execute(
        select(WaSubscription).order_by(desc(WaSubscription.updated_at)).limit(per_page).offset(page * per_page)
    )
    subs = list(result.scalars().all())
    lines = [f"👥 <b>مشتركو واتساب</b> (صفحة {page + 1})\n"]
    if not subs:
        lines.append("لا يوجد مشتركون بعد.")
    now = datetime.utcnow()
    for sub in subs:
        user = await session.get(User, sub.user_id)
        name = (user.full_name or user.username or str(user.telegram_id)) if user else "?"
        active = sub.active_until and sub.active_until > now
        mark = "🟢" if active else "🔴"
        linked = {"none": "—", "pending": "⏳", "linked": "🔗", "expired": "💀"}.get(
            sub.link_state, "?"
        )
        renew = "تجديد تلقائي ✓" if sub.auto_renew else ""
        until = _fmt(sub.active_until)
        lines.append(
            f"{mark} {name} ({user.telegram_id if user else sub.user_id})\n"
            f"   {sub.phone or 'بلا رقم'} · ربط: {linked} · "
            f"حتى: {until} · {renew}\n"
        )
    b = InlineKeyboardBuilder()
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"admin:wa_users:{page - 1}")
    if len(subs) == per_page:
        b.button(text="التالي ▶️", callback_data=f"admin:wa_users:{page + 1}")
    b.button(text="🔙", callback_data="admin:wa_sections")
    b.adjust(3, 1)
    await callback.message.edit_text("\n".join(lines), reply_markup=b.as_markup())
    await callback.answer()


def _fmt(until) -> str:
    return until.strftime("%Y-%m-%d %H:%M") if until else "—"


# ══════════════ الإحصاءات ══════════════


@router.callback_query(F.data == "admin:wa_stats")
async def wa_stats(callback: CallbackQuery, session):
    stats = await WhatsAppSectionService.stats(session)
    await callback.message.edit_text(
        "📊 <b>إحصاءات قسم واتساب</b>\n\n"
        f"• اشتراكات نشطة: {stats['active']}\n"
        f"• جلسات مربوطة: {stats['linked']}\n"
        f"• إيراد آخر 30 يوم: {stats['revenue_30d']:g}$"
    )
    await callback.answer()

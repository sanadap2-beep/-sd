"""
إدارة الدول من لوحة الأدمن.
يدعم مزودي الأرقام الأربعة (HeroSMS, 5sim, SMS-Activate, SMSHub)،
مع دعم السحب التلقائي، الإضافة اليدوية، والتهيئة الكاملة.
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import delete as sql_delete, func, select

from config import settings
from database.models import Country, ServicePricing
from providers.countries import get_all_countries
from providers.fivesim import FiveSimProvider
from services.herosms_sync_service import sync_herosms_countries
from services.country_sync_service import sync_fivesim_countries, sync_grizzly_countries
from services.price_cache_service import PriceCacheService
from states.states import AdminCountryStates
from keyboards.admin import (
    ADMIN_COUNTRIES_PER_PAGE,
    admin_countries_kb,
    admin_country_detail_kb,
    admin_back_kb,
    country_reset_confirm_kb,
    fivesim_sync_menu_kb,
    grizzly_sync_menu_kb,
    herosms_sync_menu_kb,
)
from filters.admin_filter import IsAdmin

router = Router(name="admin_countries")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════════════════════════════════════
# ══════════════ عرض قائمة الدول ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "admin:countries")
async def countries_list(callback: CallbackQuery, session):
    await _show_countries_list(callback, session, page=0)


@router.callback_query(F.data.startswith("admin:countries:"))
async def countries_list_page(callback: CallbackQuery, session):
    """تنقل بين صفحات الدول."""
    try:
        page = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0
    await _show_countries_list(callback, session, page=page)


async def _show_countries_list(callback: CallbackQuery, session, page: int = 0):
    """يعرض قائمة الدول مع ترقيم صفحات وأزرار الإدارة."""
    countries = await get_all_countries(session)
    total_pages = max(
        1, (len(countries) + ADMIN_COUNTRIES_PER_PAGE - 1) // ADMIN_COUNTRIES_PER_PAGE
    )

    text = (
        "🌍 <b>إدارة الدول</b>\n\n"
        f"📊 العدد الكلي: <b>{len(countries)}</b> دولة"
        + (f" · صفحة {page + 1}/{total_pages}" if total_pages > 1 else "")
        + "\n\n🟢 = مفعّلة | ⚪ = معطّلة\n\n"
    )
    if countries:
        text += "اضغط على أي دولة لتعديلها أو استخدم أزرار التحكم بالأسفل:"
    else:
        text += "لا توجد أي دولة مضافة حالياً. اضغط على <b>🔄 سحب دول من HeroSMS</b> لجلبها."

    await callback.message.edit_text(
        text, reply_markup=admin_countries_kb(countries, page)
    )


# ══════════════════════════════════════════════
# ══════════════ إضافة دولة يدوية ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "admin:country_add")
async def country_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ <b>إضافة دولة جديدة يدوياً</b>\n\n"
        "أرسل معرّفاً داخلياً بالإنجليزية (بدون مسافات):\n"
        "(مثال: <code>egypt</code> أو <code>saudi_arabia</code>)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminCountryStates.waiting_code)


@router.message(AdminCountryStates.waiting_code)
async def country_code_received(message: Message, state: FSMContext, session):
    code = message.text.strip().lower().replace(" ", "_")
    existing = await session.execute(select(Country).where(Country.code == code))
    if existing.scalar_one_or_none():
        await message.answer("⚠️ يوجد دولة بهذا المعرّف مسبقاً. أرسل معرّفاً آخر:")
        return
    await state.update_data(code=code)
    await message.answer("✏️ أرسل اسم الدولة بالعربي:\n(مثال: مصر)")
    await state.set_state(AdminCountryStates.waiting_name_ar)


@router.message(AdminCountryStates.waiting_name_ar)
async def country_name_received(message: Message, state: FSMContext):
    await state.update_data(name_ar=message.text.strip())
    await message.answer("🚩 أرسل علم الدولة (إيموجي):\n(أو أرسل - للتخطي واستخدام 🌍)")
    await state.set_state(AdminCountryStates.waiting_flag)


@router.message(AdminCountryStates.waiting_flag)
async def country_flag_received(message: Message, state: FSMContext):
    flag = message.text.strip()
    await state.update_data(flag=None if flag == "-" else flag)
    await message.answer("🔢 أرسل كود الدولة لدى <b>5sim</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminCountryStates.waiting_fivesim_code)


@router.message(AdminCountryStates.waiting_fivesim_code)
async def country_fivesim_received(message: Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(fivesim_code=None if val == "-" else val)
    await message.answer("🔢 أرسل كود الدولة الرقمي لدى <b>HeroSMS</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminCountryStates.waiting_herosms_code)


@router.message(AdminCountryStates.waiting_herosms_code)
async def country_herosms_received(message: Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(herosms_code=None if val == "-" else val)
    await message.answer("🔢 أرسل كود الدولة لدى <b>SMS-Activate</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminCountryStates.waiting_sms_activate_code)


@router.message(AdminCountryStates.waiting_sms_activate_code)
async def country_sms_activate_received(message: Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(sms_activate_code=None if val == "-" else val)
    await message.answer("🔢 أرسل كود الدولة لدى <b>SMSHub</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminCountryStates.waiting_smshub_code)


@router.message(AdminCountryStates.waiting_smshub_code)
async def country_smshub_received(message: Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(smshub_code=None if val == "-" else val)
    await message.answer("🔢 أرسل كود الدولة لدى <b>SMSPool</b> (مثال: <code>US</code>):\n(أو أرسل - للتخطي)")
    await state.set_state(AdminCountryStates.waiting_smspool_code)


@router.message(AdminCountryStates.waiting_smspool_code)
async def country_smspool_received(message: Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(smspool_code=None if val == "-" else val)
    await message.answer("🔢 أرسل كود الدولة الرقمي لدى <b>GrizzlySMS</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminCountryStates.waiting_grizzly_code)


@router.message(AdminCountryStates.waiting_grizzly_code)
async def country_grizzly_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    val = message.text.strip()
    data = await state.get_data()

    fivesim_code = data.get("fivesim_code")
    herosms_code = data.get("herosms_code")
    sms_activate_code = data.get("sms_activate_code")
    smshub_code = data.get("smshub_code")
    smspool_code = data.get("smspool_code")
    grizzly_code = None if val == "-" else val

    if not any([fivesim_code, herosms_code, sms_activate_code, smshub_code, smspool_code, grizzly_code]):
        await message.answer("⚠️ يجب تحديد كود لمزود واحد على الأقل.")
        await state.clear()
        return

    country = Country(
        code=data["code"],
        name_ar=data["name_ar"],
        flag=data.get("flag") or "🌍",
        fivesim_code=fivesim_code,
        herosms_code=herosms_code,
        sms_activate_code=sms_activate_code,
        smshub_code=smshub_code,
        smspool_code=smspool_code,
        grizzly_code=grizzly_code,
        is_active=False,
        added_by_admin_id=db_user.id,
    )
    session.add(country)
    await session.commit()

    await message.answer(
        f"✅ تمت إضافة الدولة {country.flag} {country.name_ar} بنجاح.\n"
        "يمكنك تفعيلها الآن من قائمة إدارة الدول."
    )
    await state.clear()


# ══════════════════════════════════════════════
# ══════════════ تفاصيل وتعديل دولة ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:country_view:"))
async def country_view(callback: CallbackQuery, session):
    country_id = int(callback.data.split(":")[2])
    country = await session.get(Country, country_id)
    if not country:
        await callback.answer("⚠️ الدولة غير موجودة.", show_alert=True)
        return

    status = "🟢 مفعّلة" if country.is_active else "⚪ معطّلة"
    await callback.message.edit_text(
        f"{country.flag} <b>{country.name_ar}</b>\n\n"
        f"المعرّف الداخلي: <code>{country.code}</code>\n"
        f"كود HeroSMS: <code>{country.herosms_code or '—'}</code>\n"
        f"كود 5sim: <code>{country.fivesim_code or '—'}</code>\n"
        f"كود SMS-Activate: <code>{country.sms_activate_code or '—'}</code>\n"
        f"كود SMSHub: <code>{country.smshub_code or '—'}</code>\n"
        f"كود SMSPool: <code>{getattr(country, 'smspool_code', None) or '—'}</code>\n"
        f"كود GrizzlySMS: <code>{getattr(country, 'grizzly_code', None) or '—'}</code>\n"
        f"الحالة: {status}\n"
        f"الترتيب: {country.sort_order}",
        reply_markup=admin_country_detail_kb(country),
    )


@router.callback_query(F.data.startswith("admin:country_toggle:"))
async def country_toggle(callback: CallbackQuery, session):
    country_id = int(callback.data.split(":")[2])
    country = await session.get(Country, country_id)
    if not country:
        await callback.answer("⚠️ غير موجودة.", show_alert=True)
        return
    country.is_active = not country.is_active
    await session.commit()
    await callback.answer("✅ تم تغيير الحالة.")
    await country_view(callback, session)


@router.callback_query(F.data.startswith("admin:country_delete:"))
async def country_delete(callback: CallbackQuery, session):
    country_id = int(callback.data.split(":")[2])
    country = await session.get(Country, country_id)
    if country:
        await session.delete(country)
        await session.commit()
    await callback.answer("🗑 تم الحذف بنجاح.")
    await countries_list(callback, session)


@router.callback_query(F.data == "admin:country_reference_list")
async def country_reference_list(callback: CallbackQuery):
    await callback.answer("⏳ جاري جلب الأكواد...")
    try:
        provider = FiveSimProvider()
        countries = await provider.list_countries()
        text = "🌍 <b>أكواد دول 5sim المرجعية:</b>\n\n" + "\n".join(countries[:40])
    except Exception as e:
        text = f"⚠️ تعذّر الجلب: {e}"
    await callback.message.answer(text)


# ══════════════════════════════════════════════
# ══════════════ سحب الدول من HeroSMS ══════════════
# ══════════════════════════════════════════════

_HEROSMS_SERVICE_LABELS = {
    "whatsapp": "💬 واتساب",
    "telegram": "✈️ تيليجرام",
}


@router.callback_query(F.data == "admin:country_sync_herosms")
async def country_sync_herosms_menu(callback: CallbackQuery):
    """قائمة خيارات السحب من HeroSMS."""
    await callback.answer()
    await callback.message.edit_text(
        "🔄 <b>سحب الدول من HeroSMS</b>\n\n"
        "سيقوم البوت بسحب كتالوج الدول وفحص توفر الأرقام والمخزون،\n"
        "ثم تعريب الأسماء وتفعيل الدول المتوفرة تلقائياً.\n\n"
        "اختر الخدمة المطلوبة للسحب:",
        reply_markup=herosms_sync_menu_kb(),
    )


def _parse_sync_services(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


async def _run_herosms_sync(
    callback: CallbackQuery,
    session,
    services_raw: str,
    activate: bool,
):
    if not settings.HEROSMS_API_KEY:
        await callback.answer(
            "⚠️ لا يوجد HEROSMS_API_KEY مضبوط في الإعدادات.",
            show_alert=True,
        )
        return

    wanted = _parse_sync_services(services_raw)
    labels = " + ".join(_HEROSMS_SERVICE_LABELS.get(code, code) for code in wanted)
    await callback.answer("⏳ بدأ السحب... يرجى الانتظار.")

    status_text = (
        f"⏳ <b>جاري سحب وفحص الدول من HeroSMS...</b>\n\n"
        f"الخدمات: {labels}\n"
        f"التفعيل التلقائي: {'🟢 نعم' if activate else '⚪ لا'}\n\n"
        "قد تستغرق العملية بضع ثوانٍ..."
    )
    try:
        await callback.message.edit_text(status_text)
    except Exception:
        await callback.message.answer(status_text)

    try:
        report = await sync_herosms_countries(
            session,
            wanted_services=wanted,
            activate=activate,
        )
        text = report.summary()
    except Exception as e:
        text = f"❌ <b>فشل السحب من HeroSMS:</b>\n\n<code>{type(e).__name__}: {e}</code>"

    countries = await get_all_countries(session)
    try:
        await callback.message.edit_text(text, reply_markup=admin_countries_kb(countries))
    except Exception:
        await callback.message.answer(text, reply_markup=admin_countries_kb(countries))


@router.callback_query(F.data.startswith("admin:country_sync_idle:"))
async def country_sync_herosms_idle(callback: CallbackQuery, session):
    """سحب الدول مع إبقائها معطلة."""
    services_raw = callback.data.split(":", 2)[2]
    await _run_herosms_sync(callback, session, services_raw, activate=False)


@router.callback_query(F.data.startswith("admin:country_sync:"))
async def country_sync_herosms_go(callback: CallbackQuery, session):
    """سحب الدول مع تفعيل المتاح منها تلقائياً."""
    services_raw = callback.data.split(":", 2)[2]
    await _run_herosms_sync(callback, session, services_raw, activate=True)


# ══════════════════════════════════════════════
# ══════════════ سحب الدول من 5sim ══════════════
# ══════════════════════════════════════════════

_FIVESIM_SERVICE_LABELS = {
    "whatsapp": "💬 واتساب",
    "telegram": "✈️ تيليجرام",
}


@router.callback_query(F.data == "admin:country_sync_fivesim_menu")
async def country_sync_fivesim_menu(callback: CallbackQuery):
    """قائمة خيارات السحب من 5sim."""
    await callback.answer()
    await callback.message.edit_text(
        "🟢 <b>سحب الدول من 5sim</b>\n\n"
        "سيقوم البوت بسحب كتالوج الدول وفحص توفر الأرقام والمخزون،\n"
        "ثم تعريب الأسماء وتفعيل الدول المتوفرة تلقائياً.\n"
        "الدول الموجودة مسبقاً (من HeroSMS مثلاً) تُدمج ولا تتكرر.\n\n"
        "اختر الخدمة المطلوبة للسحب:",
        reply_markup=fivesim_sync_menu_kb(),
    )


async def _run_fivesim_sync(
    callback: CallbackQuery,
    session,
    services_raw: str,
    activate: bool,
):
    wanted = [part.strip() for part in services_raw.split(",") if part.strip()]
    labels = " + ".join(_FIVESIM_SERVICE_LABELS.get(code, code) for code in wanted)
    await callback.answer("⏳ بدأ السحب... يرجى الانتظار.")

    status_text = (
        "⏳ <b>جاري سحب وفحص الدول من 5sim...</b>\n\n"
        f"الخدمات: {labels}\n"
        f"التفعيل التلقائي: {'🟢 نعم' if activate else '⚪ لا'}\n\n"
        "قد تستغرق العملية بضع دقائق (فحص كل دولة لواتساب وتيليجرام)..."
    )
    try:
        await callback.message.edit_text(status_text)
    except Exception:
        await callback.message.answer(status_text)

    try:
        report = await sync_fivesim_countries(
            session,
            wanted_services=wanted,
            activate=activate,
        )
        text = report.summary()
    except Exception as e:
        text = f"❌ <b>فشل السحب من 5sim:</b>\n\n<code>{type(e).__name__}: {e}</code>"

    countries = await get_all_countries(session)
    try:
        await callback.message.edit_text(text, reply_markup=admin_countries_kb(countries))
    except Exception:
        await callback.message.answer(text, reply_markup=admin_countries_kb(countries))


@router.callback_query(F.data.startswith("admin:country_sync_fivesim_idle:"))
async def country_sync_fivesim_idle(callback: CallbackQuery, session):
    """سحب الدول مع إبقائها معطلة."""
    services_raw = callback.data.split(":", 2)[2]
    await _run_fivesim_sync(callback, session, services_raw, activate=False)


@router.callback_query(F.data.startswith("admin:country_sync_fivesim:"))
async def country_sync_fivesim_go(callback: CallbackQuery, session):
    """سحب الدول مع تفعيل المتاح منها تلقائياً."""
    services_raw = callback.data.split(":", 2)[2]
    await _run_fivesim_sync(callback, session, services_raw, activate=True)


# ══════════════════════════════════════════════
# ══════════════ سحب الدول من GrizzlySMS ══════════════
# ══════════════════════════════════════════════

_GRIZZLY_SERVICE_LABELS = {
    "whatsapp": "💬 واتساب",
    "telegram": "✈️ تيليجرام",
}


@router.callback_query(F.data == "admin:country_sync_grizzly_menu")
async def country_sync_grizzly_menu(callback: CallbackQuery):
    """قائمة خيارات السحب من GrizzlySMS."""
    await callback.answer()
    await callback.message.edit_text(
        "🐻 <b>سحب الدول من GrizzlySMS</b>\n\n"
        "سيقوم البوت بسحب كتالوج الدول وفحص توفر الأرقام والمخزون،\n"
        "ثم تعريب الأسماء وتفعيل الدول المتوفرة تلقائياً.\n"
        "الدول الموجودة مسبقاً (من HeroSMS أو 5sim) تُدمج ولا تتكرر.\n\n"
        "اختر الخدمة المطلوبة للسحب:",
        reply_markup=grizzly_sync_menu_kb(),
    )


async def _run_grizzly_sync(
    callback: CallbackQuery,
    session,
    services_raw: str,
    activate: bool,
):
    if not settings.GRIZZLY_API_KEY:
        await callback.answer(
            "⚠️ لا يوجد GRIZZLY_API_KEY مضبوط في الإعدادات.",
            show_alert=True,
        )
        return

    wanted = [part.strip() for part in services_raw.split(",") if part.strip()]
    labels = " + ".join(_GRIZZLY_SERVICE_LABELS.get(code, code) for code in wanted)
    await callback.answer("⏳ بدأ السحب... يرجى الانتظار.")

    status_text = (
        "⏳ <b>جاري سحب وفحص الدول من GrizzlySMS...</b>\n\n"
        f"الخدمات: {labels}\n"
        f"التفعيل التلقائي: {'🟢 نعم' if activate else '⚪ لا'}\n\n"
        "قد تستغرق العملية بضع دقائق (فحص كل دولة لواتساب وتيليجرام)..."
    )
    try:
        await callback.message.edit_text(status_text)
    except Exception:
        await callback.message.answer(status_text)

    try:
        report = await sync_grizzly_countries(
            session,
            wanted_services=wanted,
            activate=activate,
        )
        text = report.summary()
    except Exception as e:
        text = f"❌ <b>فشل السحب من GrizzlySMS:</b>\n\n<code>{type(e).__name__}: {e}</code>"

    countries = await get_all_countries(session)
    try:
        await callback.message.edit_text(text, reply_markup=admin_countries_kb(countries))
    except Exception:
        await callback.message.answer(text, reply_markup=admin_countries_kb(countries))


@router.callback_query(F.data.startswith("admin:country_sync_grizzly_idle:"))
async def country_sync_grizzly_idle(callback: CallbackQuery, session):
    """سحب الدول مع إبقائها معطلة."""
    services_raw = callback.data.split(":", 2)[2]
    await _run_grizzly_sync(callback, session, services_raw, activate=False)


@router.callback_query(F.data.startswith("admin:country_sync_grizzly:"))
async def country_sync_grizzly_go(callback: CallbackQuery, session):
    """سحب الدول مع تفعيل المتاح منها تلقائياً."""
    services_raw = callback.data.split(":", 2)[2]
    await _run_grizzly_sync(callback, session, services_raw, activate=True)


# ══════════════════════════════════════════════
# ══════════════ حذف / تصفير جميع الدول ══════════════
# ══════════════════════════════════════════════


async def _reset_synced_countries(session) -> int:
    """حذف كل الدول وقواعد التسعير ومسح الكاش."""
    result = await session.execute(select(Country))
    targets = list(result.scalars().all())
    if not targets:
        return 0

    codes = [c.code for c in targets]
    await session.execute(
        sql_delete(ServicePricing).where(ServicePricing.country_code.in_(codes))
    )
    await session.execute(
        sql_delete(Country).where(Country.id.in_([c.id for c in targets]))
    )
    await session.commit()

    try:
        await PriceCacheService.invalidate("number-price:")
    except Exception:
        pass

    return len(targets)


@router.callback_query(F.data.in_(["admin:country_reset", "admin:country_delete_all_confirm"]))
async def country_reset_confirm(callback: CallbackQuery, session):
    total = (await session.execute(select(func.count(Country.id)))).scalar_one()
    await callback.answer()
    await callback.message.edit_text(
        "🗑 <b>تصفير وحذف جميع الدول</b>\n\n"
        f"سيتم حذف جميع الدول المسجلة في البوت حالياً (عددها: <b>{total}</b> دولة).\n"
        "✅ لن تتأثر أرصدة المستخدمين ولا سجل الطلبات إطلاقاً.\n\n"
        "هل أنت متأكد من الحذف؟",
        reply_markup=country_reset_confirm_kb(),
    )


@router.callback_query(F.data.in_(["admin:country_reset_go", "admin:country_delete_all_execute"]))
async def country_reset_go(callback: CallbackQuery, session):
    await callback.answer("⏳ جاري الحذف...")
    deleted = await _reset_synced_countries(session)
    await callback.message.edit_text(
        f"✅ تم حذف وتصفير <b>{deleted}</b> دولة بنجاح.\n\n"
        "يمكنك الآن سحب الدول من جديد عبر زر السحب أدناه:",
        reply_markup=herosms_sync_menu_kb(),
    )
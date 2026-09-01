"""
إدارة الدول من لوحة الأدمن.
يدعم 4 مزودين الآن بدل 2.
"""

from aiogram import Router, F
from decimal import Decimal
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete as sql_delete, func, select

from config import settings
from database.models import Country, NumberService, ProviderName
from providers.countries import get_all_countries, get_number_service_by_code
from providers.fivesim import FiveSimProvider
from services.herosms_sync_service import sync_herosms_countries
from states.states import AdminCountryStates
from keyboards.admin import (
    ADMIN_COUNTRIES_PER_PAGE,
    admin_countries_kb,
    admin_country_detail_kb,
    admin_back_kb,
    country_reset_confirm_kb,
    herosms_sync_menu_kb,
)
from filters.admin_filter import IsAdmin

router = Router(name="admin_countries")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


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
    """يعرض قائمة الدول مع ترقيم صفحات وأزرار الإدارة ظاهرة دائماً."""
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
        text += "اضغط على أي دولة لتعديلها."
    else:
        text += "لا توجد أي دولة مضافة بعد."
    await callback.message.edit_text(
        text, reply_markup=admin_countries_kb(countries, page)
    )


@router.callback_query(F.data == "admin:country_add")
async def country_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ <b>إضافة دولة جديدة</b>\n\nأرسل معرّفاً داخلياً بالإنجليزية:\n(مثال: <code>egypt</code>)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminCountryStates.waiting_code)


@router.message(AdminCountryStates.waiting_code)
async def country_code_received(message: Message, state: FSMContext, session):
    code = message.text.strip().lower().replace(" ", "_")
    existing = await session.execute(select(Country).where(Country.code == code))
    if existing.scalar_one_or_none():
        await message.answer("⚠️ يوجد دولة بهذا المعرّف. أرسل معرّفاً آخر.")
        return
    await state.update_data(code=code)
    await message.answer("✏️ أرسل اسم الدولة بالعربي:")
    await state.set_state(AdminCountryStates.waiting_name_ar)


@router.message(AdminCountryStates.waiting_name_ar)
async def country_name_received(message: Message, state: FSMContext):
    await state.update_data(name_ar=message.text.strip())
    await message.answer("🚩 أرسل علم الدولة (إيموجي):\n(أو أرسل - للتخطي)")
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
    await message.answer("🔢 أرسل كود الدولة لدى <b>HeroSMS</b>:\n(أو أرسل - للتخطي)")
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
async def country_smshub_received(
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
    smshub_code = None if val == "-" else val

    if not any([fivesim_code, herosms_code, sms_activate_code, smshub_code]):
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
        is_active=False,
        added_by_admin_id=db_user.id,
    )
    session.add(country)
    await session.commit()

    await message.answer(
        f"✅ تمت إضافة الدولة {country.flag} "
        f"{country.name_ar} (معطّلة حالياً).\n"
        "فعّلها من قائمة الدول عندما تكون جاهزاً."
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin:country_view:"))
async def country_view(callback: CallbackQuery, session):
    country_id = int(callback.data.split(":")[2])
    country = await session.get(Country, country_id)
    if not country:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    status = "🟢 مفعّلة" if country.is_active else "⚪ معطّلة"

    # أسعار التكلفة اليدوية لواتساب/تيليجرام مع سعر البيع الناتج
    from services.pricing_service import PricingService

    price_lines = []
    for svc_code, svc_emoji in (("whatsapp", "💬"), ("telegram", "✈️")):
        manual = await PricingService.get_manual_cost(session, svc_code, country.code)
        if manual is not None:
            margin_type, margin_value = await PricingService.get_margin(
                session, svc_code, country.code, ProviderName.HEROSMS
            )
            sell = PricingService.apply_margin(manual, margin_type, margin_value)
            price_lines.append(
                f"{svc_emoji} {svc_code}: تكلفة <b>{manual}$</b> → "
                f"بيع <b>{sell}$</b> (ربح {margin_value}%)"
            )
        else:
            price_lines.append(f"{svc_emoji} {svc_code}: سعر تلقائي من المزود")
    prices_text = "\n".join(price_lines)

    await callback.message.edit_text(
        f"{country.flag} <b>{country.name_ar}</b>\n\n"
        f"المعرّف: <code>{country.code}</code>\n"
        f"الحالة: {status}\n"
        f"5sim: <code>{country.fivesim_code or '—'}</code>\n"
        f"HeroSMS: <code>{country.herosms_code or '—'}</code>\n"
        f"SMS-Activate: <code>{country.sms_activate_code or '—'}</code>\n"
        f"SMSHub: <code>{country.smshub_code or '—'}</code>\n"
        f"الترتيب: {country.sort_order}\n\n"
        f"💰 <b>الأسعار:</b>\n{prices_text}",
        reply_markup=admin_country_detail_kb(country),
    )


@router.callback_query(F.data.startswith("admin:country_toggle:"))
async def country_toggle(callback: CallbackQuery, session):
    country_id = int(callback.data.split(":")[2])
    country = await session.get(Country, country_id)
    if not country:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    country.is_active = not country.is_active
    await session.commit()
    await callback.answer("✅ تم التحديث.")
    await country_view(callback, session)


@router.callback_query(F.data.startswith("admin:country_delete:"))
async def country_delete(callback: CallbackQuery, session):
    country_id = int(callback.data.split(":")[2])
    country = await session.get(Country, country_id)
    if not country:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    await session.delete(country)
    await session.commit()
    await callback.answer("🗑 تم الحذف.")
    await countries_list(callback, session)


@router.callback_query(F.data == "admin:country_reference_list")
async def country_reference_list(callback: CallbackQuery):
    await callback.answer("⏳ جاري الجلب...")
    try:
        provider = FiveSimProvider()
        countries = await provider.list_countries()
        text = "🌍 <b>أكواد دول 5sim:</b>\n\n" + "\n".join(countries[:40])
    except Exception as e:
        text = f"⚠️ تعذّر الجلب: {e}"
    await callback.message.answer(text)


# ══════════════════════════════════════════════
# ══════════════ التسعير اليدوي للدول ══════════════
# ══════════════════════════════════════════════


async def _load_price_context(session, data: dict):
    """يجلب الدولة والخدمة من بيانات حالة التسعير اليدوي."""
    country = await session.get(Country, data.get("price_country") or 0)
    service = None
    service_code = data.get("price_service")
    if service_code:
        service = await get_number_service_by_code(session, service_code)
    return country, service


@router.callback_query(F.data.startswith("admin:country_price:"))
async def country_price_start(callback: CallbackQuery, state: FSMContext, session):
    """يبدأ ضبط التكلفة اليدوية لخدمة داخل دولة."""
    parts = callback.data.split(":")
    service_code = parts[2]
    country_id = int(parts[3])

    country = await session.get(Country, country_id)
    if not country:
        await callback.answer("⚠️ الدولة غير موجودة.", show_alert=True)
        return
    service = await get_number_service_by_code(session, service_code)
    if service is None:
        await callback.answer("⚠️ الخدمة غير موجودة.", show_alert=True)
        return

    from services.pricing_service import PricingService

    current = await PricingService.get_manual_cost(session, service_code, country.code)
    margin_type, margin_value = await PricingService.get_margin(
        session, service_code, country.code, ProviderName.HEROSMS
    )

    await callback.answer()
    await callback.message.edit_text(
        f"💰 <b>تسعير يدوي: {service.emoji} {service.name_ar} — {country.flag} {country.name_ar}</b>\n\n"
        f"كود الدولة لدى HeroSMS: <code>{country.herosms_code or '—'}</code>\n"
        f"كود الخدمة لدى HeroSMS: <code>{service.herosms_code or '—'}</code>\n"
        f"حالة الدولة: {'🟢 مفعّلة' if country.is_active else '⚪ معطّلة (فعّلها لعرضها للمستخدمين)'}\n\n"
        f"التكلفة الحالية: <b>{f'{current}$' if current is not None else 'تلقائية من المزود'}</b>\n"
        f"نسبة الربح الحالية: <b>{margin_value}%</b>\n\n"
        "أرسل <b>سعر التكلفة بالدولار</b> كما هو في لوحة HeroSMS\n"
        "(مثال: <code>0.5</code>)\n\n"
        "أو أرسل <code>-</code> للرجوع للتسعير التلقائي.",
        reply_markup=admin_back_kb(),
    )
    await state.update_data(price_service=service_code, price_country=country_id)
    await state.set_state(AdminCountryStates.waiting_manual_price)


@router.message(AdminCountryStates.waiting_manual_price)
async def manual_price_received(message: Message, state: FSMContext, session):
    """يستقبل سعر التكلفة اليدوي بالدولار."""
    raw = (message.text or "").strip().replace(",", ".")
    country, service = await _load_price_context(session, await state.get_data())
    if not country or not service:
        await message.answer("⚠️ البيانات غير مكتملة. ابدأ من جديد من إدارة الدول.")
        await state.clear()
        return

    from services.pricing_service import PricingService

    if raw == "-":
        await PricingService.delete_manual_cost(session, service.code, country.code)
        from services.number_catalog_service import invalidate_board

        invalidate_board(service.code)
        await message.answer(
            f"✅ رجعت تكلفة {service.emoji} {service.name_ar} لدولة "
            f"{country.flag} {country.name_ar} للتسعير التلقائي من المزود."
        )
        await state.clear()
        return

    try:
        cost = Decimal(raw)
    except Exception:  # noqa: BLE001 - إدخال غير رقمي
        await message.answer("⚠️ أرسل رقماً صحيحاً مثل 0.5 أو أرسل - للإلغاء.")
        return
    if cost <= 0:
        await message.answer("⚠️ السعر يجب أن يكون أكبر من صفر.")
        return

    await PricingService.set_manual_cost(session, service.code, country.code, cost)
    await message.answer(
        f"✅ تم حفظ التكلفة <b>{cost}$</b>.\n\n"
        "الآن أرسل <b>نسبة الربح %</b> (مثال: <code>50</code>)\n"
        "أو أرسل <code>-</code> للاحتفاظ بالنسبة الحالية."
    )
    await state.set_state(AdminCountryStates.waiting_manual_margin)


@router.message(AdminCountryStates.waiting_manual_margin)
async def manual_margin_received(message: Message, state: FSMContext, session):
    """يستقبل نسبة الربح ويعرض ملخص التسعير النهائي."""
    raw = (message.text or "").strip().replace("%", "").replace(",", ".")
    country, service = await _load_price_context(session, await state.get_data())
    if not country or not service:
        await message.answer("⚠️ البيانات غير مكتملة. ابدأ من جديد من إدارة الدول.")
        await state.clear()
        return

    from services.pricing_service import PricingService

    if raw != "-":
        try:
            margin = Decimal(raw)
        except Exception:  # noqa: BLE001 - إدخال غير رقمي
            await message.answer("⚠️ أرسل نسبة صحيحة مثل 50 أو أرسل - للاحتفاظ بالحالية.")
            return
        if margin < 0:
            await message.answer("⚠️ النسبة لا يمكن أن تكون سالبة.")
            return
        await PricingService.set_custom_margin(
            session,
            service.code,
            "percent",
            margin,
            country_code=country.code,
        )

    cost = await PricingService.get_manual_cost(session, service.code, country.code)
    margin_type, margin_value = await PricingService.get_margin(
        session, service.code, country.code, ProviderName.HEROSMS
    )
    sell = PricingService.apply_margin(cost or Decimal("0"), margin_type, margin_value)

    from services.number_catalog_service import invalidate_board

    invalidate_board(service.code)

    await message.answer(
        f"✅ <b>تم ضبط التسعير</b>\n\n"
        f"{service.emoji} {service.name_ar} — {country.flag} {country.name_ar}\n"
        f"💰 التكلفة: <b>{cost}$</b>\n"
        f"📈 الربح: <b>{margin_value}%</b>\n"
        f"🛒 سعر البيع للمستخدم: <b>{sell}$</b>\n\n"
        "السعر ظاهر الآن للمستخدمين فوراً، والشراء يطلب الرقم من المزود مباشرة.\n"
        "⚠️ إن كان السعر الفعلي عند المزود أعلى من تكلفتك المسجلة سيفشل الشراء "
        "ويُسترجع رصيد المشتري — حدّث التكلفة وقتها."
    )
    await state.clear()


# ══════════════════════════════════════════════
# ══════════════ سحب الدول من HeroSMS ══════════════
# ══════════════════════════════════════════════

_HEROSMS_SERVICE_LABELS = {
    "whatsapp": "💬 واتساب",
    "telegram": "✈️ تيليجرام",
}


@router.callback_query(F.data == "admin:country_sync_herosms")
async def country_sync_herosms_menu(callback: CallbackQuery):
    """يعرض قائمة اختيار خدمات السحب من HeroSMS."""
    await callback.answer()
    await callback.message.edit_text(
        "🔄 <b>سحب الدول من HeroSMS</b>\n\n"
        "يسحب البوت كتالوج الدول من حسابك في HeroSMS تلقائياً،\n"
        "ويفحص توفر المخزون للخدمة المختارة، ثم ينشئ الدول\n"
        "بأكواد HeroSMS الصحيحة ويفعّل المتاح منها.\n\n"
        "⏱ تستغرق العملية عادة من 20 إلى 60 ثانية.\n"
        "✏️ يمكنك بعد السحب تعديل أي اسم أو علم من إدارة الدول.\n\n"
        "اختر الخدمة المطلوبة:",
        reply_markup=herosms_sync_menu_kb(),
    )


def _parse_sync_services(raw: str) -> list[str]:
    """يحلل جزء الخدمات من callback مثل whatsapp,telegram."""
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
    labels = " + ".join(
        _HEROSMS_SERVICE_LABELS.get(code, code) for code in wanted
    )
    await callback.answer("⏳ جارٍ السحب من HeroSMS... قد يستغرق دقيقة.")

    status_text = (
        f"⏳ <b>جارٍ السحب من HeroSMS...</b>\n\n"
        f"الخدمات: {labels}\n"
        f"التفعيل التلقائي: {'🟢 نعم' if activate else '⚪ لا'}\n\n"
        "قد تستغرق العملية حتى دقيقة، لا تغلق الشاشة."
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
    except Exception as e:  # noqa: BLE001 - نعرض الخطأ للأدمن بدل الصمت
        text = f"❌ <b>فشل السحب من HeroSMS</b>\n\n<code>{type(e).__name__}: {e}</code>"

    from keyboards.admin import admin_countries_kb as _kb

    countries = await get_all_countries(session)
    try:
        await callback.message.edit_text(text, reply_markup=_kb(countries))
    except Exception:
        await callback.message.answer(text, reply_markup=_kb(countries))


@router.callback_query(F.data.startswith("admin:country_sync_idle:"))
async def country_sync_herosms_idle(callback: CallbackQuery, session):
    """سحب الدول دون تفعيلها تلقائياً."""
    services_raw = callback.data.split(":", 2)[2]
    await _run_herosms_sync(callback, session, services_raw, activate=False)


@router.callback_query(F.data.startswith("admin:country_sync:"))
async def country_sync_herosms_go(callback: CallbackQuery, session):
    """سحب الدول مع تفعيل المتاح منها تلقائياً."""
    services_raw = callback.data.split(":", 2)[2]
    await _run_herosms_sync(callback, session, services_raw, activate=True)


# ══════════════════════════════════════════════
# ══════════════ تصفير الدول وإعادة السحب ══════════════
# ══════════════════════════════════════════════


async def _reset_synced_countries(session) -> int:
    """يحذف الدول المسحوبة تلقائياً من HeroSMS (herosms_code ليس فارغاً).

    يحذف معها قواعد التسعير الخاصة بها (كي لا تبقى قواعد يتيمة)
    ويمسح كاش أسعار الأرقام. الدول المضافة يدوياً تبقى كما هي،
    وكذلك المستخدمون والأرصدة والطلبات القديمة (لا يوجد أي مفتاح
    خارجي يشير لجدول countries). يرجع عدد الدول المحذوفة.
    """
    from database.models import ServicePricing
    from services.price_cache_service import PriceCacheService
    from services.settings_service import SettingsService

    result = await session.execute(
        select(Country).where(Country.herosms_code.is_not(None))
    )
    targets = list(result.scalars().all())
    if not targets:
        return 0

    codes = [c.code for c in targets]
    await session.execute(
        sql_delete(ServicePricing).where(ServicePricing.country_code.in_(codes))
    )

    # إزالة الأسعار اليدوية المرتبطة بالدول المحذوفة أيضاً
    svc_rows = await session.execute(select(NumberService.code))
    service_codes = [row[0] for row in svc_rows.all()]
    for code in codes:
        for svc in service_codes:
            try:
                await SettingsService.delete(session, f"manual_cost:{svc}:{code}")
            except Exception:  # noqa: BLE001 - تنظيف اختياري
                pass
    await session.execute(
        sql_delete(Country).where(Country.id.in_([c.id for c in targets]))
    )
    await session.commit()

    try:
        await PriceCacheService.invalidate("number-price:")
    except Exception:  # noqa: BLE001 - فشل الكاش لا يوقف العملية
        pass
    return len(targets)


@router.callback_query(F.data == "admin:country_reset")
async def country_reset_confirm(callback: CallbackQuery, session):
    """شاشة تأكيد تصفير الدول المسحوبة تلقائياً."""
    synced = (
        await session.execute(
            select(func.count(Country.id)).where(Country.herosms_code.is_not(None))
        )
    ).scalar_one()
    total = (
        await session.execute(select(func.count(Country.id)))
    ).scalar_one()

    await callback.answer()
    await callback.message.edit_text(
        "🗑 <b>تصفير الدول وإعادة السحب</b>\n\n"
        f"سيتم حذف <b>{synced}</b> دولة (المسحوبة تلقائياً من HeroSMS) "
        f"من أصل {total} دولة.\n"
        "الدول المضافة يدوياً ستبقى كما هي.\n\n"
        "بعدها يبدأ سحب جديد فوراً (واتساب + تيليجرام) مع تفعيل المتوفر.\n\n"
        "✅ المستخدمون والأرصدة والطلبات القديمة لا تُمس إطلاقاً.\n\n"
        "متأكد؟",
        reply_markup=country_reset_confirm_kb(),
    )


@router.callback_query(F.data == "admin:country_reset_go")
async def country_reset_go(callback: CallbackQuery, session):
    """ينفذ التصفير ثم سحباً جديداً من HeroSMS."""
    if not settings.HEROSMS_API_KEY:
        await callback.answer(
            "⚠️ لا يوجد HEROSMS_API_KEY مضبوط في الإعدادات.",
            show_alert=True,
        )
        return

    await callback.answer("⏳ جارٍ التصفير وإعادة السحب... قد يستغرق دقيقة.")
    status_text = (
        "🗑 <b>جارٍ حذف الدول المسحوبة ثم سحبها من جديد...</b>\n\n"
        "لا تغلق الشاشة، قد تستغرق العملية حتى دقيقة."
    )
    try:
        await callback.message.edit_text(status_text)
    except Exception:
        await callback.message.answer(status_text)

    try:
        deleted = await _reset_synced_countries(session)
        report = await sync_herosms_countries(
            session,
            wanted_services=["whatsapp", "telegram"],
            activate=True,
        )
        text = (
            f"🗑 تم حذف <b>{deleted}</b> دولة قديمة (المسحوبة تلقائياً).\n\n"
            + report.summary()
        )
    except Exception as e:  # noqa: BLE001 - نعرض الخطأ للأدمن بدل الصمت
        text = f"❌ <b>فشل التصفير/السحب</b>\n\n<code>{type(e).__name__}: {e}</code>"

    from keyboards.admin import admin_countries_kb as _kb

    countries = await get_all_countries(session)
    try:
        await callback.message.edit_text(text, reply_markup=_kb(countries))
    except Exception:
        await callback.message.answer(text, reply_markup=_kb(countries))

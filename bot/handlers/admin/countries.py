"""
إدارة الدول من لوحة الأدمن.
يدعم 4 مزودين الآن بدل 2.
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database.models import Country
from providers.countries import get_all_countries
from providers.fivesim import FiveSimProvider
from states.states import AdminCountryStates
from keyboards.admin import (
    admin_countries_kb,
    admin_country_detail_kb,
    admin_back_kb,
)
from filters.admin_filter import IsAdmin

router = Router(name="admin_countries")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:countries")
async def countries_list(callback: CallbackQuery, session):
    countries = await get_all_countries(session)
    text = "🌍 <b>إدارة الدول</b>\n\n🟢 = مفعّلة | ⚪ = معطّلة\n\n"
    if countries:
        text += "اضغط على أي دولة لتعديلها."
    else:
        text += "لا توجد أي دولة مضافة بعد."
    await callback.message.edit_text(text, reply_markup=admin_countries_kb(countries))


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
    await callback.message.edit_text(
        f"{country.flag} <b>{country.name_ar}</b>\n\n"
        f"المعرّف: <code>{country.code}</code>\n"
        f"الحالة: {status}\n"
        f"5sim: <code>{country.fivesim_code or '—'}</code>\n"
        f"HeroSMS: <code>{country.herosms_code or '—'}</code>\n"
        f"SMS-Activate: <code>{country.sms_activate_code or '—'}</code>\n"
        f"SMSHub: <code>{country.smshub_code or '—'}</code>\n"
        f"الترتيب: {country.sort_order}",
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

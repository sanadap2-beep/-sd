"""
إدارة خدمات الأرقام الديناميكية من لوحة الأدمن.
بدل SERVICE_MAP الثابت، كل خدمة تُدار من هنا.
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from services.dynamic_service import DynamicService
from states.states import AdminNumberServiceStates
from keyboards.admin import (
    admin_number_services_kb,
    admin_nsvc_detail_kb,
    admin_back_kb,
)
from filters.admin_filter import IsAdmin

router = Router(name="admin_number_services")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════ قائمة الخدمات ══════════════


@router.callback_query(F.data == "admin:number_services")
async def number_services_list(callback: CallbackQuery, session):
    services = await DynamicService.get_all_number_services(session)
    await callback.message.edit_text(
        "📞 <b>إدارة خدمات الأرقام</b>\n\n"
        "🟢 = مفعّلة | ⚪ = معطّلة\n\n"
        "هذه الخدمات تظهر في القائمة الرئيسية للمستخدمين.",
        reply_markup=admin_number_services_kb(services),
    )


# ══════════════ إضافة خدمة ══════════════


@router.callback_query(F.data == "admin:nsvc_add")
async def nsvc_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ <b>إضافة خدمة أرقام جديدة</b>\n\n"
        "أرسل كود الخدمة بالإنجليزية (بدون مسافات):\n"
        "(مثال: instagram أو snapchat)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminNumberServiceStates.waiting_code)


@router.message(AdminNumberServiceStates.waiting_code)
async def nsvc_code_received(message: Message, state: FSMContext, session):
    code = message.text.strip().lower().replace(" ", "_")

    existing = await DynamicService.get_number_service_by_code(session, code)
    if existing:
        await message.answer("⚠️ توجد خدمة بهذا الكود مسبقاً. أرسل كوداً آخر.")
        return

    await state.update_data(nsvc_code=code)
    await message.answer("📝 أرسل اسم الخدمة بالعربي:\n(مثال: إنستقرام)")
    await state.set_state(AdminNumberServiceStates.waiting_name)


@router.message(AdminNumberServiceStates.waiting_name)
async def nsvc_name_received(message: Message, state: FSMContext):
    await state.update_data(nsvc_name=message.text.strip())
    await message.answer("أرسل إيموجي للخدمة:\n(أو أرسل - لاستخدام 📱)")
    await state.set_state(AdminNumberServiceStates.waiting_emoji)


@router.message(AdminNumberServiceStates.waiting_emoji)
async def nsvc_emoji_received(message: Message, state: FSMContext):
    emoji = message.text.strip()
    if emoji == "-":
        emoji = "📱"
    await state.update_data(nsvc_emoji=emoji)
    await message.answer("🔢 أرسل كود الخدمة لدى <b>5sim</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminNumberServiceStates.waiting_fivesim_code)


@router.message(AdminNumberServiceStates.waiting_fivesim_code)
async def nsvc_fivesim_received(message: Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(nsvc_fivesim=None if val == "-" else val)
    await message.answer("🔢 أرسل كود الخدمة لدى <b>HeroSMS</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminNumberServiceStates.waiting_herosms_code)


@router.message(AdminNumberServiceStates.waiting_herosms_code)
async def nsvc_herosms_received(message: Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(nsvc_herosms=None if val == "-" else val)
    await message.answer("🔢 أرسل كود الخدمة لدى <b>SMS-Activate</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminNumberServiceStates.waiting_sms_activate_code)


@router.message(AdminNumberServiceStates.waiting_sms_activate_code)
async def nsvc_sms_activate_received(message: Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(nsvc_sms_activate=None if val == "-" else val)
    await message.answer("🔢 أرسل كود الخدمة لدى <b>SMSHub</b>:\n(أو أرسل - للتخطي)")
    await state.set_state(AdminNumberServiceStates.waiting_smshub_code)


@router.message(AdminNumberServiceStates.waiting_smshub_code)
async def nsvc_smshub_received(message: Message, state: FSMContext, session):
    val = message.text.strip()
    data = await state.get_data()

    fivesim = data.get("nsvc_fivesim")
    herosms = data.get("nsvc_herosms")
    sms_activate = data.get("nsvc_sms_activate")
    smshub = None if val == "-" else val

    if not any([fivesim, herosms, sms_activate, smshub]):
        await message.answer("⚠️ يجب تحديد كود لدى مزود واحد على الأقل.")
        await state.clear()
        return

    service = await DynamicService.create_number_service(
        session=session,
        code=data["nsvc_code"],
        name_ar=data["nsvc_name"],
        emoji=data["nsvc_emoji"],
        fivesim_code=fivesim,
        herosms_code=herosms,
        sms_activate_code=sms_activate,
        smshub_code=smshub,
    )

    await message.answer(
        f"✅ تم إضافة خدمة الأرقام:\n"
        f"{service.emoji} <b>{service.name_ar}</b>\n\n"
        "ستظهر الآن في القائمة الرئيسية للمستخدمين."
    )
    await state.clear()


# ══════════════ تفاصيل خدمة ══════════════


@router.callback_query(F.data.startswith("admin:nsvc_view:"))
async def nsvc_view(callback: CallbackQuery, session):
    svc_id = int(callback.data.split(":")[2])
    svc = await session.get(
        __import__("database.models", fromlist=["NumberService"]).NumberService,
        svc_id,
    )
    if not svc:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    status = "🟢 مفعّلة" if svc.is_active else "⚪ معطّلة"
    await callback.message.edit_text(
        f"{svc.emoji} <b>{svc.name_ar}</b>\n\n"
        f"الكود: <code>{svc.code}</code>\n"
        f"الحالة: {status}\n"
        f"5sim: <code>{svc.fivesim_code or '—'}</code>\n"
        f"HeroSMS: <code>{svc.herosms_code or '—'}</code>\n"
        f"SMS-Activate: <code>{svc.sms_activate_code or '—'}</code>\n"
        f"SMSHub: <code>{svc.smshub_code or '—'}</code>\n"
        f"الترتيب: {svc.sort_order}",
        reply_markup=admin_nsvc_detail_kb(svc),
    )


# ══════════════ تفعيل/تعطيل ══════════════


@router.callback_query(F.data.startswith("admin:nsvc_toggle:"))
async def nsvc_toggle(callback: CallbackQuery, session):
    svc_id = int(callback.data.split(":")[2])
    await DynamicService.update_number_service(
        session,
        svc_id,
        is_active=not (
            await session.get(
                __import__("database.models", fromlist=["NumberService"]).NumberService,
                svc_id,
            )
        ).is_active,
    )
    await callback.answer("✅ تم التحديث.")
    await nsvc_view(callback, session)


# ══════════════ تعديل الاسم ══════════════


@router.callback_query(F.data.startswith("admin:nsvc_edit_name:"))
async def nsvc_edit_name_start(callback: CallbackQuery, state: FSMContext):
    svc_id = int(callback.data.split(":")[2])
    await state.update_data(edit_nsvc_id=svc_id)
    await callback.message.edit_text(
        "📝 أرسل الاسم الجديد بالعربي:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminNumberServiceStates.waiting_edit_value)


@router.message(AdminNumberServiceStates.waiting_edit_value)
async def nsvc_edit_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    svc_id = data.get("edit_nsvc_id")
    await DynamicService.update_number_service(session, svc_id, name_ar=message.text.strip())
    await message.answer("✅ تم تحديث الاسم.")
    await state.clear()


# ══════════════ حذف خدمة ══════════════


@router.callback_query(F.data.startswith("admin:nsvc_delete:"))
async def nsvc_delete(callback: CallbackQuery, session):
    svc_id = int(callback.data.split(":")[2])
    success = await DynamicService.delete_number_service(session, svc_id)
    if success:
        await callback.answer("🗑 تم حذف الخدمة.")
    else:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
    await number_services_list(callback, session)

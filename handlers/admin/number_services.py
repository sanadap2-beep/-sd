"""
إدارة خدمات الأرقام الديناميكية من لوحة الأدمن.
بدل SERVICE_MAP الثابت، كل خدمة تُدار من هنا.
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from services.dynamic_service import DynamicService
from services.feature_service import FeatureService
from services.availability_board_service import FEATURE_KEY as AVAIL_FEATURE, AvailabilityBoardService
from services.number_catalog_service import normalize_country_code
from services.number_server_service import NumberServerService, server_label
from states.states import AdminNumberServiceStates
from keyboards.admin import (
    admin_number_services_kb,
    admin_nsvc_avail_kb,
    admin_nsvc_avail_services_kb,
    admin_nsvc_choose_provider_kb,
    admin_nsvc_detail_kb,
    admin_nsvc_server_detail_kb,
    admin_nsvc_servers_kb,
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


# ══════════════ سيرفرات/مزودي خدمة الأرقام (ديناميكي) ══════════════


@router.callback_query(F.data.startswith("admin:nsvc_servers:"))
async def nsvc_servers_list(callback: CallbackQuery, session):
    svc_id = int(callback.data.split(":")[2])
    svc = await session.get(__import__("database.models", fromlist=["NumberService"]).NumberService, svc_id)
    if not svc:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    servers = await NumberServerService.list_servers(session, svc.id, active_only=False)
    await callback.answer()
    await callback.message.edit_text(
        f"⚙️ <b>سيرفرات «{svc.name_ar}»</b>\n\n"
        "كل سيرفر = مزود مستقل. المستخدم يختار السيرفر قبل رؤية الدول.\n\n"
        f"🟢 = مفعّل | ⚪ = معطّل",
        reply_markup=admin_nsvc_servers_kb(svc.id, servers),
    )


@router.callback_query(F.data.startswith("admin:nsvc_server_auto:"))
async def nsvc_server_auto(callback: CallbackQuery, session):
    """ينشئ سيرفراً تلقائياً لكل مزود عليه كود مضبوط في هذه الخدمة."""
    svc_id = int(callback.data.split(":")[2])
    svc = await session.get(__import__("database.models", fromlist=["NumberService"]).NumberService, svc_id)
    if not svc:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    created = await NumberServerService.ensure_defaults(session, svc)
    await callback.answer(f"✅ تم تجهيز {len(created)} سيرفر.")
    await nsvc_servers_list(callback, session)


@router.callback_query(F.data.startswith("admin:nsvc_server_add:"))
async def nsvc_server_add_start(callback: CallbackQuery, state: FSMContext):
    svc_id = int(callback.data.split(":")[2])
    await state.update_data(nsvc_server_service_id=svc_id)
    await callback.message.edit_text(
        "➕ <b>إضافة سيرفر</b>\n\n"
        "أرسل اسم السيرفر بالعربي (مثال: سيرفر 5sim سريع):",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminNumberServiceStates.waiting_server_name)


@router.message(AdminNumberServiceStates.waiting_server_name)
async def nsvc_server_name_received(message: Message, state: FSMContext):
    await state.update_data(nsvc_server_name=message.text.strip())
    await message.answer("🎨 أرسل إيموجي للسيرفر (أو أرسل - لاستخدام 🖥):")
    await state.set_state(AdminNumberServiceStates.waiting_server_emoji)


@router.message(AdminNumberServiceStates.waiting_server_emoji)
async def nsvc_server_emoji_received(message: Message, state: FSMContext):
    emoji = message.text.strip()
    if emoji == "-":
        emoji = "🖥"
    await state.update_data(nsvc_server_emoji=emoji)
    data = await state.get_data()
    await message.answer(
        "🔌 <b>اختر المزود المرتبط بهذا السيرفر:</b>",
        reply_markup=admin_nsvc_choose_provider_kb(
            data["nsvc_server_service_id"], show_back=False
        ),
    )
    await state.set_state(AdminNumberServiceStates.waiting_server_provider)


@router.callback_query(F.data.startswith("admin:nsvc_server_provider:"))
async def nsvc_server_provider_received(callback: CallbackQuery, state: FSMContext, session):
    parts = callback.data.split(":")
    server_id = int(parts[1])
    provider_value = parts[2]
    data = await state.get_data()
    svc_id = data.get("nsvc_server_service_id") or data.get("edit_server_service_id")
    await callback.answer()
    if not svc_id:
        await callback.answer("⚠️ بيانات ناقصة.", show_alert=True)
        return
    provider = NumberServerService.validate_provider(provider_value)
    if not provider:
        await callback.answer("❌ مزود غير معروف.", show_alert=True)
        return
    if server_id:
        await NumberServerService.update(session, server_id, provider=provider)
        await callback.message.edit_text(f"✅ تم ربط السيرفر بالمزود: {provider}")
        await callback.message.answer("🔙 مباشرةً إلى شاشة السيرفر.", reply_markup=admin_back_kb())
        await state.clear()
        return
    emoji, label = await server_label(provider)
    name = data.get("nsvc_server_name") or label
    emoji = data.get("nsvc_server_emoji") or emoji
    server = await NumberServerService.create(
        session,
        number_service_id=int(svc_id),
        name_ar=name,
        provider=provider,
        emoji=emoji,
    )
    await state.clear()
    await callback.message.edit_text(
        f"✅ <b>تمت إضافة السيرفر</b>\n\n{server.emoji} {server.name_ar}\n"
        f"🔌 المزود: {provider}", reply_markup=admin_back_kb()
    )
    await callback.message.answer("🔙 مباشرةً إلى شاشة السيرفر.", reply_markup=admin_back_kb())


@router.callback_query(F.data.startswith("admin:nsvc_server:"))
async def nsvc_server_view(callback: CallbackQuery, session):
    server_id = int(callback.data.split(":")[2])
    server = await NumberServerService.get(session, server_id)
    if server is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    status = "🟢 مفعّل" if server.is_active else "⚪ معطّل"
    await callback.answer()
    await callback.message.edit_text(
        f"{server.emoji} <b>{server.name_ar}</b>\n\n"
        f"الحالة: {status}\n"
        f"🔌 المزود: <b>{server.provider}</b>\n"
        f"🔢 الترتيب: {server.sort_order}\n\n"
        "المستخدم يرى هذا السيرفر قبل اختيار الدولة.",
        reply_markup=admin_nsvc_server_detail_kb(server.number_service_id, server),
    )


@router.callback_query(F.data.startswith("admin:nsvc_server_toggle:"))
async def nsvc_server_toggle(callback: CallbackQuery, session):
    server_id = int(callback.data.split(":")[2])
    server = await NumberServerService.get(session, server_id)
    if server is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    await NumberServerService.update(session, server_id, is_active=not server.is_active)
    await callback.answer("✅ تم التحديث.")
    await nsvc_server_view(callback, session)


@router.callback_query(F.data.startswith("admin:nsvc_server_edit_name:"))
async def nsvc_server_edit_name(callback: CallbackQuery, state: FSMContext):
    server_id = int(callback.data.split(":")[2])
    await state.update_data(edit_server_id=server_id, edit_server_field="name")
    await callback.message.edit_text("📝 أرسل اسم السيرفر الجديد:", reply_markup=admin_back_kb())
    await state.set_state(AdminNumberServiceStates.waiting_server_edit_value)


@router.callback_query(F.data.startswith("admin:nsvc_server_edit_emoji:"))
async def nsvc_server_edit_emoji(callback: CallbackQuery, state: FSMContext):
    server_id = int(callback.data.split(":")[2])
    await state.update_data(edit_server_id=server_id, edit_server_field="emoji")
    await callback.message.edit_text("🎨 أرسل الإيموجي الجديد (أو - للافتراضي):", reply_markup=admin_back_kb())
    await state.set_state(AdminNumberServiceStates.waiting_server_edit_value)


@router.callback_query(F.data.startswith("admin:nsvc_server_edit_provider:"))
async def nsvc_server_edit_provider(callback: CallbackQuery, state: FSMContext, session):
    server_id = int(callback.data.split(":")[2])
    server = await NumberServerService.get(session, server_id)
    if server is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    await state.update_data(edit_server_id=server_id, edit_server_service_id=server.number_service_id)
    await callback.answer()
    await callback.message.edit_text(
        "🔌 اختر المزود الجديد لهذا السيرفر:",
        reply_markup=admin_nsvc_choose_provider_kb(server.number_service_id, server_id),
    )


@router.callback_query(F.data.startswith("admin:nsvc_server_delete:"))
async def nsvc_server_delete(callback: CallbackQuery, session):
    server_id = int(callback.data.split(":")[2])
    server = await NumberServerService.get(session, server_id)
    if server is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    svc_id = server.number_service_id
    await NumberServerService.delete(session, server_id)
    await callback.answer("🗑 تم حذف السيرفر.")
    await nsvc_servers_list(callback, session)


@router.message(AdminNumberServiceStates.waiting_server_edit_value)
async def nsvc_server_edit_value_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    server_id = data.get("edit_server_id")
    field = data.get("edit_server_field")
    value = message.text.strip()
    if field == "name" and value:
        await NumberServerService.update(session, server_id, name_ar=value)
    elif field == "emoji":
        await NumberServerService.update(session, server_id, emoji="🖥" if value == "-" else value)
    await message.answer("✅ تم التحديث.")
    await state.clear()


# ══════════════ قناة التوفر المتقطع (الأرقام الحية) ══════════════


def _repost_cadence_text(cycles: int, refresh_seconds: int) -> str:
    """:وصف معدّل إعادة النشر بالعربية («كل دورة» أو «كل N دورات»)."""
    minutes = round(cycles * refresh_seconds / 60, 1)
    if cycles == 1:
        return f"رسالة جديدة <b>كل دورة</b> (≈{minutes} دقيقة) — حذف القديمة ثم نشر الجديدة"
    return f"رسالة جديدة كل <b>{cycles}</b> دورات (≈{minutes} دقيقة)"


async def _avail_status_text() -> str:
    enabled = await AvailabilityBoardService.enabled()
    chat_id = await AvailabilityBoardService.channel_chat_id()
    service_code = await AvailabilityBoardService.service_code()
    top_n = await AvailabilityBoardService.top_n()
    refresh = await AvailabilityBoardService.refresh_seconds()
    watched = await AvailabilityBoardService.watched_country_codes_text()
    rotate = await AvailabilityBoardService.rotate_stable()
    auto_repost = await AvailabilityBoardService.auto_repost()
    repost_every = await AvailabilityBoardService.repost_every_cycles()
    restock_push = await AvailabilityBoardService.repost_on_restock()
    channel = f"<code>{chat_id}</code>" if chat_id else "⚪ لم تُضبط"
    status = "🟢 مفعّلة" if enabled else "⚪ معطّلة (من مركز الإضافات)"
    from services.bot_identity import resolve_bot_username

    username = await resolve_bot_username(None)
    link_state = f"<code>@{username}</code>" if username else "⚠️ غير محدد (الروابط ستفشل)"
    return (
        "📡 <b>التوفر المتقطع — قناة الأرقام الحية</b>\n\n"
        "تُحذف الرسالة القديمة وتُنشر لوحة جديدة بأحدث الدول المتوفرة كل "
        "دورة (إشعار فعلي للمشتركين + بقاؤها رأس القناة)، بترتيب دوّار "
        "للدول المتوفرة، مع وسم 🔥 للدول النادرة لحظة رجوعها للمخزون و💎 للنادرة "
        "المتاحة. كل زر رابط شراء مباشر لدولة مفعّلة فعلاً.\n\n"
        f"الحالة: {status}\n"
        f"القناة: {channel}\n"
        f"يوزرنيم الروابط: {link_state}\n"
        f"الخدمة: <code>{service_code}</code>\n"
        f"عدد الدول: <b>{top_n}</b>\n"
        f"التحديث: كل <b>{refresh}</b> ثانية\n"
        f"الترتيب الدوّار: <b>{'مفعّل' if rotate else 'معطّل'}</b>\n"
        f"🔔 إعادة النشر التلقائي: <b>{'مفعّل' if auto_repost else 'معطّل'}</b>"
        f" — {_repost_cadence_text(repost_every, refresh)}\\n"
        f"🔥 إشعار فوري عند رجوع دولة نادرة: <b>{'مفعّل' if restock_push else 'معطّل'}</b>\\n"
        f"الدول المراقبة: <code>{watched}</code>"
    )


@router.callback_query(F.data == "admin:nsvc_avail")
async def nsvc_avail_home(callback: CallbackQuery):
    text = await _avail_status_text()
    await callback.message.edit_text(text, reply_markup=await _avail_kb())
    await callback.answer()


async def _avail_kb():
    """أزرار القناة بحالتها الحالية (بدل تمرير القيم يدوياً في كل مكان)."""
    return admin_nsvc_avail_kb(
        await AvailabilityBoardService.rotate_stable(),
        await AvailabilityBoardService.auto_repost(),
        await AvailabilityBoardService.repost_on_restock(),
    )


@router.callback_query(F.data == "admin:nsvc_avail_channel")
async def nsvc_avail_channel_start(callback: CallbackQuery, state: FSMContext):
    chat_id = await AvailabilityBoardService.channel_chat_id()
    current = f"\n\nالقناة الحالية: <code>{chat_id}</code>" if chat_id else ""
    await callback.message.edit_text(
        " أرسل آيدي قناة التوفر:\n"
        "(مثال: -1001234567890)\n"
        "أضف البوت أدمن في القناة أولًا.\n"
        "أو أرسل 0 لإيقاف النشر."
        f"{current}",
        reply_markup=admin_nsvc_avail_kb(),
    )
    await state.set_state(AdminNumberServiceStates.waiting_availability_channel)
    await callback.answer()


@router.message(AdminNumberServiceStates.waiting_availability_channel)
async def nsvc_avail_channel_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw not in ("0",) and not (raw.lstrip("-").isdigit()):
        await message.answer("⚠️ أرسل آيدي القناة رقمياً (أو 0 للإيقاف).")
        return
    await FeatureService.set_option(session, AVAIL_FEATURE, "channel_chat_id", raw)
    await state.clear()
    await message.answer(
        "✅ تم تحديث قناة التوفر.",
        reply_markup=admin_nsvc_avail_kb(),
    )


@router.callback_query(F.data == "admin:nsvc_avail_topn")
async def nsvc_avail_topn_start(callback: CallbackQuery, state: FSMContext):
    current = await AvailabilityBoardService.top_n()
    await callback.message.edit_text(
        f"🔢 أرسل عدد الدول المعروضة (3–25).\n\nالحالي: <b>{current}</b>",
        reply_markup=admin_nsvc_avail_kb(),
    )
    await state.set_state(AdminNumberServiceStates.waiting_availability_topn)
    await callback.answer()


@router.message(AdminNumberServiceStates.waiting_availability_topn)
async def nsvc_avail_topn_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    try:
        value = int(raw)
    except ValueError:
        await message.answer("⚠️ أرسل رقماً فقط.")
        return
    if not (3 <= value <= 25):
        await message.answer("⚠️ القيمة بين 3 و25.")
        return
    await FeatureService.set_option(session, AVAIL_FEATURE, "top_n", value)
    await state.clear()
    await message.answer("✅ تم تحديث عدد الدول.", reply_markup=admin_nsvc_avail_kb())


@router.callback_query(F.data == "admin:nsvc_avail_watchlist")
async def nsvc_avail_watchlist_start(callback: CallbackQuery, state: FSMContext):
    current = await AvailabilityBoardService.watched_country_codes_text(limit=80)
    await callback.message.edit_text(
        "🌍 <b>الدول النادرة المراقبة</b>\n\n"
        "أرسل أكواد الدول مفصولة بفواصل، حسب أكواد الدول داخل قاعدة البيانات.\n"
        "مثال: <code>ae,sa,us,gb,qa,kw,bh,om</code>\n\n"
        "عند رجوع إحدى هذه الدول للمخزون ستظهر في قناة التوفر فوراً.\n\n"
        f"الحالي:\n<code>{current}</code>",
        reply_markup=admin_nsvc_avail_kb(),
    )
    await state.set_state(AdminNumberServiceStates.waiting_availability_watchlist)
    await callback.answer()


@router.message(AdminNumberServiceStates.waiting_availability_watchlist)
async def nsvc_avail_watchlist_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip().replace("\n", ",").replace(";", ",")
    codes = []
    seen = set()
    for part in raw.split(","):
        code = normalize_country_code(part)
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        await message.answer("⚠️ أرسل كود دولة واحداً على الأقل، مثل: ae,sa,us")
        return
    await FeatureService.set_option(session, AVAIL_FEATURE, "watched_country_codes", ",".join(codes))
    AvailabilityBoardService.reset_state()
    await state.clear()
    await message.answer("✅ تم تحديث قائمة الدول النادرة ومُسحت حالة المقارنة القديمة.", reply_markup=admin_nsvc_avail_kb())


@router.callback_query(F.data == "admin:nsvc_avail_services")
async def nsvc_avail_services(callback: CallbackQuery, session):
    services = await DynamicService.get_all_number_services(session)
    current = await AvailabilityBoardService.service_code()
    lines = ["📱 <b>اختر خدمة التوفر المتقطع</b>", ""]
    for svc in services:
        mark = "🟢" if svc.code == current else "⚪"
        lines.append(f"{mark} {svc.emoji} {svc.name_ar} (<code>{svc.code}</code>)")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_nsvc_avail_services_kb(services),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:nsvc_avail_svc:"))
async def nsvc_avail_service_selected(callback: CallbackQuery, session, state: FSMContext):
    code = callback.data.rsplit(":", 1)[1]
    await FeatureService.set_option(session, AVAIL_FEATURE, "service_code", code)
    AvailabilityBoardService.reset_state()
    await callback.answer("✅ تم اختيار الخدمة.")
    await nsvc_avail_home(callback)


@router.callback_query(F.data == "admin:nsvc_avail_post")
async def nsvc_avail_post(callback: CallbackQuery, session, bot):
    await callback.answer("⏳ جاري النشر...")
    result = await AvailabilityBoardService.post_board(bot)
    text = await _avail_status_text()
    await callback.message.edit_text(
        f"{text}\n\n<b>نتيجة الاختبار:</b>\n{result}",
        reply_markup=admin_nsvc_avail_kb(),
    )


@router.callback_query(F.data == "admin:nsvc_avail_rotate")
async def nsvc_avail_rotate(callback: CallbackQuery, session):
    """تبديل الترتيب الدوّار للدول الثابتة."""
    current = await AvailabilityBoardService.rotate_stable()
    await FeatureService.set_option(session, AVAIL_FEATURE, "rotate_stable", not current)
    await callback.answer("✅ تم التبديل.")
    await nsvc_avail_home(callback)


@router.callback_query(F.data == "admin:nsvc_avail_repost")
async def nsvc_avail_repost(callback: CallbackQuery, bot):
    """إعادة نشر اللوحة كرسالة جديدة (ترفعها لأعلى القناة)."""
    await callback.answer("⏳ جاري إعادة النشر...")
    result = await AvailabilityBoardService.repost_now(bot)
    text = await _avail_status_text()
    await callback.message.edit_text(
        f"{text}\n\n<b>نتيجة إعادة النشر:</b>\n{result}",
        reply_markup=admin_nsvc_avail_kb(await AvailabilityBoardService.rotate_stable()),
    )


@router.callback_query(F.data == "admin:nsvc_avail_autorepost")
async def nsvc_avail_autorepost(callback: CallbackQuery, session):
    """:🔔 إعادة النشر التلقائي: كل N دورة تُنشر اللوحة كرسالة جديدة.

    فتظهر في أعلى القناة ويصل المشتركين إشعار فعلي (تعديل الرسالة
    لا يُصدر إشعاراً في Telegram)."""
    current = await AvailabilityBoardService.auto_repost()
    await FeatureService.set_option(session, AVAIL_FEATURE, "auto_repost", not current)
    await callback.answer("✅ تم التبديل.")
    await nsvc_avail_home(callback)


@router.callback_query(F.data == "admin:nsvc_avail_restockpush")
async def nsvc_avail_restock_push(callback: CallbackQuery, session):
    """إشعار فوري (رسالة جديدة) لحظة رجوع دولة نادرة للمخزون."""
    current = await AvailabilityBoardService.repost_on_restock()
    await FeatureService.set_option(session, AVAIL_FEATURE, "repost_on_restock", not current)
    await callback.answer("✅ تم التبديل.")
    await nsvc_avail_home(callback)


@router.callback_query(F.data == "admin:nsvc_avail_repostevery")
async def nsvc_avail_repost_every_start(callback: CallbackQuery, state: FSMContext):
    current = await AvailabilityBoardService.repost_every_cycles()
    refresh = await AvailabilityBoardService.refresh_seconds()
    await callback.message.edit_text(
        "⏱ <b>كل كم دورة تُعاد اللوحة كرسالة جديدة؟</b>\\n\\n"
        f"الحالي: <b>{current}</b> — {_repost_cadence_text(current, refresh)}\\n"
        f"(دورة التحديث = {refresh} ثانية)\\n\\n"
        "أرسل رقماً بين 1 و240:\\n"
        "<b>1</b> = كل دورة (إشعار كل دقيقة — الأفضل لقناة حيّة)\\n"
        "رقم أكبر = أهدأ وأقل إشعارات.\\n\\n"
        "في كل الأحوال اللوحة تُحدَّث بأحدث الدول المتوفرة في كل دورة.",
        reply_markup=await _avail_kb(),
    )
    await state.set_state(AdminNumberServiceStates.waiting_availability_repost_every)
    await callback.answer()


@router.message(AdminNumberServiceStates.waiting_availability_repost_every)
async def nsvc_avail_repost_every_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    try:
        value = int(raw)
    except ValueError:
        await message.answer("⚠️ أرسل رقماً فقط.")
        return
    if not (1 <= value <= 240):
        await message.answer("⚠️ القيمة بين 1 و240.")
        return
    await FeatureService.set_option(session, AVAIL_FEATURE, "repost_every_cycles", value)
    await state.clear()
    await message.answer(
        f"✅ ستُعاد اللوحة كرسالة جديدة كل <b>{value}</b> دورة.",
        reply_markup=await _avail_kb(),
    )


@router.callback_query(F.data == "admin:nsvc_avail_reset")
async def nsvc_avail_reset(callback: CallbackQuery):
    """مسح صورة التوفر المحفوظة (يعيد بناء تاريخ Restock من الصفر)."""
    AvailabilityBoardService.reset_state()
    await callback.answer("✅ مُسحت حالة المقارنة.")
    await nsvc_avail_home(callback)

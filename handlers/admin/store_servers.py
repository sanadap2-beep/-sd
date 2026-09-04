"""لوحة إدارة السيرفرات العامة — تعمل على كل الأقسام.

كل سيرفر:
- يتبع قسم (رئيسي / فرعي / خدمة أرقام) أو «عام» لكل الأقسام.
- مربوط بمزود (متجر/رشق/ألعاب API أو مزود أرقام).
- يحمل نسبة ربح مستقلة تُطبق عند الشراء تلقائياً.
"""

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database.models import ApiProvider, StoreServer
from filters.admin_filter import IsAdmin
from keyboards.admin import (
    admin_back_kb,
    admin_ssvc_api_provider_kb,
    admin_ssvc_number_provider_kb,
    admin_ssvc_provider_kind_kb,
    admin_ssvc_scope_kb,
    admin_ssvc_target_kb,
    admin_store_server_detail_kb,
    admin_store_servers_kb,
)
from services.dynamic_service import DynamicService
from services.store_server_service import StoreServerService
from states.states import AdminStoreServerStates

router = Router(name="admin_store_servers")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SCOPE_LABELS = {
    "category": "📂 قسم رئيسي",
    "subcategory": "🗂 قسم فرعي",
    "number_service": "📞 خدمة أرقام",
    "global": "🌐 عام (كل الأقسام)",
}


def _provider_text(server: StoreServer) -> str:
    if server.provider_kind == "number":
        return f"📱 {server.provider_value or '—'}"
    return f"🔌 مزود API #{server.api_provider_id or '—'}"


# ══════════════ قائمة السيرفرات ══════════════


@router.callback_query(F.data == "admin:store_servers")
async def store_servers_list(callback: CallbackQuery, session):
    servers = await StoreServerService.list_all(session)
    counts = await StoreServerService.counts_by_scope(session)
    lines = ["🖥 <b>السيرفرات العامة (كل الأقسام)</b>", ""]
    for scope in ("category", "subcategory", "number_service", "global"):
        label = SCOPE_LABELS.get(scope, scope)
        lines.append(f"• {label}: <b>{counts.get(scope, 0)}</b>")
    lines.append("")
    lines.append(f"إجمالي السيرفرات: <b>{len(servers)}</b>")
    lines.append("")
    lines.append("🟢 = مفعّل | ⚪ = معطّل")
    lines.append("كل سيرفر مربوط بمزود + نسبة ربح، ويظهر للمستخدم في القسم التابع له.")
    await callback.answer()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_store_servers_kb(servers),
    )


# ══════════════ إضافة سيرفر ══════════════


@router.callback_query(F.data == "admin:ssvc_add")
async def ssvc_add_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "➕ <b>إضافة سيرفر عام</b>\n\n"
        "أولاً: في أي قسم سيعمل هذا السيرفر؟\n"
        "(اختَر «عام» ليعمل على كل الأقسام تلقائياً)",
        reply_markup=admin_ssvc_scope_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:ssvc_scope:"))
async def ssvc_scope_selected(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    # admin:ssvc_scope:{scope} أو admin:ssvc_scope:{scope}:{page}
    if len(parts) < 3:
        await callback.answer("⚠️ نطاق غير معروف.", show_alert=True)
        return
    scope = parts[2]
    try:
        page = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        page = 0
    if scope not in SCOPE_LABELS:
        await callback.answer("⚠️ نطاق غير معروف.", show_alert=True)
        return
    if scope == "global":
        await state.update_data(ssvc_scope="global", ssvc_scope_id=0)
        await callback.message.edit_text(
            "📝 أرسل اسم السيرفر بالعربي:\n(مثال: سيرفر الألعاب السريع)",
            reply_markup=admin_back_kb(),
        )
        await state.set_state(AdminStoreServerStates.waiting_name)
        await callback.answer()
        return
    if scope == "category":
        targets = await DynamicService.get_all_categories(session)
    elif scope == "number_service":
        targets = await DynamicService.get_all_number_services(session)
    else:  # subcategory
        targets = await _all_subcategories(session)
    if not targets:
        await callback.answer("⚠️ لا توجد عناصر في هذا النطاق.", show_alert=True)
        return
    await callback.message.edit_text(
        f"{SCOPE_LABELS[scope]}\n\nاختر العنصر الذي تريد ربط السيرفر به:",
        reply_markup=admin_ssvc_target_kb(scope, targets, page),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:ssvc_target:"))
async def ssvc_target_selected(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    scope = parts[1]
    scope_id = int(parts[2])
    await state.update_data(ssvc_scope=scope, ssvc_scope_id=scope_id)
    await callback.message.edit_text(
        "📝 أرسل اسم السيرفر بالعربي:", reply_markup=admin_back_kb()
    )
    await state.set_state(AdminStoreServerStates.waiting_name)
    await callback.answer()


@router.message(AdminStoreServerStates.waiting_name)
async def ssvc_name_received(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("⚠️ أرسل اسم السيرفر.")
        return
    await state.update_data(ssvc_name=name)
    await message.answer("🎨 أرسل إيموجي للسيرفر (أو - لاستخدام 🖥):")
    await state.set_state(AdminStoreServerStates.waiting_emoji)


@router.message(AdminStoreServerStates.waiting_emoji)
async def ssvc_emoji_received(message: Message, state: FSMContext):
    emoji = message.text.strip()
    if emoji == "-":
        emoji = "🖥"
    await state.update_data(ssvc_emoji=emoji)
    await message.answer(
        "🔌 <b>اختر نوع المزود:</b>\n"
        "• مزود متجر/رشق/ألعاب (API)\n"
        "• مزود أرقام",
        reply_markup=admin_ssvc_provider_kind_kb(),
    )
    await state.set_state(AdminStoreServerStates.waiting_provider_kind)


@router.callback_query(F.data.startswith("admin:ssvc_provider_kind:"))
async def ssvc_provider_kind_selected(callback: CallbackQuery, state: FSMContext, session):
    kind = callback.data.rsplit(":", 1)[1]
    await state.update_data(ssvc_provider_kind=kind)
    if kind == "number":
        await callback.message.edit_text(
            "📱 اختر مزود الأرقام المرتبط بالسيرفر:",
            reply_markup=admin_ssvc_number_provider_kb(),
        )
    else:
        providers = await DynamicService.get_all_providers(session)
        await callback.message.edit_text(
            "🔌 اختر مزود المتجر/رشق/الألعاب المرتبط بالسيرفر:",
            reply_markup=admin_ssvc_api_provider_kb(providers),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:ssvc_number_provider:"))
async def ssvc_number_provider_selected(callback: CallbackQuery, state: FSMContext):
    provider_value = callback.data.rsplit(":", 1)[1]
    await state.update_data(ssvc_provider=provider_value)
    await _prompt_margin(callback, state)


@router.callback_query(F.data.startswith("admin:ssvc_api_provider:"))
async def ssvc_api_provider_selected(callback: CallbackQuery, state: FSMContext):
    try:
        provider_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("⚠️ مزود غير صحيح.", show_alert=True)
        return
    await state.update_data(ssvc_api_provider_id=provider_id)
    await _prompt_margin(callback, state)


async def _prompt_margin(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💰 <b>نسبة الربح لهذا السيرفر (%)</b>\n\n"
        "أرسل رقماً فقط:\n"
        "• 0 = سعر التكلفة بلا ربح\n"
        "• 30 = ربح 30% على سعر التكلفة\n"
        "• اتركه فارغاً أو «-» = استخدم هامش المنتج الحالي",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminStoreServerStates.waiting_margin)
    await callback.answer()


@router.message(AdminStoreServerStates.waiting_margin)
async def ssvc_margin_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    margin = None
    if raw not in ("", "-"):
        try:
            margin = Decimal(raw)
        except InvalidOperation:
            await message.answer("⚠️ أرسل رقماً صحيحاً (مثل: 30 أو 12.5) أو - للتخطي.")
            return
    data = await state.get_data()
    if margin is not None and margin < 0:
        await message.answer("⚠️ نسبة الربح لا يمكن أن تكون سالبة.")
        return
    scope = data.get("ssvc_scope")
    scope_id = data.get("ssvc_scope_id", 0)
    name = data.get("ssvc_name", "سيرفر")
    emoji = data.get("ssvc_emoji", "🖥")
    provider_kind = data.get("ssvc_provider_kind", "api")
    api_provider_id = data.get("ssvc_api_provider_id")
    provider_value = data.get("ssvc_provider")
    server = await StoreServerService.create(
        session=session,
        scope=scope,
        scope_id=int(scope_id or 0),
        name_ar=name,
        provider_kind=provider_kind,
        provider_value=provider_value,
        api_provider_id=int(api_provider_id) if api_provider_id else None,
        emoji=emoji,
        margin_percent=margin,
    )
    await state.clear()
    await message.answer(
        f"✅ <b>تمت إضافة السيرفر</b>\n\n"
        f"{server.emoji} <b>{server.name_ar}</b>\n"
        f"النطاق: {SCOPE_LABELS.get(server.scope, server.scope)}\n"
        f"المزود: {_provider_text(server)}\n"
        f"نسبة الربح: {server.margin_percent or '—'}%\n\n"
        "سيظهر الآن تلقائياً أمام المستخدم في هذا القسم.",
        reply_markup=admin_back_kb(),
    )


# ══════════════ تفاصيل/تعديل ══════════════


@router.callback_query(F.data.startswith("admin:ssvc_server:"))
async def ssvc_server_view(callback: CallbackQuery, session):
    server_id = int(callback.data.split(":")[2])
    server = await StoreServerService.get(session, server_id)
    if server is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    status = "🟢 مفعّل" if server.is_active else "⚪ معطّل"
    await callback.answer()
    await callback.message.edit_text(
        f"{server.emoji} <b>{server.name_ar}</b>\n\n"
        f"النطاق: {SCOPE_LABELS.get(server.scope, server.scope)} (#{server.scope_id})\n"
        f"الحالة: {status}\n"
        f"المزود: <b>{_provider_text(server)}</b>\n"
        f"نسبة الربح: <b>{server.margin_percent or '—'}%</b>\n"
        f"الترتيب: {server.sort_order}\n\n"
        "يظهر هذا السيرفر للمستخدم قبل عرض المنتجات في قسمه.",
        reply_markup=admin_store_server_detail_kb(server),
    )


@router.callback_query(F.data.startswith("admin:ssvc_toggle:"))
async def ssvc_toggle(callback: CallbackQuery, session):
    server_id = int(callback.data.split(":")[2])
    server = await StoreServerService.get(session, server_id)
    if server is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    await StoreServerService.update(session, server_id, is_active=not server.is_active)
    await callback.answer("✅ تم التحديث.")
    await ssvc_server_view(callback, session)


@router.callback_query(F.data.startswith("admin:ssvc_edit_name:"))
async def ssvc_edit_name(callback: CallbackQuery, state: FSMContext):
    server_id = int(callback.data.split(":")[2])
    await state.update_data(ssvc_edit_id=server_id, ssvc_edit_field="name")
    await callback.message.edit_text("📝 أرسل اسم السيرفر الجديد:", reply_markup=admin_back_kb())
    await state.set_state(AdminStoreServerStates.waiting_edit_value)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:ssvc_edit_emoji:"))
async def ssvc_edit_emoji(callback: CallbackQuery, state: FSMContext):
    server_id = int(callback.data.split(":")[2])
    await state.update_data(ssvc_edit_id=server_id, ssvc_edit_field="emoji")
    await callback.message.edit_text("🎨 أرسل الإيموجي الجديد (أو - للافتراضي):", reply_markup=admin_back_kb())
    await state.set_state(AdminStoreServerStates.waiting_edit_value)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:ssvc_edit_desc:"))
async def ssvc_edit_desc(callback: CallbackQuery, state: FSMContext):
    server_id = int(callback.data.split(":")[2])
    await state.update_data(ssvc_edit_id=server_id, ssvc_edit_field="description")
    await callback.message.edit_text("📝 أرسل وصفاً جديداً (أو - للمسح):", reply_markup=admin_back_kb())
    await state.set_state(AdminStoreServerStates.waiting_edit_value)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:ssvc_edit_margin:"))
async def ssvc_edit_margin(callback: CallbackQuery, state: FSMContext):
    server_id = int(callback.data.split(":")[2])
    await state.update_data(ssvc_edit_id=server_id, ssvc_edit_field="margin")
    await callback.message.edit_text(
        "💰 أرسل نسبة الربح الجديدة (%) (أو - لاستخدام هامش المنتج):",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminStoreServerStates.waiting_edit_value)
    await callback.answer()


@router.message(AdminStoreServerStates.waiting_edit_value)
async def ssvc_edit_value_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    server_id = data.get("ssvc_edit_id")
    field = data.get("ssvc_edit_field")
    value = (message.text or "").strip()
    if not server_id:
        await message.answer("⚠️ بيانات ناقصة.")
        await state.clear()
        return
    if field == "name" and value:
        await StoreServerService.update(session, server_id, name_ar=value)
    elif field == "emoji":
        await StoreServerService.update(session, server_id, emoji="🖥" if value == "-" else value)
    elif field == "description":
        await StoreServerService.update(session, server_id, description=None if value == "-" else value)
    elif field == "margin":
        margin = None
        if value not in ("", "-"):
            try:
                margin = Decimal(value)
            except InvalidOperation:
                await message.answer("⚠️ أرسل رقماً صحيحاً أو - للتخطي.")
                return
        await StoreServerService.update(session, server_id, margin_percent=margin)
    await message.answer("✅ تم التحديث.")
    await state.clear()


@router.callback_query(F.data.startswith("admin:ssvc_delete:"))
async def ssvc_delete(callback: CallbackQuery, session):
    server_id = int(callback.data.split(":")[2])
    server = await StoreServerService.get(session, server_id)
    if server is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    await StoreServerService.delete(session, server_id)
    await callback.answer("🗑 تم حذف السيرفر.")
    await store_servers_list(callback, session)


# ══════════════ أدوات داخلية ══════════════


async def _all_subcategories(session):
    categories = await DynamicService.get_all_categories(session)
    targets = []
    for category in categories:
        subs = await DynamicService.get_all_sub_categories(session, category.id)
        for sub in subs:
            sub.emoji = f"{category.emoji} {sub.emoji}"
            targets.append(sub)
    return targets

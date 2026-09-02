"""
إدارة المزودين V2 - محسّنة بالكامل.

يدعم:
- Wizard كامل لإضافة مزود (بروتوكول → نوع → اسم → URL → API Key → عملة → اختبار)
- سحب الخدمات في الخلفية مع إشعار عند الاكتمال
- عرض قائمة المزودين مع الحالة وعدد الخدمات
- البحث في خدمات المزود
- عرض الخدمات مع Pagination
- تعديل كامل لبيانات المزود
- حذف مع تأكيد
"""

import asyncio
import json
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database.models import (
    ApiProvider,
    ApiProviderType,
    ApiProtocolType,
    Category,
    CategoryType,
    ProductFulfillmentType,
    ProviderService,
    SubCategory,
)
from services.dynamic_service import DynamicService
from services.provider_sync_service import (
    ProviderSyncService,
)
from services.currency_service import CurrencyService
from services.settings_service import SettingsService
from states.states import (
    AdminApiProviderStates,
    AdminProviderServicesStates,
)
from keyboards.admin_providers_v2 import (
    providers_list_kb,
    select_protocol_kb,
    select_provider_type_kb,
    select_currency_kb,
    test_connection_kb,
    ask_sync_now_kb,
    provider_detail_kb,
    confirm_delete_provider_kb,
    provider_services_kb,
    provider_service_detail_kb,
    cancel_search_kb,
    sync_in_progress_kb,
    custom_provider_presets_kb,
    CUSTOM_PROVIDER_CONFIG_PRESETS,
    SERVICES_PER_PAGE,
)
from keyboards.admin import admin_back_kb
from filters.admin_filter import IsAdmin

logger = logging.getLogger(__name__)

router = Router(name="admin_api_providers")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════════════════════════════════════
# ══════════════ قائمة المزودين ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "admin:api_providers")
async def providers_list(callback: CallbackQuery, session):
    providers = await DynamicService.get_all_providers(session)

    if not providers:
        text = (
            "🔌 <b>إدارة المزودين</b>\n\n"
            "لا يوجد مزودين مسجلين حتى الآن.\n"
            "اضغط الزر أدناه لإضافة مزود جديد."
        )
    else:
        text = (
            "🔌 <b>إدارة المزودين</b>\n\n"
            f"عدد المزودين: <b>{len(providers)}</b>\n\n"
            "🟢 = مفعّل | 🔴 = معطّل\n"
            "اضغط على أي مزود لعرض التفاصيل."
        )

    await callback.message.edit_text(
        text,
        reply_markup=providers_list_kb(providers),
    )


# ══════════════════════════════════════════════
# ══════════════ إضافة مزود - الخطوة 1 ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "admin:aprov_add")
async def aprov_add_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🔌 <b>إضافة مزود جديد</b>\n\n"
        "الخطوة 1️⃣ من 6️⃣\n\n"
        "اختر نوع البروتوكول:\n\n"
        "📈 <b>SMM V2</b>: بروتوكول قياسي لمواقع "
        "رشق السوشيال (JumboSMM, SMMGold, PeakSMM, إلخ)\n\n"
        "🎮 <b>Games</b>: مزود REST JSON للألعاب والتطبيقات\n\n"
        "🛠 <b>Custom</b>: مسارات وحقول قابلة للتخصيص لأي API JSON",
        reply_markup=select_protocol_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_protocol_type)


@router.callback_query(F.data.startswith("admin:aprov_proto:"))
async def aprov_protocol_selected(callback: CallbackQuery, state: FSMContext):
    protocol_key = callback.data.split(":")[2]

    try:
        protocol_type = ApiProtocolType(protocol_key)
    except ValueError:
        await callback.answer("❌ بروتوكول غير صالح", show_alert=True)
        return

    if protocol_type == ApiProtocolType.SMS:
        await callback.answer(
            "ℹ️ مزودو SMS يضافون من إعدادات مفاتيح SMS، وليس من هذا القسم.",
            show_alert=True,
        )
        return

    await state.update_data(protocol_type=protocol_type.value)
    await callback.answer()

    if protocol_type == ApiProtocolType.CUSTOM:
        await callback.message.edit_text(
            "🛠 <b>إعداد مزود مخصص بدون كود</b>\n\n"
            "اختر قالباً جاهزاً ثم عدّل المسارات لاحقاً عند الحاجة، أو أرسل JSON يدوي.\n\n"
            "مثال: لو عندك موقع يبيع أي منتجات وعنده API، اختر قالب "
            "<b>متجر/منتجات REST</b> ثم أدخل رابط API ومفتاحه.",
            reply_markup=custom_provider_presets_kb(),
        )
        return

    await callback.message.edit_text(
        f"✅ البروتوكول: <b>{protocol_type.value.upper()}</b>\\n\\n"
        "الخطوة 2️⃣ من 6️⃣\\n\\n"
        "اختر نوع المزود:",
        reply_markup=select_provider_type_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_type)


@router.callback_query(F.data.startswith("admin:aprov_custom_preset:"))
async def aprov_custom_preset_selected(callback: CallbackQuery, state: FSMContext):
    preset_key = callback.data.rsplit(":", 1)[1]
    preset = CUSTOM_PROVIDER_CONFIG_PRESETS.get(preset_key)
    if preset is None:
        await callback.answer("قالب غير معروف.", show_alert=True)
        return
    await state.update_data(custom_config=json.dumps(preset, ensure_ascii=False))
    await callback.message.edit_text(
        "✅ تم اختيار قالب المزود المخصص.\n\n"
        "الخطوة 2️⃣ من 6️⃣\n\n"
        "اختر نوع القسم الذي يخدمه هذا المزود:",
        reply_markup=select_provider_type_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_type)
    await callback.answer()


CUSTOM_WIZARD_STEPS = [
    ("services_endpoint", "مسار جلب المنتجات/الخدمات", "/products"),
    ("services_path", "مكان قائمة المنتجات في الرد JSON", "data"),
    ("id_field", "حقل آيدي المنتج عند المزود", "id"),
    ("name_field", "حقل اسم المنتج", "name"),
    ("price_field", "حقل السعر", "price"),
    ("category_field", "حقل التصنيف/القسم عند المزود", "category"),
    ("order_endpoint", "مسار إنشاء الطلب", "/orders"),
    ("status_endpoint", "مسار فحص الطلب، استخدم {order_id}", "/orders/{order_id}"),
    ("order_id_field", "حقل رقم الطلب في رد الإنشاء", "id"),
    ("status_field", "حقل حالة الطلب في رد الفحص", "status"),
]


@router.callback_query(F.data == "admin:aprov_custom_wizard")
async def aprov_custom_wizard_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(custom_wizard={}, custom_wizard_index=0)
    await state.set_state(AdminApiProviderStates.waiting_custom_wizard_value)
    await _ask_custom_wizard_value(callback.message, state)
    await callback.answer()


async def _ask_custom_wizard_value(target, state: FSMContext) -> None:
    data = await state.get_data()
    index = int(data.get("custom_wizard_index", 0))
    key, label, default = CUSTOM_WIZARD_STEPS[index]
    await target.answer(
        "🧙 <b>معالج ربط مزود بدون JSON</b>\n\n"
        f"الخطوة {index + 1}/{len(CUSTOM_WIZARD_STEPS)}\n"
        f"{label}\n\n"
        f"الافتراضي: <code>{default}</code>\n\n"
        "أرسل القيمة، أو أرسل <code>-</code> لاستخدام الافتراضي."
    )


@router.message(AdminApiProviderStates.waiting_custom_wizard_value)
async def aprov_custom_wizard_value(message: Message, state: FSMContext):
    data = await state.get_data()
    index = int(data.get("custom_wizard_index", 0))
    values = dict(data.get("custom_wizard", {}))
    key, _label, default = CUSTOM_WIZARD_STEPS[index]
    raw = (message.text or "").strip()
    values[key] = default if raw in {"", "-"} else raw
    index += 1
    await state.update_data(custom_wizard=values, custom_wizard_index=index)

    if index < len(CUSTOM_WIZARD_STEPS):
        await _ask_custom_wizard_value(message, state)
        return

    config = CUSTOM_PROVIDER_CONFIG_PRESETS["store_rest"].copy()
    config["endpoints"] = dict(config.get("endpoints", {}))
    config["fields"] = dict(config.get("fields", {}))
    config["service_fields"] = dict(config.get("service_fields", {}))
    config["endpoints"]["services"] = values["services_endpoint"]
    config["endpoints"]["order"] = values["order_endpoint"]
    config["endpoints"]["status"] = values["status_endpoint"]
    config["fields"]["services"] = values["services_path"]
    config["fields"]["order_id"] = values["order_id_field"]
    config["fields"]["status"] = values["status_field"]
    config["service_fields"]["id"] = values["id_field"]
    config["service_fields"]["name"] = values["name_field"]
    config["service_fields"]["rate"] = values["price_field"]
    config["service_fields"]["category"] = values["category_field"]
    await state.update_data(
        custom_config=json.dumps(config, ensure_ascii=False),
        custom_wizard={},
        custom_wizard_index=0,
    )
    await message.answer(
        "✅ تم بناء إعداد الربط بدون كتابة JSON.\n\n"
        "الخطوة 2️⃣ من 6️⃣\n\n"
        "اختر نوع القسم الذي يخدمه هذا المزود:",
        reply_markup=select_provider_type_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_type)


@router.callback_query(F.data == "admin:aprov_custom_manual")
async def aprov_custom_manual(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "✍️ <b>إعداد JSON يدوي</b>\n\n"
        "أرسل JSON يحدد المسارات والحقول. مثال مختصر:\n"
        '<code>{"balance_optional":true,"endpoints":{"services":"/products","order":"/orders","status":"/orders/{order_id}"}}</code>\n\n'
        "إذا كان الموقع لا يملك API فلا يمكن الربط التلقائي؛ استخدم منتج يدوي أو مخزون رقمي.",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_custom_config)
    await callback.answer()


@router.message(AdminApiProviderStates.waiting_custom_config)
async def aprov_custom_config_received(
    message: Message,
    state: FSMContext,
):
    raw_config = (message.text or "").strip()
    try:
        custom_config = json.loads(raw_config or "{}")
    except json.JSONDecodeError:
        await message.answer("⚠️ أرسل JSON صحيحاً أو {}.")
        return
    if not isinstance(custom_config, dict):
        await message.answer("⚠️ إعداد المزود يجب أن يكون JSON من نوع object.")
        return

    await state.update_data(custom_config=json.dumps(custom_config, ensure_ascii=False))
    await message.answer(
        "✅ تم حفظ إعدادات المزود المخصص.\\n\\nالخطوة 2️⃣ من 6️⃣\\n\\nاختر نوع المزود:",
        reply_markup=select_provider_type_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_type)


@router.callback_query(F.data.startswith("admin:aprov_ptype:"))
async def aprov_type_selected(callback: CallbackQuery, state: FSMContext):
    type_key = callback.data.split(":")[2]

    try:
        provider_type = ApiProviderType(type_key)
    except ValueError:
        await callback.answer("❌ نوع غير صالح", show_alert=True)
        return

    await state.update_data(provider_type=provider_type.value)
    await callback.answer()

    await callback.message.edit_text(
        f"✅ النوع: <b>{provider_type.value}</b>\n\n"
        "الخطوة 3️⃣ من 6️⃣\n\n"
        "📝 أرسل اسم المزود:\n"
        "(مثال: <code>JumboSMM</code> أو "
        "<code>SMMGold</code>)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_name)


@router.message(AdminApiProviderStates.waiting_name)
async def aprov_name_received(message: Message, state: FSMContext):
    name = message.text.strip()

    if len(name) < 2:
        await message.answer("⚠️ الاسم قصير جداً.")
        return

    if len(name) > 64:
        await message.answer("⚠️ الاسم طويل جداً (الحد الأقصى 64 حرف).")
        return

    await state.update_data(name=name)

    await message.answer(
        f"✅ الاسم: <b>{name}</b>\n\n"
        "الخطوة 4️⃣ من 6️⃣\n\n"
        "🔗 أرسل رابط API الخاص بالمزود:\n"
        "(مثال: <code>https://jumbosmm.com/api/v2</code>)"
    )
    await state.set_state(AdminApiProviderStates.waiting_api_url)


@router.message(AdminApiProviderStates.waiting_api_url)
async def aprov_url_received(message: Message, state: FSMContext):
    url = message.text.strip()

    if not url.startswith(("http://", "https://")):
        await message.answer("⚠️ يجب أن يبدأ الرابط بـ http:// أو https://")
        return

    if len(url) > 500:
        await message.answer("⚠️ الرابط طويل جداً.")
        return

    await state.update_data(api_url=url)

    await message.answer("✅ الرابط محفوظ\n\nالخطوة 5️⃣ من 6️⃣\n\n🔑 أرسل مفتاح API (API Key):")
    await state.set_state(AdminApiProviderStates.waiting_api_key)


@router.message(AdminApiProviderStates.waiting_api_key)
async def aprov_key_received(message: Message, state: FSMContext):
    api_key = message.text.strip()

    if len(api_key) < 5:
        await message.answer("⚠️ المفتاح قصير جداً. تأكد من إدخاله كاملاً.")
        return

    await state.update_data(api_key=api_key)

    try:
        await message.delete()
    except Exception:
        pass

    await message.answer(
        "✅ المفتاح محفوظ\n\nالخطوة 6️⃣ من 6️⃣\n\n💱 اختر عملة المزود:\n(عملة الأسعار في لوحة المزود)",
        reply_markup=select_currency_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_currency)


@router.callback_query(F.data.startswith("admin:aprov_curr:"))
async def aprov_currency_selected(callback: CallbackQuery, state: FSMContext, session):
    currency_code = callback.data.split(":")[2]

    if currency_code == "custom":
        await callback.message.edit_text(
            "✏️ أرسل رمز العملة (3 أحرف):\n(مثال: <code>USD</code> أو <code>UAH</code>)"
        )
        await state.set_state(AdminApiProviderStates.waiting_currency)
        await state.update_data(waiting_custom_currency=True)
        await callback.answer()
        return

    await state.update_data(currency=currency_code)

    rate = await CurrencyService.get_rate_to_usd(currency_code, session)
    await state.update_data(rate_to_usd=str(rate))

    await callback.answer()
    await _show_summary_and_test(callback.message, state)


@router.message(AdminApiProviderStates.waiting_currency)
async def aprov_custom_currency_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    if not data.get("waiting_custom_currency"):
        return

    currency = message.text.strip().upper()

    if len(currency) != 3 or not currency.isalpha():
        await message.answer("⚠️ العملة يجب أن تكون 3 أحرف فقط (مثل USD)")
        return

    await state.update_data(currency=currency)
    await state.update_data(waiting_custom_currency=False)

    rate = await CurrencyService.get_rate_to_usd(currency, session)
    await state.update_data(rate_to_usd=str(rate))

    await _show_summary_and_test(message, state)


async def _show_summary_and_test(message: Message, state: FSMContext):
    """يعرض ملخص البيانات وزر اختبار الاتصال."""
    data = await state.get_data()

    text = (
        "📋 <b>ملخص بيانات المزود</b>\n\n"
        f"🔌 البروتوكول: <b>{data['protocol_type'].upper()}</b>\n"
        f"📁 النوع: <b>{data['provider_type']}</b>\n"
        f"📝 الاسم: <b>{data['name']}</b>\n"
        f"🔗 URL: <code>{data['api_url'][:50]}...</code>\n"
        f"💱 العملة: <b>{data['currency']}</b>\n"
        f"💵 سعر الصرف: 1 {data['currency']} = "
        f"{data['rate_to_usd']} USD\n\n"
        "اضغط الزر أدناه لاختبار الاتصال وحفظ المزود:"
    )

    await message.answer(
        text,
        reply_markup=test_connection_kb(),
    )
    # ══════════════════════════════════════════════


# ══════════════ اختبار الاتصال + الحفظ ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "admin:aprov_test")
async def aprov_test_and_save(callback: CallbackQuery, state: FSMContext, session):
    data = await state.get_data()

    required_fields = [
        "protocol_type",
        "provider_type",
        "name",
        "api_url",
        "api_key",
        "currency",
    ]
    for field in required_fields:
        if field not in data:
            await callback.answer(f"⚠️ حقل ناقص: {field}", show_alert=True)
            return

    await callback.answer("⏳ جاري اختبار الاتصال...")

    test_msg = await callback.message.edit_text(
        "⏳ <b>جاري اختبار الاتصال...</b>\n\n"
        f"مزود: {data['name']}\n"
        f"URL: <code>{data['api_url'][:50]}...</code>"
    )

    try:
        temp_provider = ApiProvider(
            name=data["name"],
            type=ApiProviderType(data["provider_type"]),
            protocol_type=ApiProtocolType(data["protocol_type"]),
            api_url=data["api_url"],
            api_key=data["api_key"],
            currency=data["currency"],
            rate_to_usd=Decimal(data["rate_to_usd"]),
            custom_config=data.get("custom_config"),
        )
    except Exception as e:
        await test_msg.edit_text(
            f"❌ خطأ في البيانات: {e}",
            reply_markup=admin_back_kb(),
        )
        await state.clear()
        return

    success, message_text, balance, currency = await ProviderSyncService.test_provider_connection(
        temp_provider
    )

    if not success:
        await test_msg.edit_text(
            f"❌ <b>فشل الاتصال بالمزود!</b>\n\n"
            f"السبب: <code>{message_text}</code>\n\n"
            "تأكد من:\n"
            "• صحة الـ API URL\n"
            "• صحة الـ API Key\n"
            "• اتصال الإنترنت\n"
            "• أن المزود يدعم البروتوكول المحدد",
            reply_markup=admin_back_kb(),
        )
        await state.clear()
        return

    provider = ApiProvider(
        name=data["name"],
        type=ApiProviderType(data["provider_type"]),
        protocol_type=ApiProtocolType(data["protocol_type"]),
        api_url=data["api_url"],
        api_key=data["api_key"],
        currency=currency or data["currency"],
        rate_to_usd=Decimal(data["rate_to_usd"]),
        balance=balance,
        custom_config=data.get("custom_config"),
        is_active=True,
        priority=1,
        low_balance_threshold=Decimal("10"),
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)

    logger.info(f"تم إضافة مزود جديد: #{provider.id} - {provider.name}")

    balance_display = f"{balance} {currency}" if balance else "غير معروف"
    balance_usd = None
    if balance and currency:
        balance_usd = (balance * Decimal(data["rate_to_usd"])).quantize(Decimal("0.01"))

    text = (
        "✅ <b>تم إضافة المزود بنجاح!</b>\n\n"
        f"📝 الاسم: <b>{provider.name}</b>\n"
        f"🔌 البروتوكول: {provider.protocol_type.value.upper()}\n"
        f"💰 الرصيد: <b>{balance_display}</b>"
    )
    if balance_usd:
        text += f"\n💵 يعادل: <b>{balance_usd}$</b>"
    text += "\n\n<b>هل تريد سحب خدمات المزود الآن؟</b>\nقد يستغرق وقتاً إذا كانت الخدمات كثيرة."

    await test_msg.edit_text(
        text,
        reply_markup=ask_sync_now_kb(provider.id),
    )
    await state.clear()


# ══════════════════════════════════════════════
# ══════════════ تفاصيل المزود ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_view:"))
async def aprov_view(callback: CallbackQuery, session):
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)

    if not provider:
        await callback.answer("⚠️ المزود غير موجود.", show_alert=True)
        return

    await _show_provider_details(callback.message, provider, edit=True)


async def _show_provider_details(message, provider: ApiProvider, edit: bool = True):
    """يعرض تفاصيل مزود."""
    status = "🟢 مفعّل" if provider.is_active else "🔴 معطّل"
    type_label = {
        "smm": "📈 رشق SMM",
        "games": "🎮 ألعاب",
        "apps": "📱 تطبيقات",
        "balances": "💳 أرصدة",
        "cards": "💳 بطاقات/فيز",
        "subscriptions": "🔐 اشتراكات",
        "verification": "✅ توثيق",
        "codes": "🎟 أكواد",
        "store": "🛍 متجر عام",
        "custom": "🧩 مخصص",
        "numbers": "📞 أرقام",
    }.get(provider.type.value, provider.type.value)

    balance_text = (
        f"{provider.balance} {provider.currency}" if provider.balance is not None else "غير معروف"
    )

    balance_usd = None
    if provider.balance is not None:
        balance_usd = (provider.balance * provider.rate_to_usd).quantize(Decimal("0.01"))

    last_check = (
        provider.last_checked_at.strftime("%Y-%m-%d %H:%M") if provider.last_checked_at else "—"
    )
    last_sync = (
        provider.last_sync_at.strftime("%Y-%m-%d %H:%M") if provider.last_sync_at else "لم يتم بعد"
    )

    api_url_display = (
        provider.api_url[:40] + "..." if len(provider.api_url) > 40 else provider.api_url
    )
    api_key_display = provider.api_key[:8] + "***" if len(provider.api_key) > 8 else "***"

    text = (
        f"🔌 <b>{provider.name}</b>\n\n"
        f"📁 النوع: {type_label}\n"
        f"🔧 البروتوكول: "
        f"{provider.protocol_type.value.upper()}\n"
        f"📊 الحالة: {status}\n"
        f"🎯 الأولوية: {provider.priority}\n\n"
        f"💰 الرصيد: <b>{balance_text}</b>\n"
    )
    if balance_usd:
        text += f"💵 يعادل: <b>{balance_usd}$</b>\n"
    text += (
        f"💱 العملة: {provider.currency}\n"
        f"📈 سعر الصرف: 1 {provider.currency} = "
        f"{provider.rate_to_usd}$\n\n"
        f"🔗 URL: <code>{api_url_display}</code>\n"
        f"🔑 API Key: <code>{api_key_display}</code>\n\n"
        f"📦 عدد الخدمات: <b>"
        f"{provider.total_services or 0}</b>\n"
        f"🕐 آخر فحص: {last_check}\n"
        f"🔄 آخر مزامنة: {last_sync}\n"
    )

    if provider.last_error:
        text += f"\n⚠️ آخر خطأ: <code>{provider.last_error[:150]}</code>"

    kb = provider_detail_kb(provider)

    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
        except Exception:
            await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ══════════════════════════════════════════════
# ══════════════ تفعيل / تعطيل ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_toggle:"))
async def aprov_toggle(callback: CallbackQuery, session):
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)

    if not provider:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    provider.is_active = not provider.is_active
    await session.commit()

    status_text = "✅ تم تفعيل" if provider.is_active else "❌ تم تعطيل"
    await callback.answer(f"{status_text} المزود.")

    await session.refresh(provider)
    await _show_provider_details(callback.message, provider, edit=True)


# ══════════════════════════════════════════════
# ══════════════ تحديث الرصيد ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_balance:"))
async def aprov_check_balance(callback: CallbackQuery, session):
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)

    if not provider:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    await callback.answer("⏳ جاري فحص الرصيد...")

    success, msg = await ProviderSyncService.update_provider_balance(provider_id)

    await session.refresh(provider)

    if success:
        await callback.message.answer(f"✅ <b>{provider.name}</b>\n{msg}")
    else:
        await callback.message.answer(f"❌ فشل تحديث الرصيد:\n<code>{msg}</code>")

    await _show_provider_details(callback.message, provider, edit=False)


# ══════════════════════════════════════════════
# ══════════════ مزامنة الخدمات ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_sync:"))
async def aprov_sync_services(callback: CallbackQuery, session, bot):
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)

    if not provider:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    await callback.answer("⏳ جاري بدء المزامنة...")

    progress_msg = await callback.message.edit_text(
        f"🔄 <b>جاري مزامنة خدمات {provider.name}...</b>\n\n"
        "⏱ قد تستغرق العملية عدة دقائق حسب عدد الخدمات.\n"
        "سيصلك إشعار عند الاكتمال.",
        reply_markup=sync_in_progress_kb(provider_id),
    )

    admin_chat_id = callback.from_user.id

    asyncio.create_task(
        _sync_in_background(
            bot=bot,
            provider_id=provider_id,
            admin_chat_id=admin_chat_id,
            progress_message_id=progress_msg.message_id,
        )
    )


async def _sync_in_background(
    bot,
    provider_id: int,
    admin_chat_id: int,
    progress_message_id: int,
):
    """ينفذ المزامنة في الخلفية."""
    try:
        result = await ProviderSyncService.sync_provider_services(provider_id)

        try:
            await bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=progress_message_id,
                text=result.summary(),
                reply_markup=sync_in_progress_kb(provider_id),
            )
        except Exception:
            await bot.send_message(
                chat_id=admin_chat_id,
                text=result.summary(),
                reply_markup=sync_in_progress_kb(provider_id),
            )
    except Exception as e:
        logger.error(f"خطأ في مزامنة خلفية: {e}")
        try:
            await bot.send_message(
                chat_id=admin_chat_id,
                text=(f"❌ فشل المزامنة:\n<code>{str(e)[:200]}</code>"),
            )
        except Exception:
            pass


# ══════════════════════════════════════════════
# ══════════════ عرض خدمات المزود ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_services:"))
async def aprov_services_list(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    provider_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    provider = await session.get(ApiProvider, provider_id)
    if not provider:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    await state.clear()

    total_count = await ProviderSyncService.get_provider_services_count(
        provider_id, active_only=True
    )

    if total_count == 0:
        await callback.message.edit_text(
            f"📦 <b>خدمات {provider.name}</b>\n\n"
            "⚠️ لا توجد خدمات مسحوبة.\n"
            "اضغط 'مزامنة الخدمات' لسحبها.",
            reply_markup=sync_in_progress_kb(provider_id),
        )
        return

    services = await ProviderSyncService.get_provider_services(
        provider_id=provider_id,
        limit=SERVICES_PER_PAGE,
        offset=page * SERVICES_PER_PAGE,
        active_only=True,
    )

    total_pages = max(
        1,
        (total_count + SERVICES_PER_PAGE - 1) // SERVICES_PER_PAGE,
    )

    text = (
        f"📦 <b>خدمات {provider.name}</b>\n\n"
        f"📊 المجموع: <b>{total_count}</b> خدمة\n"
        f"📄 الصفحة: <b>{page + 1}/{total_pages}</b>\n\n"
        "اضغط على أي خدمة لعرض التفاصيل:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=provider_services_kb(
            provider_id=provider_id,
            services=services,
            current_page=page,
            total_count=total_count,
        ),
    )
    await callback.answer()


# ══════════════════════════════════════════════
# ══════════════ البحث في الخدمات ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_search:"))
async def aprov_search_start(callback: CallbackQuery, state: FSMContext):
    provider_id = int(callback.data.split(":")[2])
    await state.update_data(search_provider_id=provider_id)

    await callback.message.edit_text(
        "🔍 <b>البحث في خدمات المزود</b>\n\n"
        "أرسل كلمة أو جملة للبحث:\n"
        "(البحث في: الاسم، التصنيف، الآيدي)",
        reply_markup=cancel_search_kb(provider_id),
    )
    await state.set_state(AdminProviderServicesStates.waiting_search_query)
    await callback.answer()


@router.message(AdminProviderServicesStates.waiting_search_query)
async def aprov_search_query_received(message: Message, state: FSMContext, session):
    query = message.text.strip()

    if len(query) < 1:
        await message.answer("⚠️ أرسل كلمة بحث صالحة.")
        return

    data = await state.get_data()
    provider_id = data.get("search_provider_id")

    if not provider_id:
        await message.answer("⚠️ جلسة منتهية.")
        await state.clear()
        return

    provider = await session.get(ApiProvider, provider_id)
    if not provider:
        await message.answer("⚠️ المزود غير موجود.")
        await state.clear()
        return

    await state.update_data(search_query=query)

    services = await ProviderSyncService.get_provider_services(
        provider_id=provider_id,
        limit=SERVICES_PER_PAGE,
        offset=0,
        active_only=True,
        search=query,
    )

    if not services:
        await message.answer(
            f"🔍 <b>البحث عن:</b> {query}\n\n❌ لا توجد نتائج.",
            reply_markup=cancel_search_kb(provider_id),
        )
        return

    total_search = len(services)

    text = (
        f"🔍 <b>نتائج البحث</b>\n\n"
        f"🔎 الكلمة: <code>{query}</code>\n"
        f"📊 النتائج: <b>{total_search}+</b>\n\n"
        "اضغط على أي خدمة للتفاصيل:"
    )

    await message.answer(
        text,
        reply_markup=provider_services_kb(
            provider_id=provider_id,
            services=services,
            current_page=0,
            total_count=total_search,
            search_query=query,
        ),
    )
    await state.set_state(None)


# ══════════════════════════════════════════════
# ══════════════ تفاصيل خدمة ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_svc:"))
async def aprov_service_view(callback: CallbackQuery, session):
    service_id = int(callback.data.split(":")[2])
    service = await session.get(ProviderService, service_id)

    if not service:
        await callback.answer("⚠️ الخدمة غير موجودة.", show_alert=True)
        return

    provider = await session.get(ApiProvider, service.api_provider_id)
    if not provider:
        await callback.answer("⚠️ المزود غير موجود.", show_alert=True)
        return

    from sqlalchemy import func
    from database.models import Product

    products_count_result = await session.execute(
        select(func.count(Product.id)).where(Product.provider_service_ref_id == service.id)
    )
    products_count = products_count_result.scalar_one()

    text = (
        f"📦 <b>تفاصيل الخدمة</b>\n\n"
        f"🔌 المزود: {provider.name}\n"
        f"🆔 الآيدي: <code>{service.external_service_id}</code>\n"
        f"📝 الاسم: <b>{service.name}</b>\n"
    )

    if service.category:
        text += f"📁 التصنيف: {service.category}\n"
    if service.service_type:
        text += f"🏷 النوع: {service.service_type}\n"

    text += (
        f"\n💰 <b>السعر عند المزود:</b>\n"
        f"• {service.rate} {provider.currency} / 1000\n"
        f"• {service.rate_usd}$ / 1000\n\n"
        f"📊 <b>الكمية:</b>\n"
        f"• الحد الأدنى: {service.min_quantity:,}\n"
        f"• الحد الأقصى: {service.max_quantity:,}\n\n"
        f"⚙️ <b>المتطلبات:</b>\n"
    )
    if service.requires_link:
        text += "• ✅ رابط\n"
    if service.requires_player_id:
        text += "• ✅ Player ID\n"
    if service.requires_quantity:
        text += "• ✅ الكمية\n"

    text += (
        f"\n🔧 <b>الميزات:</b>\n"
        f"• Refill: "
        f"{'✅' if service.supports_refill else '❌'}\n"
        f"• Cancel: "
        f"{'✅' if service.supports_cancel else '❌'}\n\n"
        f"📦 المنتجات المرتبطة: <b>{products_count}</b>"
    )

    if service.description:
        desc = service.description[:200]
        text += f"\n\n📄 <b>الوصف:</b>\n<i>{desc}</i>"

    await callback.message.edit_text(
        text,
        reply_markup=provider_service_detail_kb(service),
    )
    await callback.answer()


# ══════════════════════════════════════════════
# ══════════════ إنشاء منتج تلقائي من خدمة مزود ══════════════
# ══════════════════════════════════════════════


_PROVIDER_CATEGORY_DEFAULTS = {
    "smm": (CategoryType.SMM, "قسم الرشق", "📈"),
    "games": (CategoryType.GAMES, "قسم شحن الألعاب", "🎮"),
    "apps": (CategoryType.APPS, "قسم شحن التطبيقات", "📱"),
    "balances": (CategoryType.BALANCES, "قسم الأرصدة", "💳"),
    "cards": (CategoryType.CARDS, "قسم البطاقات والفيز", "💳"),
    "subscriptions": (CategoryType.SUBSCRIPTIONS, "قسم الاشتراكات الرقمية", "🔐"),
    "verification": (CategoryType.VERIFICATION, "قسم توثيق الحسابات", "✅"),
    "codes": (CategoryType.CODES, "قسم الأكواد الرقمية", "🎟"),
    "store": (CategoryType.CUSTOM, "متجر عام", "🛍"),
    "custom": (CategoryType.CUSTOM, "قسم مخصص", "🧩"),
}


async def _ensure_provider_category(session, provider: ApiProvider) -> Category:
    category_type, name, emoji = _PROVIDER_CATEGORY_DEFAULTS.get(
        provider.type.value, (CategoryType.CUSTOM, "قسم مخصص", "🧩")
    )
    result = await session.execute(select(Category).where(Category.type == category_type))
    category = result.scalar_one_or_none()
    if category is not None:
        return category
    category = Category(
        name_ar=name,
        emoji=emoji,
        type=category_type,
        is_active=True,
        sort_order=100,
    )
    session.add(category)
    await session.flush()
    return category


async def _ensure_service_subcategory(session, category: Category, service: ProviderService) -> SubCategory:
    from services.smm_catalog import resolve_smm_app

    raw_name = (service.category or service.service_type or "منتجات عامة").strip()
    matched = None
    if category.type == CategoryType.SMM:
        matched = (
            resolve_smm_app(service.category or "")
            or resolve_smm_app(service.service_type or "")
            or resolve_smm_app(service.name or "")
        )

    if matched is not None:
        name = matched.name_ar
        emoji = matched.emoji
    else:
        name = (raw_name or "منتجات عامة")[:64]
        emoji = category.emoji

    result = await session.execute(
        select(SubCategory).where(
            SubCategory.category_id == category.id,
            SubCategory.name_ar == name,
        )
    )
    subcategory = result.scalar_one_or_none()
    if subcategory is not None:
        if matched is not None:
            subcategory.name_ar = matched.name_ar
            subcategory.emoji = matched.emoji
        return subcategory

    if matched is not None:
        existing_result = await session.execute(
            select(SubCategory).where(SubCategory.category_id == category.id)
        )
        for sub in existing_result.scalars().all():
            haystack = f"{sub.emoji or ''} {sub.name_ar or ''}"
            resolved = resolve_smm_app(haystack) or resolve_smm_app(sub.name_ar or "")
            if resolved is not None and resolved.name_ar == matched.name_ar:
                sub.name_ar = matched.name_ar
                sub.emoji = matched.emoji
                return sub

    subcategory = SubCategory(
        category_id=category.id,
        name_ar=name,
        emoji=emoji,
        description=f"منتجات {name}",
        is_active=True,
        sort_order=100,
    )
    session.add(subcategory)
    await session.flush()
    return subcategory


@router.callback_query(F.data.startswith("admin:aprov_create_product:"))
async def create_product_from_provider_service(callback: CallbackQuery, session):
    service_id = int(callback.data.rsplit(":", 1)[1])
    service = await session.get(ProviderService, service_id)
    if service is None:
        await callback.answer("الخدمة غير موجودة.", show_alert=True)
        return
    provider = await session.get(ApiProvider, service.api_provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return

    margin_percent = await SettingsService.get_decimal(
        "default_profit_margin_percent", Decimal("50")
    )
    cost = service.rate_usd or Decimal("0")
    sell_price = (cost * (Decimal("1") + margin_percent / Decimal("100"))).quantize(
        Decimal("0.0001")
    )
    if sell_price <= 0:
        sell_price = Decimal("0.0001")

    category = await _ensure_provider_category(session, provider)
    subcategory = await _ensure_service_subcategory(session, category, service)
    await session.commit()

    product = await DynamicService.create_product(
        session=session,
        sub_category_id=subcategory.id,
        name_ar=service.name[:128],
        description=(service.description or f"منتج مستورد تلقائياً من {provider.name}")[:500],
        price_usd=sell_price,
        cost_price_usd=cost,
        api_provider_id=provider.id,
        provider_service_id=service.external_service_id,
        provider_service_ref_id=service.id,
        fulfillment_type=ProductFulfillmentType.API,
        min_quantity=service.min_quantity or 1,
        max_quantity=service.max_quantity or 1,
        requires_link=service.requires_link,
        requires_player_id=service.requires_player_id,
        requires_quantity=service.requires_quantity,
    )
    await callback.message.edit_text(
        "✅ <b>تم إنشاء المنتج تلقائياً من خدمة المزود</b>\n\n"
        f"📦 المنتج: <b>{product.name_ar}</b>\n"
        f"📂 القسم: {category.emoji} {category.name_ar} / {subcategory.name_ar}\n"
        f"🔌 المزود: {provider.name}\n"
        f"🆔 خدمة المزود: <code>{service.external_service_id}</code>\n"
        f"💵 التكلفة: {cost}$\n"
        f"💰 سعر البيع المقترح: {sell_price}$\n\n"
        "تقدر الآن تعدّل الاسم والسعر والمتطلبات من إدارة المنتجات، أو تضيفه كزر رئيسي من تفاصيل المنتج.",
        reply_markup=admin_back_kb(),
    )
    await callback.answer("✅ تم إنشاء المنتج.")


# ══════════════════════════════════════════════
# ══════════════ تعديل بيانات المزود ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_edit_name:"))
async def aprov_edit_name_start(callback: CallbackQuery, state: FSMContext):
    provider_id = int(callback.data.split(":")[2])
    await state.update_data(
        edit_provider_id=provider_id,
        edit_field="name",
    )
    await callback.message.edit_text(
        "📝 أرسل الاسم الجديد للمزود:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_edit_value)


@router.callback_query(F.data.startswith("admin:aprov_edit_key:"))
async def aprov_edit_key_start(callback: CallbackQuery, state: FSMContext):
    provider_id = int(callback.data.split(":")[2])
    await state.update_data(
        edit_provider_id=provider_id,
        edit_field="api_key",
    )
    await callback.message.edit_text(
        "🔑 أرسل مفتاح API الجديد:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_edit_value)


@router.callback_query(F.data.startswith("admin:aprov_edit_url:"))
async def aprov_edit_url_start(callback: CallbackQuery, state: FSMContext):
    provider_id = int(callback.data.split(":")[2])
    await state.update_data(
        edit_provider_id=provider_id,
        edit_field="api_url",
    )
    await callback.message.edit_text(
        "🔗 أرسل رابط API الجديد:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_edit_value)


@router.callback_query(F.data.startswith("admin:aprov_edit_rate:"))
async def aprov_edit_rate_start(callback: CallbackQuery, state: FSMContext):
    provider_id = int(callback.data.split(":")[2])
    await state.update_data(
        edit_provider_id=provider_id,
        edit_field="rate_to_usd",
    )
    await callback.message.edit_text(
        "💱 أرسل سعر الصرف الجديد (1 عملة = X دولار):\n(مثال: <code>0.011</code> للروبل)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminApiProviderStates.waiting_edit_value)


@router.message(AdminApiProviderStates.waiting_edit_value)
async def aprov_edit_value_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    provider_id = data.get("edit_provider_id")
    field = data.get("edit_field")

    if not provider_id or not field:
        await message.answer("⚠️ جلسة منتهية.")
        await state.clear()
        return

    provider = await session.get(ApiProvider, provider_id)
    if not provider:
        await message.answer("⚠️ المزود غير موجود.")
        await state.clear()
        return

    value = message.text.strip()

    if field == "name":
        if len(value) < 2 or len(value) > 64:
            await message.answer("⚠️ الاسم يجب أن يكون بين 2 و 64 حرف.")
            return
        provider.name = value

    elif field == "api_key":
        if len(value) < 5:
            await message.answer("⚠️ المفتاح قصير جداً.")
            return
        provider.api_key = value
        try:
            await message.delete()
        except Exception:
            pass

    elif field == "api_url":
        if not value.startswith(("http://", "https://")):
            await message.answer("⚠️ يجب أن يبدأ بـ http:// أو https://")
            return
        provider.api_url = value

    elif field == "rate_to_usd":
        try:
            rate = Decimal(value)
            if rate <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
            return
        provider.rate_to_usd = rate

    await session.commit()
    await message.answer(f"✅ تم تحديث {field} بنجاح.")
    await state.clear()

    await session.refresh(provider)
    await _show_provider_details(message, provider, edit=False)


# ══════════════════════════════════════════════
# ══════════════ حذف المزود ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:aprov_delete_confirm:"))
async def aprov_delete_confirm(callback: CallbackQuery, session):
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)

    if not provider:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    services_count = await ProviderSyncService.get_provider_services_count(
        provider_id, active_only=False
    )

    from sqlalchemy import func
    from database.models import Product

    products_result = await session.execute(
        select(func.count(Product.id)).where(Product.api_provider_id == provider_id)
    )
    products_count = products_result.scalar_one()

    text = (
        f"⚠️ <b>تأكيد حذف المزود</b>\n\n"
        f"سيتم حذف:\n"
        f"• المزود: <b>{provider.name}</b>\n"
        f"• {services_count} خدمة مسحوبة\n"
    )
    if products_count > 0:
        text += (
            f"\n⚠️ يوجد <b>{products_count}</b> منتج "
            f"مرتبط بهذا المزود!\n"
            f"سيتم فك ارتباطها (لن تُحذف).\n"
        )
    text += "\n<b>هل أنت متأكد؟</b>"

    await callback.message.edit_text(
        text,
        reply_markup=confirm_delete_provider_kb(provider_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:aprov_delete:"))
async def aprov_delete(callback: CallbackQuery, session):
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)

    if not provider:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    provider_name = provider.name

    from sqlalchemy import update
    from database.models import Product

    await session.execute(
        update(Product)
        .where(Product.api_provider_id == provider_id)
        .values(
            api_provider_id=None,
            provider_service_ref_id=None,
        )
    )

    await session.delete(provider)
    await session.commit()

    await callback.answer(f"🗑 تم حذف المزود: {provider_name}")

    logger.info(f"تم حذف المزود: #{provider_id} - {provider_name}")

    await providers_list(callback, session)


# ══════════════════════════════════════════════
# ══════════════ noop (زر بدون فعل) ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    await callback.answer()

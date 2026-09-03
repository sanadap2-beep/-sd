"""
إدارة المنتجات من لوحة الأدمن.
"""

from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from database.models import (
    ApiProvider,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductStatus,
    ProviderService,
    ProviderServiceStatus,
)
from services.dynamic_service import DynamicService
from services.inventory_service import InventoryService
from states.states import AdminProductStates
from keyboards.admin import (
    admin_products_list_kb,
    admin_product_detail_kb,
    admin_back_kb,
)
from filters.admin_filter import IsAdmin

router = Router(name="admin_products")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════ قائمة المنتجات (عبر القسم الفرعي) ══════════════


@router.callback_query(F.data == "admin:products_menu")
async def products_menu(callback: CallbackQuery, session):
    categories = await DynamicService.get_all_categories(session)
    if not categories:
        await callback.message.edit_text(
            "⚠️ لا توجد أقسام. أنشئ قسماً أولاً.",
            reply_markup=admin_back_kb(),
        )
        return

    b = InlineKeyboardBuilder()
    for cat in categories:
        b.button(
            text=f"{cat.emoji} {cat.name_ar}",
            callback_data=f"admin:prods_cat:{cat.id}",
        )
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(2)
    await callback.message.edit_text(
        "📦 <b>إدارة المنتجات</b>\n\nاختر القسم الرئيسي:",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("admin:prods_cat:"))
async def products_cat_selected(callback: CallbackQuery, session):
    cat_id = int(callback.data.split(":")[2])
    sub_cats = await DynamicService.get_all_sub_categories(session, cat_id)
    if not sub_cats:
        await callback.message.edit_text(
            "⚠️ لا توجد أقسام فرعية. أنشئ قسماً فرعياً أولاً.",
            reply_markup=admin_back_kb(),
        )
        return

    b = InlineKeyboardBuilder()
    for sub in sub_cats:
        b.button(
            text=f"{sub.emoji} {sub.name_ar}",
            callback_data=f"admin:prods:{sub.id}",
        )
    b.button(text="🔙 رجوع", callback_data="admin:products_menu")
    b.adjust(2)
    await callback.message.edit_text(
        "📦 اختر القسم الفرعي:",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("admin:prods:"))
async def products_list(callback: CallbackQuery, session):
    sub_id = int(callback.data.split(":")[2])
    sub = await DynamicService.get_sub_category(session, sub_id)
    if not sub:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    products = await DynamicService.get_all_products(session, sub_id)
    await callback.message.edit_text(
        f"📦 <b>منتجات {sub.emoji} {sub.name_ar}</b>\n\n🟢 = مفعّل | ⚪ = معطّل",
        reply_markup=admin_products_list_kb(sub_id, products),
    )


# ══════════════ إضافة منتج ══════════════


@router.callback_query(F.data.startswith("admin:prod_add:"))
async def prod_add_start(callback: CallbackQuery, state: FSMContext):
    sub_id = int(callback.data.split(":")[2])
    await state.update_data(prod_sub_id=sub_id)
    await callback.message.edit_text(
        "📝 أرسل اسم المنتج بالعربي:\n(مثال: 60 UC ببجي / 1000 متابع إنستا)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductStates.waiting_name)


@router.message(AdminProductStates.waiting_name)
async def prod_name_received(message: Message, state: FSMContext):
    await state.update_data(prod_name=message.text.strip())
    await message.answer(
        "💰 أرسل سعر البيع بالدولار:\n"
        "(مثال: 1.50)\n\n"
        "📌 لقسم الرشق: هذا السعر هو <b>لكل 1000</b> وليس لكل 100."
    )
    await state.set_state(AdminProductStates.waiting_price)


@router.message(AdminProductStates.waiting_price)
async def prod_price_received(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip())
        if not price.is_finite() or price <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(prod_price=str(price))
    await message.answer(
        "💵 أرسل سعر التكلفة بالدولار (سعرك عند المزود):\n(أو أرسل 0 إذا لا ينطبق)"
    )
    await state.set_state(AdminProductStates.waiting_cost_price)


@router.message(AdminProductStates.waiting_cost_price)
async def prod_cost_received(message: Message, state: FSMContext, session):
    try:
        cost = Decimal((message.text or "").strip())
        if not cost.is_finite() or cost < 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("⚠️ أرسل رقماً صحيحاً غير سالب.")
        return

    await state.update_data(prod_cost=str(cost))

    providers = await DynamicService.get_all_providers(session)
    if providers:
        b = InlineKeyboardBuilder()
        for p in providers:
            b.button(
                text=f"🔌 {p.name} ({p.type.value})",
                callback_data=f"admin:prod_provider:{p.id}",
            )
        b.button(
            text="⏭ بدون مزود (يدوي)",
            callback_data="admin:prod_provider:0",
        )
        b.adjust(1)
        await message.answer(
            "🔌 اختر المزود لهذا المنتج:",
            reply_markup=b.as_markup(),
        )
    else:
        await state.update_data(
            prod_provider_id=None,
            prod_provider_svc_id=None,
        )
        await _ask_fulfillment_mode(message, state)


async def _ask_fulfillment_mode(message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📦 مخزون رقمي (كود/ترخيص شرعي)",
        callback_data="admin:prod_fulfillment:inventory",
    )
    builder.button(
        text="✋ يدوي (غير قابل للبيع تلقائياً)",
        callback_data="admin:prod_fulfillment:manual",
    )
    builder.button(text="❌ إلغاء", callback_data="admin:products_menu")
    builder.adjust(1)
    await message.answer(
        "هذا المنتج بلا مزود API. اختر طريقة التسليم:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("admin:prod_fulfillment:"))
async def prod_fulfillment_selected(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(":")[2]
    fulfillment = {
        "inventory": ProductFulfillmentType.INVENTORY.value,
        "manual": ProductFulfillmentType.MANUAL.value,
    }.get(mode)
    if fulfillment is None:
        await callback.answer("⚠️ نوع غير صالح.", show_alert=True)
        return
    await state.update_data(
        prod_fulfillment_type=fulfillment,
        prod_provider_id=None,
        prod_provider_svc_id=None,
        prod_provider_service_ref_id=None,
    )
    await _ask_product_type(callback.message, state)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:prod_provider:"))
async def prod_provider_selected(
    callback: CallbackQuery,
    state: FSMContext,
    session,
):
    provider_id = int(callback.data.split(":")[2])
    if provider_id == 0:
        await state.update_data(
            prod_provider_id=None,
            prod_provider_svc_id=None,
            prod_provider_service_ref_id=None,
        )
        await _ask_fulfillment_mode(callback.message, state)
        await callback.answer()
        return

    provider = await session.get(ApiProvider, provider_id)
    if provider is None or not provider.is_active:
        await callback.answer(
            "⚠️ المزود غير موجود أو معطّل.",
            show_alert=True,
        )
        return

    await state.update_data(
        prod_provider_id=provider_id,
        prod_fulfillment_type=ProductFulfillmentType.API.value,
    )
    result = await session.execute(
        select(ProviderService)
        .where(
            ProviderService.api_provider_id == provider_id,
            ProviderService.status == ProviderServiceStatus.ACTIVE,
        )
        .order_by(ProviderService.category, ProviderService.name)
        .limit(40)
    )
    services = list(result.scalars().all())
    if not services:
        await callback.message.edit_text(
            "⚠️ لا توجد خدمات متزامنة لهذا المزود.\\n\\nأرسل آيدي الخدمة الخارجي يدوياً:",
            reply_markup=admin_back_kb(),
        )
        await state.set_state(AdminProductStates.waiting_provider_service_id)
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for service in services:
        builder.button(
            text=f"{service.external_service_id} · {service.name[:38]}",
            callback_data=f"admin:prod_service:{service.id}",
        )
    builder.button(
        text="✏️ إدخال آيدي خارجي يدوياً",
        callback_data=f"admin:prod_service_manual:{provider_id}",
    )
    builder.button(text="🔙 إلغاء", callback_data="admin:products_menu")
    builder.adjust(1)
    await callback.message.edit_text(
        f"🔌 <b>{provider.name}</b>\\n\\nاختر الخدمة التي سُحبت من المزود:",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(AdminProductStates.waiting_provider_service_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:prod_service_manual:"))
async def prod_service_manual(
    callback: CallbackQuery,
    state: FSMContext,
):
    provider_id = int(callback.data.split(":")[2])
    await state.update_data(
        prod_provider_id=provider_id,
        prod_provider_service_ref_id=None,
        prod_fulfillment_type=ProductFulfillmentType.API.value,
    )
    await callback.message.edit_text(
        "🔢 أرسل آيدي الخدمة عند المزود يدوياً:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductStates.waiting_provider_service_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:prod_service:"))
async def prod_service_selected(
    callback: CallbackQuery,
    state: FSMContext,
    session,
):
    service_id = int(callback.data.split(":")[2])
    service = await session.get(ProviderService, service_id)
    if service is None or service.status != ProviderServiceStatus.ACTIVE:
        await callback.answer("⚠️ الخدمة غير متاحة.", show_alert=True)
        return
    await state.update_data(
        prod_provider_id=service.api_provider_id,
        prod_provider_svc_id=service.external_service_id,
        prod_provider_service_ref_id=service.id,
        prod_fulfillment_type=ProductFulfillmentType.API.value,
    )
    await _ask_product_type(callback.message, state)
    await callback.answer("✅ تم اختيار الخدمة.")


@router.message(AdminProductStates.waiting_provider_service_id)
async def prod_svc_id_received(message: Message, state: FSMContext):
    service_id = (message.text or "").strip()
    if not service_id or len(service_id) > 64:
        await message.answer("⚠️ أرسل آيدي خدمة صالحاً (حتى 64 رمزاً).")
        return
    await state.update_data(
        prod_provider_svc_id=service_id,
        prod_provider_service_ref_id=None,
    )
    await _ask_product_type(message, state)


async def _ask_product_type(message: Message, state: FSMContext):
    b = InlineKeyboardBuilder()
    b.button(
        text="🎮 يحتاج Player ID",
        callback_data="admin:prod_req:player_id",
    )
    b.button(
        text="🔗 يحتاج رابط",
        callback_data="admin:prod_req:link",
    )
    b.button(
        text="🔗📊 يحتاج رابط + كمية",
        callback_data="admin:prod_req:link_qty",
    )
    b.button(
        text="📦 لا يحتاج إدخال",
        callback_data="admin:prod_req:none",
    )
    b.adjust(1)
    await message.answer(
        "ما الذي يحتاجه المستخدم لشراء هذا المنتج؟",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("admin:prod_req:"))
async def prod_requirements_selected(callback: CallbackQuery, state: FSMContext):
    req = callback.data.split(":")[2]

    requires_player_id = req == "player_id"
    requires_link = req in ("link", "link_qty")
    requires_quantity = req == "link_qty"

    await state.update_data(
        requires_player_id=requires_player_id,
        requires_link=requires_link,
        requires_quantity=requires_quantity,
    )

    if requires_quantity:
        await callback.message.edit_text("📊 أرسل الحد الأدنى للكمية:\n(مثال: 100)")
        await state.set_state(AdminProductStates.waiting_min_quantity)
    else:
        await _ask_estimated_time(callback.message, state)

    await callback.answer()


@router.message(AdminProductStates.waiting_min_quantity)
async def prod_min_qty_received(message: Message, state: FSMContext):
    try:
        min_qty = int(message.text.strip())
        if min_qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(min_quantity=min_qty)
    await message.answer("📊 أرسل الحد الأقصى للكمية:\n(مثال: 10000)")
    await state.set_state(AdminProductStates.waiting_max_quantity)


@router.message(AdminProductStates.waiting_max_quantity)
async def prod_max_qty_received(message: Message, state: FSMContext):
    try:
        max_qty = int(message.text.strip())
        if max_qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(max_quantity=max_qty)
    await _ask_estimated_time(message, state)


async def _ask_estimated_time(message: Message, state: FSMContext):
    await message.answer(
        "⏱️ أرسل <b>الوقت التقريبي للاكتمال</b>:\n"
        "(مثال: 5-30 دقيقة / 1-3 ساعات)\n\n"
        "أو أرسل <b>تخطي</b> لتركه فارغاً."
    )
    await state.set_state(AdminProductStates.waiting_estimated_time)


@router.message(AdminProductStates.waiting_estimated_time)
async def prod_estimated_time_received(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text or text in ("تخطي", "skip", "-", "0"):
        await state.update_data(estimated_time=None)
    else:
        await state.update_data(estimated_time=text[:64])
    await _save_product(message, state)


async def _save_product(message, state, callback=None):
    from database.engine import async_session_maker

    data = await state.get_data()

    async with async_session_maker() as session:
        product = await DynamicService.create_product(
            session=session,
            sub_category_id=data["prod_sub_id"],
            name_ar=data["prod_name"],
            price_usd=Decimal(data["prod_price"]),
            cost_price_usd=Decimal(data.get("prod_cost", "0")),
            api_provider_id=data.get("prod_provider_id"),
            provider_service_id=data.get("prod_provider_svc_id"),
            provider_service_ref_id=data.get("prod_provider_service_ref_id"),
            fulfillment_type=ProductFulfillmentType(
                data.get(
                    "prod_fulfillment_type",
                    ProductFulfillmentType.API.value,
                )
            ),
            requires_player_id=data.get("requires_player_id", False),
            requires_link=data.get("requires_link", False),
            requires_quantity=data.get("requires_quantity", False),
            min_quantity=data.get("min_quantity", 1),
            max_quantity=data.get("max_quantity", 1),
            estimated_time=data.get("estimated_time"),
            display_type=(
                ProductDisplayType.PER_1000
                if data.get("requires_quantity")
                else ProductDisplayType.FIXED_TOTAL
            ),
        )

    target = callback.message if callback else message
    await target.answer(
        f"✅ تم إنشاء المنتج: <b>{product.name_ar}</b>\n💰 السعر: {product.price_usd}$"
    )
    await state.clear()


# ══════════════ تفاصيل المنتج ══════════════


@router.callback_query(F.data.startswith("admin:prod_view:"))
async def prod_view(callback: CallbackQuery, session):
    prod_id = int(callback.data.split(":")[2])
    product = await DynamicService.get_product(session, prod_id)
    if not product:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    status = "🟢 مفعّل" if product.status.value == "active" else "⚪ معطّل"
    provider_name = "—"
    if product.api_provider:
        provider_name = product.api_provider.name

    reqs = []
    if product.requires_player_id:
        reqs.append("🎮 Player ID")
    if product.requires_link:
        reqs.append("🔗 رابط")
    if product.requires_quantity:
        reqs.append(f"📊 كمية ({product.min_quantity}-{product.max_quantity})")
    req_text = " | ".join(reqs) if reqs else "📦 لا يحتاج إدخال"

    sub_cat = product.sub_category

    # الهامش الفعّال (منتج ← قسم فرعي ← قسم ← عالمي)
    from services.margin_service import MarginService

    effective_margin, margin_source = await MarginService.resolve_product_margin(
        session, product
    )

    await callback.message.edit_text(
        f"📦 <b>{product.name_ar}</b>\n"
        f"🆔 ID: <code>{product.id}</code> | ربط زر: <code>prod:{product.id}</code>\n\n"
        f"الحالة: {status}\n"
        f"💰 سعر البيع: {product.price_usd}${' / 1000' if product.requires_quantity else ''}\n"
        f"💵 سعر التكلفة: {product.cost_price_usd}$\n"
        f"📈 الربح: {product.price_usd - product.cost_price_usd}$\n"
        f"💵 هامش الربح: <b>{effective_margin}%</b> (من: {margin_source})\n"
        f"🔌 المزود: {provider_name}\n"
        f"🔢 آيدي الخدمة: {product.provider_service_id or '—'}\n"
        f"⏱️ الوقت التقريبي: {product.estimated_time or '—'}\n"
        f"📥 متطلبات: {req_text}\n"
        f"🛒 إجمالي المبيعات: {product.total_sold}\n"
        f"🔢 الترتيب: {product.sort_order}",
        reply_markup=admin_product_detail_kb(
            product,
            sub_cat.id if sub_cat else 0,
        ),
    )


# ══════════════ فحص جاهزية المنتج ══════════════


@router.callback_query(F.data.startswith("admin:prod_ready:"))
async def prod_readiness(callback: CallbackQuery, session):
    prod_id = int(callback.data.rsplit(":", 1)[1])
    product = await DynamicService.get_product(session, prod_id)
    if not product:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    checks: list[tuple[bool, str]] = []
    checks.append((bool(product.name_ar and product.name_ar.strip()), "اسم المنتج موجود"))
    checks.append((product.price_usd and product.price_usd > 0, "السعر أكبر من صفر"))
    checks.append((product.sub_category is not None, "مرتبط بقسم فرعي"))
    if product.sub_category:
        checks.append((product.sub_category.is_active, "القسم الفرعي مفعّل"))
        from database.models import Category

        category = await session.get(Category, product.sub_category.category_id)
        checks.append((bool(category and category.is_active), "القسم الرئيسي مفعّل"))
    checks.append((product.status == ProductStatus.ACTIVE, "المنتج مفعّل"))

    if product.fulfillment_type == ProductFulfillmentType.API:
        checks.append((product.api_provider is not None, "مزود API مربوط"))
        checks.append((bool(product.provider_service_id), "آيدي خدمة المزود موجود"))
        checks.append((bool(product.api_provider and product.api_provider.is_active), "مزود API مفعّل"))
    elif product.fulfillment_type == ProductFulfillmentType.INVENTORY:
        stock = await InventoryService.available_count(session, product.id)
        checks.append((stock > 0, f"يوجد مخزون متاح ({stock})"))
    else:
        checks.append((True, "منتج يدوي: يحتاج تنفيذ من الإدارة"))

    if product.requires_quantity:
        checks.append((product.min_quantity >= 1, "الحد الأدنى للكمية صحيح"))
        checks.append((product.max_quantity >= product.min_quantity, "الحد الأعلى للكمية صحيح"))

    ok_count = sum(1 for ok, _label in checks if ok)
    lines = [
        f"🧪 <b>فحص جاهزية المنتج</b>\n📦 {product.name_ar}\n",
        f"النتيجة: <b>{ok_count}/{len(checks)}</b>",
        "",
    ]
    for ok, label in checks:
        lines.append(f"{'✅' if ok else '⚠️'} {label}")
    lines.append("\n✅ إذا كل البنود خضراء فالمنتج جاهز للبيع.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_product_detail_kb(product, product.sub_category_id),
    )
    await callback.answer()


# ══════════════ تفعيل/تعطيل ══════════════


@router.callback_query(F.data.startswith("admin:prod_toggle:"))
async def prod_toggle(callback: CallbackQuery, session):
    prod_id = int(callback.data.split(":")[2])
    product = await DynamicService.get_product(session, prod_id)
    if not product:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    new_status = (
        ProductStatus.INACTIVE if product.status == ProductStatus.ACTIVE else ProductStatus.ACTIVE
    )
    await DynamicService.update_product(session, prod_id, status=new_status)
    await callback.answer("✅ تم التحديث.")
    await prod_view(callback, session)


# ══════════════ تعديل السعر ══════════════


@router.callback_query(F.data.startswith("admin:prod_edit_price:"))
async def prod_edit_price_start(callback: CallbackQuery, state: FSMContext):
    prod_id = int(callback.data.split(":")[2])
    await state.update_data(edit_prod_id=prod_id, edit_field="price")
    await callback.message.edit_text(
        "💰 أرسل السعر الجديد بالدولار:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductStates.waiting_edit_value)


# ══════════════ تعديل الاسم ══════════════


@router.callback_query(F.data.startswith("admin:prod_edit_name:"))
async def prod_edit_name_start(callback: CallbackQuery, state: FSMContext):
    prod_id = int(callback.data.split(":")[2])
    await state.update_data(edit_prod_id=prod_id, edit_field="name")
    await callback.message.edit_text(
        "📝 أرسل الاسم الجديد:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductStates.waiting_edit_value)


# ══════════════ تعديل آيدي المزود ══════════════


@router.callback_query(F.data.startswith("admin:prod_edit_svc_id:"))
async def prod_edit_svc_id_start(callback: CallbackQuery, state: FSMContext):
    prod_id = int(callback.data.split(":")[2])
    await state.update_data(edit_prod_id=prod_id, edit_field="svc_id")
    await callback.message.edit_text(
        "🔢 أرسل آيدي الخدمة الجديد عند المزود:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductStates.waiting_edit_value)


@router.callback_query(F.data.startswith("admin:prod_edit_desc:"))
async def prod_edit_desc_start(callback: CallbackQuery, state: FSMContext):
    prod_id = int(callback.data.split(":")[2])
    await state.update_data(edit_prod_id=prod_id, edit_field="desc")
    await callback.message.edit_text(
        "📝 أرسل شرح/وصف الخدمة (يظهر للزبون في شاشة الشراء):\n"
        "أرسل <b>مسح</b> لإزالة الوصف:",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductStates.waiting_edit_value)


# ══════════════ معالج التعديل الموحد ══════════════


@router.message(AdminProductStates.waiting_edit_value)
async def prod_edit_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    prod_id = data.get("edit_prod_id")
    field = data.get("edit_field")
    value = message.text.strip()

    if field == "price":
        try:
            price = Decimal(value)
            if price <= 0:
                raise InvalidOperation
        except InvalidOperation:
            await message.answer("⚠️ أرسل رقماً صحيحاً.")
            return
        await DynamicService.update_product(session, prod_id, price_usd=price)
    elif field == "name":
        await DynamicService.update_product(session, prod_id, name_ar=value)
    elif field == "svc_id":
        await DynamicService.update_product(session, prod_id, provider_service_id=value)
    elif field == "desc":
        if value in ("مسح", "-", "", "0"):
            value = None
        else:
            if len(value) > 500:
                await message.answer("⚠️ الوصف طويل جداً (الحد الأقصى 500 حرف).")
                return
        await DynamicService.update_product(session, prod_id, description=value)

    await message.answer("✅ تم التحديث.")
    await state.clear()


# ══════════════ حذف المنتج ══════════════


@router.callback_query(F.data.startswith("admin:prod_delete:"))
async def prod_delete(callback: CallbackQuery, session):
    prod_id = int(callback.data.split(":")[2])
    product = await DynamicService.get_product(session, prod_id)
    if not product:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    sub_id = product.sub_category_id
    await DynamicService.delete_product(session, prod_id)
    await callback.answer("🗑 تم حذف المنتج.")

    products = await DynamicService.get_all_products(session, sub_id)
    await callback.message.edit_text(
        "📦 <b>المنتجات</b>",
        reply_markup=admin_products_list_kb(sub_id, products),
    )

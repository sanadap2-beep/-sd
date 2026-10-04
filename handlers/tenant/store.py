""" storefront for white-label sub-bots: branded menu, catalog, buy flow.

كل الأسعار المعروضة = سعر البوت الأساسي + هامش التاجر.
"""

from __future__ import annotations


from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.models import Category, Product, ProductStatus, SubCategory, Tenant, UnifiedOrder, User
from services.html_guard import esc
from services.premium_emoji import store_button
from services.rich_text import prem_slot, send_rich
from services.tenant_order_service import TenantOrderError, TenantOrderService
from states.states import TenantBuyStates

router = Router(name="tenant_store")


def _t(data) -> Tenant:
    return data["tenant"]


def _menu_kb(brand: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 المتجر", callback_data="t:store")],
            [
                InlineKeyboardButton(text="💰 رصيدي", callback_data="t:balance"),
                InlineKeyboardButton(text="📦 طلباتي", callback_data="t:orders"),
            ],
            [
                InlineKeyboardButton(text="➕ شحن الرصيد", callback_data="t:deposit"),
                InlineKeyboardButton(text="🛠 الدعم", callback_data="t:support"),
            ],
        ]
    )


async def _home_text(session, tenant: Tenant, user: User) -> str:
    return (
        f"🏪 <b>{esc(tenant.brand_name)}</b>\n\n"
        f"💰 رصيدك: <b>{user.balance:.4f}$</b>\n\n"
        "اختر من القائمة:"
    )


@router.message(F.text.startswith("/start"))
async def tenant_start(message: Message, session, db_user: User, tenant: Tenant):
    await message.answer(
        await _home_text(session, tenant, db_user), reply_markup=_menu_kb(tenant.brand_name)
    )


@router.callback_query(F.data == "t:home")
async def tenant_home(callback: CallbackQuery, session, db_user: User, tenant: Tenant):
    await callback.answer()
    try:
        await callback.message.edit_text(
            await _home_text(session, tenant, db_user),
            reply_markup=_menu_kb(tenant.brand_name),
        )
    except Exception:
        await callback.message.answer(
            await _home_text(session, tenant, db_user),
            reply_markup=_menu_kb(tenant.brand_name),
        )


@router.callback_query(F.data == "t:store")
async def tenant_store(callback: CallbackQuery, session, tenant: Tenant):
    cats = await TenantOrderService.tenant_categories(session, tenant)
    cats = [c for c in cats if c.is_active]
    if not cats:
        await callback.answer("المتجر فارغ حالياً.", show_alert=True)
        return
    rows = [
        [store_button(c.name_ar, c.emoji or "📦", getattr(c, "custom_emoji_id", None), callback_data=f"t:cat:{c.id}")]
        for c in cats
    ]
    rows.append([InlineKeyboardButton(text="🔙 الرئيسية", callback_data="t:home")])
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"🛍 <b>{esc(tenant.brand_name)}</b>\n\nاختر القسم:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("t:cat:"))
async def tenant_category(callback: CallbackQuery, session, tenant: Tenant):
    cat_id = int(callback.data.split(":")[2])
    cat = await session.get(Category, cat_id)
    if cat is None or not cat.is_active:
        await callback.answer("القسم غير متاح.", show_alert=True)
        return
    result = await session.execute(
        select(SubCategory).where(SubCategory.category_id == cat.id).order_by(SubCategory.id)
    )
    subs = [s for s in result.scalars().all() if getattr(s, "is_active", True)]
    rows = [
        [store_button(s.name_ar, "📁", getattr(s, "custom_emoji_id", None), callback_data=f"t:sub:{s.id}")]
        for s in subs
    ]
    # منتجات مباشرة بدون قسم فرعي؟ نعرضها أيضاً
    prods = await _visible_products(session, tenant, sub_ids=[s.id for s in subs], direct_cat=cat.id)
    for p in prods:
        if p.sub_category_id in {s.id for s in subs}:
            continue
        rows.append(
            [store_button(p.name_ar, "📦", getattr(p, "custom_emoji_id", None), callback_data=f"t:prod:{p.id}")]
        )
    rows.append([InlineKeyboardButton(text="🔙 الأقسام", callback_data="t:store")])
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"{cat.emoji or '📦'} <b>{esc(cat.name_ar)}</b>\n\nاختر:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass


async def _visible_products(session, tenant: Tenant, sub_ids: list[int], direct_cat: int | None = None):
    from database.models import TenantCatalogSelection

    q = select(Product).where(Product.status == ProductStatus.ACTIVE)
    if sub_ids:
        q = q.where(Product.sub_category_id.in_(sub_ids))
    result = await session.execute(q.order_by(Product.id))
    products = list(result.scalars().all())
    if tenant.catalog_mode == "full":
        return products
    sel = (
        await session.execute(
            select(TenantCatalogSelection).where(
                TenantCatalogSelection.tenant_id == tenant.id
            )
        )
    ).scalars().all()
    prod_ids = {s.item_id for s in sel if s.item_type == "product"}
    sub_ids_ok = {s.item_id for s in sel if s.item_type == "subcategory"}
    cat_ids_ok = {s.item_id for s in sel if s.item_type == "category"}
    out = []
    for p in products:
        if p.id in prod_ids or p.sub_category_id in sub_ids_ok:
            out.append(p)
        elif direct_cat is not None and direct_cat in cat_ids_ok:
            out.append(p)
    return out


@router.callback_query(F.data.startswith("t:sub:"))
async def tenant_sub(callback: CallbackQuery, session, tenant: Tenant):
    sub_id = int(callback.data.split(":")[2])
    prods = await _visible_products(session, tenant, sub_ids=[sub_id])
    if not prods:
        await callback.answer("لا منتجات هنا بعد.", show_alert=True)
        return
    rows = [
        [store_button(p.name_ar, "📦", getattr(p, "custom_emoji_id", None), callback_data=f"t:prod:{p.id}")]
        for p in prods[:30]
    ]
    rows.append([InlineKeyboardButton(text="🔙 الأقسام", callback_data="t:store")])
    await callback.answer()
    try:
        await callback.message.edit_text(
            "📦 اختر المنتج:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("t:prod:"))
async def tenant_product(callback: CallbackQuery, session, db_user: User, tenant: Tenant):
    prod_id = int(callback.data.split(":")[2])
    try:
        quote = await TenantOrderService.quote(session, tenant, prod_id, 1)
    except TenantOrderError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    product = quote["product"]
    prem = getattr(product, "custom_emoji_id", None)
    title = f"{prem_slot()} <b>{esc(product.name_ar)}</b>\n" if prem else f"📦 <b>{esc(product.name_ar)}</b>\n"
    lines = [
        title,
        f"💰 السعر: <b>{quote['merchant_total']}$</b>",
        f"💳 رصيدك: <b>{db_user.balance:.4f}$</b>",
    ]
    if product.description:
        lines.append(f"\n📝 {esc(product.description[:400])}")
    if product.estimated_time:
        lines.append(f"\n⏳ المدة التقريبية: {esc(product.estimated_time)}")
    rows = [
        [InlineKeyboardButton(text="✅ شراء الآن", callback_data=f"t:buy:{product.id}")],
        [InlineKeyboardButton(text="🔙 الأقسام", callback_data="t:store")],
    ]
    await callback.answer()
    if prem:
        await send_rich(
            callback.message, "\n".join(lines), [(prem, "📦")],
            InlineKeyboardMarkup(inline_keyboard=rows), edit=True,
        )
        return
    try:
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("t:buy:"))
async def tenant_buy_start(callback: CallbackQuery, state: FSMContext, session, tenant: Tenant):
    prod_id = int(callback.data.split(":")[2])
    try:
        quote = await TenantOrderService.quote(session, tenant, prod_id, 1)
    except TenantOrderError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    product = quote["product"]
    await state.update_data(tenant_prod_id=product.id)
    needs_target = bool(
        getattr(product, "requires_link", False) or getattr(product, "requires_player_id", False)
    )
    if needs_target:
        label = "الرابط" if getattr(product, "requires_link", False) else "معرف اللاعب"
        await state.set_state(TenantBuyStates.waiting_target)
        await callback.message.answer(f"🔗 أرسل {label} لإتمام الطلب:")
        await callback.answer()
        return
    await _ask_quantity_or_confirm(callback, state, session, tenant, product)


async def _ask_quantity_or_confirm(callback, state, session, tenant, product):
    needs_qty = bool(getattr(product, "requires_quantity", False)) and (
        (product.max_quantity or 1) > 1
    )
    if needs_qty:
        await state.set_state(TenantBuyStates.waiting_quantity)
        await callback.message.answer(
            f"🔢 أدخل الكمية (من {product.min_quantity} إلى {product.max_quantity}):"
        )
        await callback.answer()
        return
    try:
        quote = await TenantOrderService.quote(session, tenant, product.id, 1)
    except TenantOrderError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.update_data(tenant_qty=1, tenant_target="")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ تأكيد الدفع {quote['merchant_total']}$", callback_data="t:confirm")],
            [InlineKeyboardButton(text="❌ إلغاء", callback_data="t:home")],
        ]
    )
    await callback.message.answer(
        f"🧾 <b>تأكيد الطلب</b>\n\n📦 {esc(product.name_ar)}\n💰 الإجمالي: <b>{quote['merchant_total']}$</b>",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(TenantBuyStates.waiting_target)
async def tenant_target_received(message: Message, state: FSMContext, session, tenant: Tenant):
    data = await state.get_data()
    product = await session.get(Product, data.get("tenant_prod_id"))
    if product is None:
        await state.clear()
        return
    target = (message.text or "").strip()
    if len(target) < 3:
        await message.answer("⚠️ قيمة غير صالحة — أعد الإرسال.")
        return
    await state.update_data(tenant_target=target)
    await _ask_quantity_or_confirm(message, state, session, tenant, product)


@router.message(TenantBuyStates.waiting_quantity)
async def tenant_qty_received(message: Message, state: FSMContext, session, tenant: Tenant):
    data = await state.get_data()
    product = await session.get(Product, data.get("tenant_prod_id"))
    if product is None:
        await state.clear()
        return
    try:
        qty = int((message.text or "").strip())
    except ValueError:
        await message.answer("⚠️ أدخل رقماً صحيحاً.")
        return
    if qty < (product.min_quantity or 1) or qty > (product.max_quantity or 1):
        await message.answer(
            f"⚠️ الكمية بين {product.min_quantity} و {product.max_quantity}."
        )
        return
    try:
        quote = await TenantOrderService.quote(session, tenant, product.id, qty)
    except TenantOrderError as exc:
        await message.answer(f"⚠️ {exc}")
        await state.clear()
        return
    await state.update_data(tenant_qty=qty)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ تأكيد الدفع {quote['merchant_total']}$", callback_data="t:confirm")],
            [InlineKeyboardButton(text="❌ إلغاء", callback_data="t:home")],
        ]
    )
    await message.answer(
        f"🧾 <b>تأكيد الطلب</b>\n\n📦 {esc(product.name_ar)}\n🔢 الكمية: {qty}\n💰 الإجمالي: <b>{quote['merchant_total']}$</b>",
        reply_markup=kb,
    )


@router.callback_query(F.data == "t:confirm")
async def tenant_confirm(
    callback: CallbackQuery, state: FSMContext, session, db_user: User, tenant: Tenant
):
    data = await state.get_data()
    prod_id = data.get("tenant_prod_id")
    qty = int(data.get("tenant_qty") or 1)
    target = data.get("tenant_target") or ""
    await state.clear()
    if not prod_id:
        await callback.answer("انتهت الجلسة — ابدأ من جديد.", show_alert=True)
        return
    try:
        result = await TenantOrderService.purchase(
            session, tenant, db_user, prod_id, target, qty
        )
    except TenantOrderError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:
        await callback.answer("تعذر إتمام الطلب — لم يُخصم شيء.", show_alert=True)
        return
    prod = await session.get(Product, prod_id)
    prod_name = prod.name_ar if prod else ""
    text = (
        "✅ <b>تم استلام طلبك!</b>\n\n"
        f"🆔 الطلب: <code>#{result.order.id}</code>\n"
        f"📦 {esc(prod_name)}\n"
        f"💰 المبلغ: <b>{result.merchant_total}$</b>\n"
    )
    if result.delivery_value:
        text += f"\n🎁 <b>التسليم:</b>\n<code>{esc(result.delivery_value)[:1000]}</code>\n"
    else:
        text += "\n⏳ قيد التنفيذ — سنعلمك فور جاهزيته."
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 الرئيسية", callback_data="t:home")]
                ]
            ),
        )
    except Exception:
        await callback.message.answer(text)
    await callback.answer("✅ تم!")


@router.callback_query(F.data == "t:balance")
async def tenant_balance(callback: CallbackQuery, db_user: User, tenant: Tenant):
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"💰 <b>رصيدك في {esc(tenant.brand_name)}</b>\n\n"
            f"المتاح: <b>{db_user.balance:.4f}$</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ شحن الرصيد", callback_data="t:deposit")],
                    [InlineKeyboardButton(text="🔙 الرئيسية", callback_data="t:home")],
                ]
            ),
        )
    except Exception:
        pass


@router.callback_query(F.data == "t:orders")
async def tenant_orders(callback: CallbackQuery, session, db_user: User, tenant: Tenant):
    result = await session.execute(
        select(UnifiedOrder)
        .where(UnifiedOrder.user_id == db_user.id, UnifiedOrder.tenant_id == tenant.id)
        .order_by(UnifiedOrder.id.desc())
        .limit(10)
    )
    orders = list(result.scalars().all())
    if not orders:
        await callback.answer("لا طلبات بعد.", show_alert=True)
        return
    lines = [f"📦 <b>آخر طلباتك في {esc(tenant.brand_name)}:</b>\n"]
    for o in orders:
        lines.append(f"#{o.id} — {o.price_usd}$ — {getattr(o.status, 'value', o.status)}")
    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 الرئيسية", callback_data="t:home")]
                ]
            ),
        )
    except Exception:
        pass
    await callback.answer()

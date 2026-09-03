"""
💵 ضبط هوامش الربح من لوحة الأدمن:
- هامش القسم الرئيسي (يُعَد أسعار كل منتجاته بلا هامش خاص).
- هامش القسم الفرعي (وأقسام الرشق الداخلية).
- هامش المنتج (له الأولوية دائماً).

الأرسل 0 أو «مسح» = حذف الهامش الخاص → يرث المستوى الأعلى.
"""

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import Category, Product, SubCategory
from filters.admin_filter import IsAdmin
from services.dynamic_service import DynamicService
from services.margin_service import MarginService
from states.states import AdminMarginStates

router = Router(name="admin_margins")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _parse_percent(raw: str) -> Decimal | None | bool:
    """يعيد النسبة، أو None (للمسح)، أو False (قيمة غير صالحة)."""
    raw = (raw or "").strip()
    if raw in ("", "0", "مسح", "-", "inherit"):
        return None
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return False
    if value < MarginService.MIN_MARGIN or value > MarginService.MAX_MARGIN:
        return False
    return value


# ══════════════ هامش القسم الرئيسي ══════════════


@router.callback_query(F.data.startswith("admin:cat_margin:"))
async def cat_margin_start(callback: CallbackQuery, state: FSMContext, session):
    cat_id = int(callback.data.split(":")[2])
    category = await session.get(Category, cat_id)
    if category is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return
    current = category.profit_margin_percent
    current_text = (
        f"{current}%" if current is not None else "غير مضبوط (يرث الهامش العالمي)"
    )
    await callback.message.edit_text(
        f"💵 <b>هامش ربح القسم: {category.name_ar}</b>\n\n"
        f"الحالي: <b>{current_text}</b>\n\n"
        "أرسل النسبة الجديدة (مثال: 50) أو أرسل <b>0</b> للعودة للهامش العالمي.\n"
        "سيُعاد حساب أسعار كل منتجات القسم التي بلا هامش خاص عليها."
    )
    await state.update_data(scope="category", id=cat_id)
    await state.set_state(AdminMarginStates.waiting_category_margin)
    await callback.answer()


@router.message(AdminMarginStates.waiting_category_margin)
async def cat_margin_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    category = await session.get(Category, data.get("id"))
    if category is None:
        await state.clear()
        await message.answer("⚠️ القسم غير موجود.")
        return
    percent = _parse_percent(message.text or "")
    if percent is False:
        await message.answer("⚠️ أرسل نسبة صحيحة (رقم بين -95 و1000) أو 0 للمسح.")
        return
    updated = await MarginService.set_category_margin(session, category, percent)
    await state.clear()
    from handlers.admin.categories import cat_view

    fake = type("FakeCB", (), {"data": f"admin:cat_view:{category.id}"})()
    fake.message = message
    await cat_view(fake, session)


# ══════════════ هامش القسم الفرعي ══════════════


@router.callback_query(F.data.startswith("admin:subcat_margin:"))
async def subcat_margin_start(callback: CallbackQuery, state: FSMContext, session):
    sub_id = int(callback.data.split(":")[2])
    sub = await session.get(SubCategory, sub_id)
    if sub is None:
        await callback.answer("⚠️ القسم الفرعي غير موجود.", show_alert=True)
        return
    current = sub.profit_margin_percent
    current_text = (
        f"{current}%" if current is not None else "غير مضبوط (يرث قسمه الرئيسي)"
    )
    await callback.message.edit_text(
        f"💵 <b>هامش ربح القسم الفرعي: {sub.name_ar}</b>\n\n"
        f"الحالي: <b>{current_text}</b>\n\n"
        "أرسل النسبة الجديدة (مثال: 75) أو أرسل <b>0</b> للعودة للهامش الأعلى.\n"
        "سيُعاد حساب أسعار كل منتجات هذا القسم وأقسامه الداخلية بلا هامش خاص."
    )
    await state.update_data(scope="sub", id=sub_id)
    await state.set_state(AdminMarginStates.waiting_sub_margin)
    await callback.answer()


@router.message(AdminMarginStates.waiting_sub_margin)
async def subcat_margin_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    sub = await session.get(SubCategory, data.get("id"))
    if sub is None:
        await state.clear()
        await message.answer("⚠️ القسم الفرعي غير موجود.")
        return
    percent = _parse_percent(message.text or "")
    if percent is False:
        await message.answer("⚠️ أرسل نسبة صحيحة (رقم بين -95 و1000) أو 0 للمسح.")
        return
    updated = await MarginService.set_sub_margin(session, sub, percent)
    await state.clear()
    from handlers.admin.categories import subcat_view

    fake = type("FakeCB", (), {"data": f"admin:subcat_view:{sub.id}"})()
    fake.message = message
    await subcat_view(fake, session)


# ══════════════ هامش المنتج ══════════════


@router.callback_query(F.data.startswith("admin:prod_margin:"))
async def prod_margin_start(callback: CallbackQuery, state: FSMContext, session):
    prod_id = int(callback.data.split(":")[2])
    product = await session.get(Product, prod_id)
    if product is None:
        await callback.answer("⚠️ المنتج غير موجود.", show_alert=True)
        return
    effective, source = await MarginService.resolve_product_margin(session, product)
    current = product.profit_margin_percent
    current_text = (
        f"{current}% (خاص بالمنتج)"
        if current is not None
        else f"يرث: {effective}% من {source}"
    )
    await callback.message.edit_text(
        f"💵 <b>هامش ربح المنتج: {product.name_ar}</b>\n\n"
        f" الهامش الفعّال: <b>{effective}%</b> (المصدر: {source})\n"
        f"🔧 الحالي: <b>{current_text}</b>\n"
        f"💵 التكلفة: {product.cost_price_usd}$\n\n"
        "أرسل نسبة الهامش الجديدة (مثال: 60) أو أرسل <b>0</b> للعودة للهامش الأعلى.\n"
        + (
            "سيُعاد حساب السعر من التكلفة وفق الهامش الجديد."
            if product.cost_price_usd > 0
            else "⚠️ لا توجد تكلفة لهذا المنتج — لن يتغير السعر."
        )
    )
    await state.update_data(scope="product", id=prod_id)
    await state.set_state(AdminMarginStates.waiting_product_margin)
    await callback.answer()


@router.message(AdminMarginStates.waiting_product_margin)
async def prod_margin_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    product = await session.get(Product, data.get("id"))
    if product is None:
        await state.clear()
        await message.answer("⚠️ المنتج غير موجود.")
        return
    percent = _parse_percent(message.text or "")
    if percent is False:
        await message.answer("⚠️ أرسل نسبة صحيحة (رقم بين -95 و1000) أو 0 للمسح.")
        return
    await MarginService.set_product_margin(session, product, percent)
    await state.clear()
    from handlers.admin.products import prod_view

    fake = type("FakeCB", (), {"data": f"admin:prod_view:{product.id}"})()
    fake.message = message
    await prod_view(fake, session)

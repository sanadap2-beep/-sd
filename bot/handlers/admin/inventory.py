"""إدارة مخزون التراخيص والقسائم الرقمية."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from database.models import (
    DigitalInventoryItem,
    InventoryItemStatus,
    Product,
    ProductFulfillmentType,
)
from filters.admin_filter import IsAdmin
from keyboards.admin import (
    admin_back_kb,
    admin_inventory_detail_kb,
    admin_inventory_items_kb,
    admin_inventory_kb,
)
from services.inventory_service import InventoryError, InventoryService
from states.states import AdminInventoryStates

router = Router(name="admin_inventory")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _inventory_products(session):
    result = await session.execute(
        select(Product)
        .where(Product.fulfillment_type == ProductFulfillmentType.INVENTORY)
        .order_by(Product.sort_order, Product.id)
    )
    products = list(result.scalars().all())
    return [
        (product, await InventoryService.available_count(session, product.id))
        for product in products
    ]


@router.callback_query(F.data == "admin:inventory")
async def inventory_list(callback: CallbackQuery, session):
    products = await _inventory_products(session)
    await callback.answer()
    if not products:
        await callback.message.edit_text(
            "📦 <b>المخزون الرقمي</b>\n\n"
            "لا توجد منتجات مخزون بعد. أنشئ منتجاً واختر "
            "«مخزون رقمي» كطريقة التسليم.",
            reply_markup=admin_back_kb(),
        )
        return
    await callback.message.edit_text(
        "📦 <b>المخزون الرقمي</b>\n\n"
        "العناصر مشفرة ولا تظهر قيمها في لوحة الإدارة.\n"
        "اختر منتجاً لإضافة أو إدارة التراخيص:",
        reply_markup=admin_inventory_kb(products),
    )


@router.callback_query(F.data.startswith("admin:inv_product:"))
async def inventory_product(callback: CallbackQuery, session):
    product_id = int(callback.data.split(":")[2])
    product = await session.get(Product, product_id)
    if product is None or product.fulfillment_type != ProductFulfillmentType.INVENTORY:
        await callback.answer("⚠️ المنتج غير موجود أو ليس مخزوناً.", show_alert=True)
        return
    counts = {}
    for status in InventoryItemStatus:
        counts[status] = (
            await session.execute(
                select(func.count(DigitalInventoryItem.id)).where(
                    DigitalInventoryItem.product_id == product_id,
                    DigitalInventoryItem.status == status,
                )
            )
        ).scalar_one()
    await callback.answer()
    await callback.message.edit_text(
        f"📦 <b>مخزون {product.name_ar}</b>\n\n"
        f"🟢 متاح: <b>{counts[InventoryItemStatus.AVAILABLE]}</b>\n"
        f"🟡 محجوز: {counts[InventoryItemStatus.RESERVED]}\n"
        f"✅ مباع: {counts[InventoryItemStatus.SOLD]}\n"
        f"⚫ ملغى: {counts[InventoryItemStatus.VOID]}\n\n"
        "أضف أكواداً أو تراخيص تم شراؤها من مصادر موثوقة فقط.",
        reply_markup=admin_inventory_detail_kb(product_id),
    )


@router.callback_query(F.data.startswith("admin:inv_add:"))
async def inventory_add_start(
    callback: CallbackQuery,
    state: FSMContext,
    session,
):
    product_id = int(callback.data.split(":")[2])
    product = await session.get(Product, product_id)
    if product is None or product.fulfillment_type != ProductFulfillmentType.INVENTORY:
        await callback.answer("⚠️ المنتج غير صالح.", show_alert=True)
        return
    await state.update_data(inventory_product_id=product_id)
    await state.set_state(AdminInventoryStates.waiting_value)
    await callback.answer()
    await callback.message.edit_text(
        f"➕ <b>إضافة عنصر إلى {product.name_ar}</b>\n\n"
        "أرسل كود القسيمة أو بيانات الترخيص الشرعي في رسالة واحدة.\n"
        "سيتم تشفير الرسالة فوراً ولن تظهر قيمتها في لوحة الإدارة.\n\n"
        "لا ترسل جلسات Telegram أو كلمات مرور حسابات أو بيانات مسروقة.",
        reply_markup=admin_back_kb(),
    )


@router.message(AdminInventoryStates.waiting_value)
async def inventory_value_received(
    message: Message,
    state: FSMContext,
    session,
):
    value = (message.text or "").strip()
    data = await state.get_data()
    try:
        item = await InventoryService.add_item(
            session,
            int(data["inventory_product_id"]),
            value,
        )
    except InventoryError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    finally:
        # The input may contain a secret voucher. Remove it from the chat.
        try:
            await message.delete()
        except Exception:
            pass
    await state.clear()
    await message.answer(
        f"✅ تمت إضافة العنصر المشفر رقم <b>#{item.id}</b>.",
        reply_markup=admin_back_kb(),
    )


@router.callback_query(F.data.startswith("admin:inv_items:"))
async def inventory_items(callback: CallbackQuery, session):
    parts = callback.data.split(":")
    product_id = int(parts[2] if parts[1] == "inv_items" else parts[3])
    result = await session.execute(
        select(DigitalInventoryItem)
        .where(
            DigitalInventoryItem.product_id == product_id,
            DigitalInventoryItem.status == InventoryItemStatus.AVAILABLE,
        )
        .order_by(DigitalInventoryItem.id)
        .limit(50)
    )
    items = list(result.scalars().all())
    await callback.answer()
    text = (
        f"📋 <b>العناصر المتاحة للمنتج #{product_id}</b>\n\n"
        + ("\n".join(f"🟢 العنصر #{item.id}" for item in items) or "لا توجد عناصر متاحة.")
        + "\n\nالقيم السرية غير معروضة."
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_inventory_items_kb(items, product_id),
    )


@router.callback_query(F.data.startswith("admin:inv_void:"))
async def inventory_void(callback: CallbackQuery, session):
    parts = callback.data.split(":")
    item_id = int(parts[2])
    if await InventoryService.void_item(session, item_id):
        await callback.answer("✅ تم إلغاء العنصر.")
    else:
        await callback.answer("⚠️ العنصر غير متاح أو تم استخدامه.", show_alert=True)
    await inventory_items(
        callback,
        session,
    )

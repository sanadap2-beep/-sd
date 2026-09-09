"""Handlers for the Ready Codes (التطبيقات والأكواد الجاهزة) section."""
import logging
from decimal import Decimal
from sqlalchemy import select
from datetime import datetime
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import State, StatesGroup
from config import settings
from database.models import ReadyCodeItem, TransactionType, UnifiedOrder, UnifiedOrderStatus
from keyboards.ready_codes import ready_codes_list_kb, ready_code_detail_kb, admin_ready_codes_kb
from services.balance_service import BalanceService, InsufficientBalanceError

logger = logging.getLogger(__name__)
router = Router(name="ready_codes")

class AdminReadyCodeStates(StatesGroup):
    waiting_name = State()
    waiting_description = State()
    waiting_price = State()
    waiting_instructions = State()
    waiting_file_url = State()

@router.callback_query(F.data == "readycode:list")
async def ready_codes_list(callback: CallbackQuery, session, db_user):
    language = getattr(db_user, "language_code", "ar") or "ar"
    result = await session.execute(
        select(ReadyCodeItem).where(ReadyCodeItem.is_active.is_(True)).order_by(ReadyCodeItem.sort_order, ReadyCodeItem.id)
    )
    items = list(result.scalars().all())
    if not items:
        await callback.message.edit_text(
            "📦 <b>التطبيقات والأكواد الجاهزة</b>\n\nلا توجد عناصر متاحة حالياً.",
            reply_markup=ready_codes_list_kb([], language)
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "📦 <b>التطبيقات والأكواد الجاهزة</b>\n\nاختر ما تريد:",
        reply_markup=ready_codes_list_kb(items, language)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("readycode:view:"))
async def ready_code_view(callback: CallbackQuery, session, db_user):
    item_id = int(callback.data.split(":")[2])
    item = await session.get(ReadyCodeItem, item_id)
    if item is None or not item.is_active:
        await callback.answer("العنصر غير متاح.")
        return
    price_str = "💰 <b>مجاني</b>" if item.price_usd == 0 else f"💰 <b>${item.price_usd}</b>"
    text = (
        f"📦 <b>{item.name_ar}</b>\n\n"
        f"{item.description or 'لا يوجد وصف'}\n\n"
        f"{price_str}\n"
    )
    if item.instructions:
        text += f"\n📋 <b>طريقة الاستخدام:</b>\n{item.instructions}"
    if item.file_url:
        text += f"\n\n🔗 <a href='{item.file_url}'>رابط التحميل</a>"
    await callback.message.edit_text(
        text,
        reply_markup=ready_code_detail_kb(item.id, item.price_usd)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("readycode:buy:"))
async def ready_code_buy(callback: CallbackQuery, session, db_user, bot):
    item_id = int(callback.data.split(":")[2])
    item = await session.get(ReadyCodeItem, item_id)
    if item is None or not item.is_active:
        await callback.answer("العنصر غير متاح.")
        return
    user = db_user
    price = item.price_usd

    if price > 0:
        try:
            await BalanceService.deduct_balance(
                session, user.id, price,
                TransactionType.PURCHASE,
                description=f"شراء {item.name_ar}",
                is_purchase=True,
            )
        except InsufficientBalanceError:
            await callback.answer("⚠️ رصيدك غير كافٍ.", show_alert=True)
            return

    order = UnifiedOrder(
        user_id=user.id,
        product_id=0, quantity=1, price_usd=price,
        status=UnifiedOrderStatus.COMPLETED,
        status_message="مكتمل - شراء من التطبيقات والأكواد الجاهزة",
        completed_at=datetime.utcnow(),
    )
    session.add(order)
    await session.commit()

    if settings.PUBLIC_CHANNEL_ID:
        try:
            await bot.send_message(
                settings.PUBLIC_CHANNEL_ID,
                f"🛍 <b>تم شراء {item.name_ar}</b>\n\n"
                f"👤 المشتري: {user.full_name or user.username or user.telegram_id}\n"
                f"💰 السعر: {'مجاني' if price == 0 else f'${price}'}\n"
                f"📦 {item.name_ar}\n"
                f"{item.description or ''}",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("فشل إشعار القناة العامة")

    text = f"✅ <b>تم الشراء بنجاح!</b>\n\n📦 <b>{item.name_ar}</b>\n"
    text += "💰 مجاني\n" if price == 0 else f"💰 المبلغ: ${price}\n"
    if item.instructions:
        text += f"\n📋 <b>طريقة الاستخدام:</b>\n{item.instructions}"
    if item.file_url:
        text += f"\n\n🔗 <a href='{item.file_url}'>رابط التحميل</a>"
    await callback.message.edit_text(text)
    await callback.answer("✅ تم الشراء بنجاح!", show_alert=True)

# ── Admin ──

@router.callback_query(F.data == "admin:readycodes")
async def admin_ready_codes_list(callback: CallbackQuery, session, db_user):
    if not db_user.is_admin:
        await callback.answer("غير مصرح"); return
    result = await session.execute(select(ReadyCodeItem).order_by(ReadyCodeItem.sort_order, ReadyCodeItem.id))
    items = list(result.scalars().all())
    await callback.message.edit_text(
        "📦 <b>إدارة التطبيقات والأكواد الجاهزة</b>", reply_markup=admin_ready_codes_kb(items)
    )
    await callback.answer()

@router.callback_query(F.data == "admin:readycode:add")
async def admin_ready_code_add_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminReadyCodeStates.waiting_name)
    await callback.message.edit_text("📝 أرسل اسم العنصر:")
    await callback.answer()

@router.message(AdminReadyCodeStates.waiting_name)
async def admin_ready_code_add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminReadyCodeStates.waiting_description)
    await message.answer("📝 أرسل وصف العنصر (أو /skip):")

@router.message(AdminReadyCodeStates.waiting_description)
async def admin_ready_code_add_desc(message: Message, state: FSMContext):
    t = message.text.strip()
    await state.update_data(description="" if t == "/skip" else t)
    await state.set_state(AdminReadyCodeStates.waiting_price)
    await message.answer("💰 أرسل السعر بالدولار (0 للمجاني):")

@router.message(AdminReadyCodeStates.waiting_price)
async def admin_ready_code_add_price(message: Message, state: FSMContext):
    try:
        price = Decimal(message.text.strip())
        if price < 0: raise ValueError
    except Exception:
        await message.answer("⚠️ السعر غير صالح.")
        return
    await state.update_data(price=price)
    await state.set_state(AdminReadyCodeStates.waiting_instructions)
    await message.answer("📋 أرسل تعليمات الاستخدام (أو /skip):")

@router.message(AdminReadyCodeStates.waiting_instructions)
async def admin_ready_code_add_instructions(message: Message, state: FSMContext):
    t = message.text.strip()
    await state.update_data(instructions="" if t == "/skip" else t)
    await state.set_state(AdminReadyCodeStates.waiting_file_url)
    await message.answer("🔗 أرسل رابط الملف (أو /skip):")

@router.message(AdminReadyCodeStates.waiting_file_url)
async def admin_ready_code_add_final(message: Message, state: FSMContext, session):
    t = message.text.strip()
    if t != "/skip":
        await state.update_data(file_url=t)
    data = await state.get_data()
    item = ReadyCodeItem(
        name_ar=data["name"], description=data.get("description",""),
        price_usd=data["price"], instructions=data.get("instructions",""),
        file_url=data.get("file_url",""), is_active=True,
    )
    session.add(item); await session.commit(); await state.clear()
    await message.answer(f"✅ تم إضافة <b>{data['name']}</b> بنجاح!", parse_mode="HTML")
    all_items = await session.execute(select(ReadyCodeItem).order_by(ReadyCodeItem.sort_order, ReadyCodeItem.id))
    await message.answer("📦 القائمة:", reply_markup=admin_ready_codes_kb(list(all_items.scalars().all())))

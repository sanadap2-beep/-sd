"""Handlers for the Ready Codes (التطبيقات والأكواد الجاهزة) section."""
import asyncio
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
from keyboards.ready_codes import (
    ready_codes_list_kb,
    ready_code_detail_kb,
    admin_ready_codes_kb,
    admin_ready_code_edit_kb,
)
from keyboards.main_menu import back_to_main_kb
from services.balance_service import BalanceService, InsufficientBalanceError

logger = logging.getLogger(__name__)
router = Router(name="ready_codes")

class AdminReadyCodeStates(StatesGroup):
    waiting_name = State()
    waiting_description = State()
    waiting_price = State()
    waiting_instructions = State()
    waiting_file_url = State()
    # حالات التعديل — نفس الحقول تُعيد استخدامها عند التعديل.
    edit_name = State()
    edit_description = State()
    edit_price = State()
    edit_instructions = State()
    edit_file_url = State()

# ── واجهة المستخدم ──


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
    has_content = bool(item.instructions) or bool(item.file_url)
    text = (
        f"📦 <b>{item.name_ar}</b>\n\n"
        f"{item.description or 'لا يوجد وصف'}\n\n"
        f"{price_str}\n"
    )
    # المحتوى (التعليمات/الملف) لا يظهر قبل الشراء — يُسلَّم بعد الدفع فقط.
    if has_content:
        text += "\n🔒 <b>المحتوى يظهر بعد الشراء</b>"
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

    # لا نستخدم product_id=0 (كان يكسّر قيد FOREIGN KEY) — هذه الطلبات
    # لا ترتبط بمنتج حقيقي في جدول products، فنجعل الحقل NULL.
    order = UnifiedOrder(
        user_id=user.id,
        product_id=None,
        quantity=1,
        price_usd=price,
        status=UnifiedOrderStatus.COMPLETED,
        status_message=f"مكتمل - شراء {item.name_ar}",
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
    # بعد الشراء فقط يظهر المحتوى.
    if item.instructions:
        text += f"\n📋 <b>طريقة الاستخدام:</b>\n{item.instructions}"
    if item.file_url:
        text += f"\n\n🔗 <a href='{item.file_url}'>رابط التحميل</a>"
    await callback.message.edit_text(text, reply_markup=back_to_main_kb())
    await callback.answer("✅ تم الشراء بنجاح!", show_alert=True)


# ── إدارة الأدمن ──


@router.callback_query(F.data == "admin:readycodes")
async def admin_ready_codes_list(callback: CallbackQuery, session, db_user):
    if not getattr(db_user, "is_admin", False):
        await callback.answer("غير مصرح")
        return
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
        if price < 0:
            raise ValueError
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
async def admin_ready_code_add_final(message: Message, state: FSMContext, session, bot):
    t = message.text.strip()
    if t != "/skip":
        await state.update_data(file_url=t)
    data = await state.get_data()
    item = ReadyCodeItem(
        name_ar=data["name"], description=data.get("description", ""),
        price_usd=data["price"], instructions=data.get("instructions", ""),
        file_url=data.get("file_url", ""), is_active=True,
    )
    session.add(item)
    await session.commit()
    await state.clear()
    await message.answer(f"✅ تم إضافة <b>{data['name']}</b> بنجاح!", parse_mode="HTML")
    # إشعار كل المستخدمين بالعنصر الجديد (خلفية حتى لا يعطّل تدفق الأدمن).
    if bot is not None:
        asyncio.create_task(_notify_all_new_ready_code_item(bot, data["name"]))
    all_items = await session.execute(select(ReadyCodeItem).order_by(ReadyCodeItem.sort_order, ReadyCodeItem.id))
    await message.answer("📦 القائمة:", reply_markup=admin_ready_codes_kb(list(all_items.scalars().all())))


async def _notify_all_new_ready_code_item(bot, name_ar: str) -> None:
    """يبثّ إشعار «تمت إضافة عنصر جديد» لكل المستخدمين غير المحظورين.

    يُشغَّل كخلفية (background task) حتى لا يُبطئ تدفق إضافة العنصر،
    ويطبّق حد إدخال بسيط لتجنّب حظر تيليجرام من الإرسال المتتالي.
    """
    try:
        from database.engine import async_session_maker
        from database.models import User

        async with async_session_maker() as session:
            users = (
                await session.execute(
                    select(User).where(User.is_banned.is_(False))
                )
            ).scalars().all()

        text = (
            f"🆕 <b>تم إضافة:</b> {name_ar}\n"
            f"📦 قسم التطبيقات والأكواد الجاهزة\n\n"
            f"اضغط على زر 📦 في المتجر لاستعراضه."
        )
        for i, user in enumerate(users):
            try:
                await bot.send_message(user.telegram_id, text, parse_mode="HTML")
            except Exception:
                pass
            if (i + 1) % 30 == 0:
                await asyncio.sleep(1)
    except Exception:
        logger.exception("فشل إشعار المستخدمين بعنصر جديد في الأكواد الجاهزة")


# ── تعديل / حذف / تفعيل ──


@router.callback_query(F.data.startswith("admin:readycode:edit:"))
async def admin_ready_code_edit_menu(callback: CallbackQuery, session, db_user):
    if not getattr(db_user, "is_admin", False):
        await callback.answer("غير مصرح")
        return
    item_id = int(callback.data.split(":")[3])
    item = await session.get(ReadyCodeItem, item_id)
    if item is None:
        await callback.answer("العنصر غير موجود.")
        return
    await callback.message.edit_text(
        f"📦 <b>{item.name_ar}</b> — اختر حقلاً للتعديل:",
        reply_markup=admin_ready_code_edit_kb(item),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("arc:field:"))
async def admin_ready_code_edit_field(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    field = parts[2]
    item_id = int(parts[3])
    await state.update_data(edit_item_id=item_id)
    prompts = {
        "name": "✏️ أرسل الاسم الجديد:",
        "description": "📝 أرسل الوصف الجديد (أو /skip لفارغ):",
        "price": "💰 أرسل السعر الجديد بالدولار (0 للمجاني):",
        "instructions": "📋 أرسل التعليمات الجديدة (أو /skip لفارغ):",
        "file_url": "🔗 أرسل الرابط الجديد (أو /skip لفارغ):",
    }
    state_for_field = {
        "name": AdminReadyCodeStates.edit_name,
        "description": AdminReadyCodeStates.edit_description,
        "price": AdminReadyCodeStates.edit_price,
        "instructions": AdminReadyCodeStates.edit_instructions,
        "file_url": AdminReadyCodeStates.edit_file_url,
    }
    await state.set_state(state_for_field[field])
    await callback.message.edit_text(prompts[field])
    await callback.answer()


async def _apply_edit(message: Message, state: FSMContext, session, field: str, parser=None):
    data = await state.get_data()
    item_id = data.get("edit_item_id")
    if item_id is None:
        await message.answer("⚠️ الجلسة انتهت، أعد المحاولة.")
        return
    t = message.text.strip()
    # نتحقق من صحة القيمة قبل مسح الحالة حتى لا يفقد الأدمن جلسة التعديل
    # عند إدخال قيمة غير صالحة (مثل نص في حقل السعر).
    if parser is not None:
        try:
            val = parser(t)
        except Exception:
            await message.answer("⚠️ القيمة غير صالحة.")
            return
    else:
        val = "" if t == "/skip" else t
    await state.clear()
    item = await session.get(ReadyCodeItem, item_id)
    if item is None:
        await message.answer("⚠️ العنصر غير موجود.")
        return
    if field == "name":
        item.name_ar = val
    elif field == "description":
        item.description = val
    elif field == "price":
        item.price_usd = val
    elif field == "instructions":
        item.instructions = val
    elif field == "file_url":
        item.file_url = val
    await session.commit()
    await message.answer("✅ تم تحديث الحقل بنجاح.", parse_mode="HTML")
    all_items = await session.execute(select(ReadyCodeItem).order_by(ReadyCodeItem.sort_order, ReadyCodeItem.id))
    await message.answer("📦 القائمة:", reply_markup=admin_ready_codes_kb(list(all_items.scalars().all())))


@router.message(AdminReadyCodeStates.edit_name)
async def admin_ready_code_edit_name(message: Message, state: FSMContext, session):
    await _apply_edit(message, state, session, "name")


@router.message(AdminReadyCodeStates.edit_description)
async def admin_ready_code_edit_description(message: Message, state: FSMContext, session):
    await _apply_edit(message, state, session, "description")


@router.message(AdminReadyCodeStates.edit_price)
async def admin_ready_code_edit_price(message: Message, state: FSMContext, session):
    def parse_price(t: str) -> Decimal:
        value = Decimal(t)
        if value < 0:
            raise ValueError
        return value
    await _apply_edit(message, state, session, "price", parser=parse_price)


@router.message(AdminReadyCodeStates.edit_instructions)
async def admin_ready_code_edit_instructions(message: Message, state: FSMContext, session):
    await _apply_edit(message, state, session, "instructions")


@router.message(AdminReadyCodeStates.edit_file_url)
async def admin_ready_code_edit_file_url(message: Message, state: FSMContext, session):
    await _apply_edit(message, state, session, "file_url")


@router.callback_query(F.data.startswith("arc:toggle:"))
async def admin_ready_code_toggle(callback: CallbackQuery, session, db_user):
    if not getattr(db_user, "is_admin", False):
        await callback.answer("غير مصرح")
        return
    item_id = int(callback.data.split(":")[2])
    item = await session.get(ReadyCodeItem, item_id)
    if item is None:
        await callback.answer("العنصر غير موجود.")
        return
    item.is_active = not item.is_active
    await session.commit()
    status = "مفعّل" if item.is_active else "معطّل"
    await callback.message.edit_text(
        f"✅ {item.name_ar} أصبح {status}.",
        reply_markup=admin_ready_code_edit_kb(item),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("arc:delete:"))
async def admin_ready_code_delete(callback: CallbackQuery, session, db_user):
    if not getattr(db_user, "is_admin", False):
        await callback.answer("غير مصرح")
        return
    item_id = int(callback.data.split(":")[2])
    item = await session.get(ReadyCodeItem, item_id)
    if item is None:
        await callback.answer("العنصر غير موجود.")
        return
    await session.delete(item)
    await session.commit()
    await callback.answer("🗑 تم حذف العنصر.", show_alert=True)
    await admin_ready_codes_list(callback, session, db_user)

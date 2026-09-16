"""
صفحة حساب المستخدم.
تعرض الرصيد بالدولار، الطلبات، سجل المعاملات.
"""
from html import escape
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload
from database.models import User, NumberOrder, UnifiedOrder, DigitalInventoryItem, ProductReview, OrderStatus, UnifiedOrderStatus, ProductStatus
from services.balance_service import BalanceService
from services.i18n_service import I18nService
from services.cashback_service import CashbackService
from services.inventory_service import InventoryError, InventoryService
from services.receipt_service import ReceiptService
from services.watch_service import WatchService
from keyboards.games import product_confirm_kb
router = Router(name='account')


def _auto_lang(scope=None) -> str:
    user = (scope or {}).get("db_user")
    if user is None:
        callback = (scope or {}).get("callback")
        user = getattr(callback, "from_user", None)
    return getattr(user, "language_code", "ar") or "ar"

def _account_kb(language: str='ar') -> InlineKeyboardBuilder:
    t = lambda key: I18nService.t(key, language)
    kb = InlineKeyboardBuilder()
    kb.button(text=t('acct_number_orders'), callback_data='my_num_orders:0')
    kb.button(text=t('acct_other_orders'), callback_data='my_uni_orders:0')
    kb.button(text=t('acct_transactions'), callback_data='my_transactions:0')
    kb.button(text=t('acct_watches'), callback_data='my_watches')
    kb.button(text=t('acct_currency'), callback_data='menu:currency')
    kb.button(text=t('acct_language'), callback_data='menu:language')
    kb.button(text=t('back_to_main'), callback_data='back_to_main')
    kb.adjust(2, 2, 2, 1)
    return kb

def _label(mapping: dict, value, language: str):
    """يرجع نص الحالة/النوع بلغة المستخدم (قوائم ar/en)."""
    entry = mapping.get(value)
    if isinstance(entry, dict):
        return entry.get(language, entry.get('ar', str(value)))
    return entry if entry is not None else str(value)
ORDER_STATUS_LABELS = {OrderStatus.PENDING: {'ar': '⏳ قيد الانتظار', 'en': '⏳ Pending'}, OrderStatus.CODE_RECEIVED: {'ar': '✅ وصل الكود', 'en': '✅ Code received'}, OrderStatus.COMPLETED: {'ar': '✅ مكتمل', 'en': '✅ Completed'}, OrderStatus.EXPIRED: {'ar': '⌛ انتهت الصلاحية', 'en': '⌛ Expired'}, OrderStatus.CANCELLED: {'ar': '❌ ملغى', 'en': '❌ Cancelled'}, OrderStatus.REFUNDED: {'ar': '↩️ مسترجَع', 'en': '↩️ Refunded'}}
UNIFIED_STATUS_LABELS = {UnifiedOrderStatus.PENDING: {'ar': '⏳ قيد الانتظار', 'en': '⏳ Pending'}, UnifiedOrderStatus.PROCESSING: {'ar': '🔄 قيد التنفيذ', 'en': '🔄 Processing'}, UnifiedOrderStatus.COMPLETED: {'ar': '✅ مكتمل', 'en': '✅ Completed'}, UnifiedOrderStatus.FAILED: {'ar': '❌ فشل', 'en': '❌ Failed'}, UnifiedOrderStatus.REFUNDED: {'ar': '↩️ مسترجَع', 'en': '↩️ Refunded'}, UnifiedOrderStatus.PARTIAL: {'ar': '⚠️ جزئي', 'en': '⚠️ Partial'}}
TRANSACTION_TYPE_LABELS = {
    'deposit': {'ar': '💰 إيداع', 'en': '💰 Deposit'},
    'purchase': {'ar': '🛒 شراء', 'en': '🛒 Purchase'},
    'refund': {'ar': '↩️ استرجاع', 'en': '↩️ Refund'},
    'referral_bonus': {'ar': '💎 مكافأة إحالة', 'en': '💎 Referral bonus'},
    'transfer_in': {'ar': '📥 تحويل وارد', 'en': '📥 Incoming transfer'},
    'transfer_out': {'ar': '📤 تحويل صادر', 'en': '📤 Outgoing transfer'},
    'admin_add': {'ar': '➕ إضافة (أدمن)', 'en': '➕ Admin credit'},
    'admin_deduct': {'ar': '➖ خصم (أدمن)', 'en': '➖ Admin debit'},
    'cashback': {'ar': '🎁 كاشباك', 'en': '🎁 Cashback'},
    'stars_deposit': {'ar': '⭐ نجوم تليجرام', 'en': '⭐ Telegram Stars'},
    'coupon_bonus': {'ar': '🎟 كوبون', 'en': '🎟 Coupon'},
    'loyalty_redeem': {'ar': '🎁 استبدال نقاط الولاء', 'en': '🎁 Loyalty redemption'},
    'gift_redeem': {'ar': '🎁 كود هدية', 'en': '🎁 Gift code'},
}

@router.message(F.text == '👤 حسابي')
async def account_handler(message: Message, session, db_user: User):
    await _send_account(message, session, db_user)

@router.callback_query(F.data == 'menu:account')
async def account_handler_cb(callback: CallbackQuery, session, db_user: User):
    await callback.answer()
    await _send_account(callback.message, session, db_user)

async def _send_account(message: Message, session, db_user: User):
    from services.currency_service import CurrencyService
    language = db_user.language_code
    t = lambda key, **kw: I18nService.t(key, language, **kw)
    result = await session.execute(select(func.count(User.id)).where(User.referrer_id == db_user.id))
    referrals_count = result.scalar_one()
    total_cashback = await CashbackService.get_user_total_cashback(session, db_user.id)
    kb = _account_kb(language)
    balance_display = await CurrencyService.format_dual(db_user.balance, db_user, session)
    balance_syp_note = await CurrencyService.syp_note(db_user.balance, db_user, session)
    balance_line = balance_display + balance_syp_note
    # «إجمالي مشترياتك» = ما اكتمل وتفعّل فعلاً فقط (الأرقام بعد التفعيل،
    # والرشق/الألعاب بعد الاكتمال) — لا الطلبات المعلّقة ولا المسترجَعة،
    # لأن المستخدم يدفع مسبقاً وقد يُرجع رصيده إذا لم يتفعّل الطلب.
    from services.ledger_service import LedgerService

    realized_spent, realized_orders = await LedgerService.user_realized_totals(
        session, db_user.id
    )
    spent_display = await CurrencyService.format_dual(realized_spent, db_user, session)
    cashback_display = await CurrencyService.format_dual(total_cashback, db_user, session)

    vip_line = ""
    try:
        from services.feature_service import FeatureService
        from services.vip_service import VipService

        if await VipService.enabled() and await VipService.show_in_profile():
            tier = await VipService.tier_for(realized_spent)
            vip_line = f"👑 {('مستوى' if language.startswith('ar') else 'Tier')}: <b>{tier.name}</b> · كاشباك ×{tier.cashback_mult}\n\n"
    except Exception:
        pass

    await message.answer(vip_line + t('account_card', user_id=db_user.telegram_id, balance=balance_line, spent=spent_display, orders=realized_orders, cashback=cashback_display, points=db_user.loyalty_points, referrals=referrals_count, joined=db_user.joined_at.strftime('%Y-%m-%d')), reply_markup=kb.as_markup())

@router.callback_query(F.data == 'my_watches')
async def my_watches(callback: CallbackQuery, session, db_user: User):
    watches = await WatchService.list_user_watches(session, db_user.id)
    await callback.answer()
    if not watches:
        await callback.message.edit_text(I18nService.t('ux_account_146_1', _auto_lang(locals())), reply_markup=_account_kb().as_markup())
        return
    lines = ['🔔 <b>تنبيهاتي</b>\n', 'سنخبرك عند انخفاض السعر أو عودة المخزون:']
    kb = InlineKeyboardBuilder()
    for watch in watches:
        if watch.product:
            lines.append(f'\n📦 {watch.product.name_ar} · {watch.product.price_usd}$')
            kb.button(text=f'🔕 إلغاء {watch.product.name_ar[:25]}', callback_data=f'watch:toggle:{watch.product_id}')
    kb.button(text='🔙 رجوع لحسابي', callback_data='menu:account')
    kb.adjust(1)
    await callback.message.edit_text('\n'.join(lines), reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith('my_num_orders:'))
async def my_number_orders(callback: CallbackQuery, session, db_user: User):
    page = int(callback.data.split(':')[1])
    per_page = 5
    result = await session.execute(select(NumberOrder).where(NumberOrder.user_id == db_user.id).order_by(desc(NumberOrder.purchased_at)).limit(per_page).offset(page * per_page))
    orders = result.scalars().all()
    total_result = await session.execute(select(func.count(NumberOrder.id)).where(NumberOrder.user_id == db_user.id))
    total = total_result.scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    if not orders and page == 0:
        await callback.message.edit_text(I18nService.t('ux_account_189_2', _auto_lang(locals())))
        await callback.answer()
        return
    lines = [f'📋 <b>طلبات الأرقام ({page + 1}/{total_pages})</b>\n']
    for o in orders:
        status_label = _label(ORDER_STATUS_LABELS, o.status, db_user.language_code)
        line = f"\n📱 <code>{o.phone_number}</code>\n📲 الخدمة: {o.service}\nالحالة: {status_label} | السعر: {o.price_sell_usd}$\nالتاريخ: {o.purchased_at.strftime('%Y-%m-%d %H:%M')}"
        if o.sms_code:
            line += f'\n🔑 الكود: <code>{o.sms_code}</code>'
        if o.extra_codes:
            line += f'\n🔑 أكواد إضافية: <code>{o.extra_codes}</code>'
        lines.append(line)
    kb = InlineKeyboardBuilder()
    if page > 0:
        kb.button(text='◀️ السابق', callback_data=f'my_num_orders:{page - 1}')
    if page < total_pages - 1:
        kb.button(text='التالي ▶️', callback_data=f'my_num_orders:{page + 1}')
    kb.button(text='🔙 رجوع لحسابي', callback_data='menu:account')
    kb.adjust(2, 1)
    await callback.message.edit_text('\n'.join(lines), reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith('my_uni_orders:'))
async def my_unified_orders(callback: CallbackQuery, session, db_user: User):
    page = int(callback.data.split(':')[1])
    per_page = 5
    result = await session.execute(select(UnifiedOrder).options(selectinload(UnifiedOrder.product)).where(UnifiedOrder.user_id == db_user.id).order_by(desc(UnifiedOrder.created_at)).limit(per_page).offset(page * per_page))
    orders = result.scalars().all()
    total_result = await session.execute(select(func.count(UnifiedOrder.id)).where(UnifiedOrder.user_id == db_user.id))
    total = total_result.scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    if not orders and page == 0:
        await callback.message.edit_text(I18nService.t('ux_account_250_3', _auto_lang(locals())))
        await callback.answer()
        return
    lines = [f'🛒 <b>طلبات أخرى ({page + 1}/{total_pages})</b>\n']
    for o in orders:
        status_label = _label(UNIFIED_STATUS_LABELS, o.status, db_user.language_code)
        product_name = '—'
        if o.product:
            product_name = o.product.name_ar
        line = f"\n🆔 #{o.id}\n📦 المنتج: {product_name}\nالحالة: {status_label} | السعر: {o.price_usd}$\nالتاريخ: {o.created_at.strftime('%Y-%m-%d %H:%M')}"
        if o.target:
            line += f'\n🎯 الهدف: <code>{o.target}</code>'
        lines.append(line)
    kb = InlineKeyboardBuilder()
    for order in orders:
        kb.button(text=f'🔎 تفاصيل الطلب #{order.id}', callback_data=f'my_uni_order:{order.id}')
    if page > 0:
        kb.button(text='◀️ السابق', callback_data=f'my_uni_orders:{page - 1}')
    if page < total_pages - 1:
        kb.button(text='التالي ▶️', callback_data=f'my_uni_orders:{page + 1}')
    kb.button(text='🔙 رجوع لحسابي', callback_data='menu:account')
    kb.adjust(1, 2, 1)
    await callback.message.edit_text('\n'.join(lines), reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith('my_uni_order:'))
async def unified_order_detail(callback: CallbackQuery, session, db_user: User):
    order_id = int(callback.data.split(':')[1])
    result = await session.execute(select(UnifiedOrder).options(selectinload(UnifiedOrder.product)).where(UnifiedOrder.id == order_id, UnifiedOrder.user_id == db_user.id))
    order = result.scalar_one_or_none()
    if order is None:
        await callback.answer(I18nService.t('ux_account_309_4', _auto_lang(locals())), show_alert=True)
        return
    status_label = _label(UNIFIED_STATUS_LABELS, order.status, db_user.language_code)
    product_name = order.product.name_ar if order.product else '—'
    text = f"🛒 <b>تفاصيل الطلب #{order.id}</b>\n\n📦 المنتج: <b>{product_name}</b>\n📊 الحالة: {status_label}\n💰 المبلغ: <b>{order.price_usd}$</b>\n🎯 الهدف: <code>{order.target or '—'}</code>\n📊 الكمية: {order.quantity}\n🆔 رقم المزود: <code>{order.external_order_id or '—'}</code>\n📝 الحالة التفصيلية: {order.status_message or '—'}\n📅 التاريخ: {order.created_at.strftime('%Y-%m-%d %H:%M')}"
    if order.remains is not None:
        text += f'\n⏳ المتبقي: {order.remains}'
    delivery_result = await session.execute(select(DigitalInventoryItem).where(DigitalInventoryItem.unified_order_id == order.id))
    delivery_item = delivery_result.scalar_one_or_none()
    if delivery_item is not None:
        try:
            delivery_value = InventoryService.decrypt_value(delivery_item.encrypted_value)
            text += f'\n\n🎁 <b>بيانات التسليم:</b>\n<code>{escape(delivery_value)}</code>'
        except InventoryError:
            text += '\n\n⚠️ تعذر عرض بيانات التسليم حالياً.'
    kb = InlineKeyboardBuilder()
    if order.status == UnifiedOrderStatus.COMPLETED and order.product_id is not None:
        review_result = await session.execute(select(ProductReview).where(ProductReview.user_id == db_user.id, ProductReview.product_id == order.product_id))
        if review_result.scalar_one_or_none() is None:
            kb.button(text='⭐ قيّم هذا المنتج', callback_data=f'review:start:{order.id}')
    kb.button(text='🔁 إعادة الطلب', callback_data=f'repeat_order:{order.id}')
    kb.button(text='🧾 الإيصال', callback_data=f'receipt:unified:{order.id}')
    kb.button(text='🔙 رجوع للطلبات', callback_data='my_uni_orders:0')
    kb.button(text='🏠 القائمة الرئيسية', callback_data='back_to_main')
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith('receipt:unified:'))
async def unified_receipt(callback: CallbackQuery, session, db_user: User):
    order_id = int(callback.data.split(':')[2])
    result = await session.execute(select(UnifiedOrder).options(selectinload(UnifiedOrder.product)).where(UnifiedOrder.id == order_id, UnifiedOrder.user_id == db_user.id))
    order = result.scalar_one_or_none()
    if order is None:
        await callback.answer(I18nService.t('ux_account_378_5', _auto_lang(locals())), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(ReceiptService.unified_text(order, db_user, order.product))

@router.callback_query(F.data.startswith('repeat_order:'))
async def repeat_order(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    order_id = int(callback.data.split(':')[1])
    result = await session.execute(select(UnifiedOrder).options(selectinload(UnifiedOrder.product)).where(UnifiedOrder.id == order_id, UnifiedOrder.user_id == db_user.id))
    order = result.scalar_one_or_none()
    if order is None or not order.product or order.product.status != ProductStatus.ACTIVE:
        await callback.answer(I18nService.t('ux_account_402_6', _auto_lang(locals())), show_alert=True)
        return
    await state.clear()
    await state.update_data(product_id=order.product_id, target=order.target or '', quantity=order.quantity or 1)
    await callback.answer()
    await callback.message.edit_text(f"{I18nService.t('ux_account_412_7', _auto_lang(locals()))}{order.product.name_ar}{I18nService.t('ux_account_412_8', _auto_lang(locals()))}{order.target or '—'}{I18nService.t('ux_account_412_9', _auto_lang(locals()))}{order.quantity}{I18nService.t('ux_account_412_10', _auto_lang(locals()))}", reply_markup=product_confirm_kb(order.product_id, order.product.sub_category_id))

@router.callback_query(F.data.startswith('my_transactions:'))
async def my_transactions(callback: CallbackQuery, session, db_user: User):
    page = int(callback.data.split(':')[1])
    per_page = 8
    transactions = await BalanceService.get_transactions(session, db_user.id, limit=per_page, offset=page * per_page)
    from sqlalchemy import select as sa_select, func as sa_func
    from database.models import Transaction
    total_result = await session.execute(sa_select(sa_func.count(Transaction.id)).where(Transaction.user_id == db_user.id))
    total = total_result.scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    if not transactions and page == 0:
        await callback.message.edit_text(I18nService.t('ux_account_450_11', _auto_lang(locals())))
        await callback.answer()
        return
    lines = [f'📊 <b>سجل المعاملات ({page + 1}/{total_pages})</b>\n']
    for tx in transactions:
        tx_label = _label(TRANSACTION_TYPE_LABELS, tx.type.value, db_user.language_code)
        sign = '+' if tx.amount > 0 else ''
        line = f"\n{tx_label}\nالمبلغ: {sign}{tx.amount:.4f}$\nالرصيد بعدها: {tx.balance_after:.2f}$\nالتاريخ: {tx.created_at.strftime('%Y-%m-%d %H:%M')}"
        if tx.description:
            line += f'\n📝 {tx.description[:50]}'
        lines.append(line)
    kb = InlineKeyboardBuilder()
    if page > 0:
        kb.button(text='◀️ السابق', callback_data=f'my_transactions:{page - 1}')
    if page < total_pages - 1:
        kb.button(text='التالي ▶️', callback_data=f'my_transactions:{page + 1}')
    kb.button(text='🔙 رجوع لحسابي', callback_data='menu:account')
    kb.adjust(2, 1)
    await callback.message.edit_text('\n'.join(lines), reply_markup=kb.as_markup())
    await callback.answer()

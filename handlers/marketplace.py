"""
سوق المستخدمين — واجهة المستخدم.

التدفق:
1) المستخدم يضغط «اعرض شيئاً للبيع» ويختار نوع المعروض.
2) يدخل العنوان والوصف والسعر والصور.
3) للأكواد: يرفع الكود نفسه فيُحفظ **مشفراً** بيد البوت.
4) لأرقام SMS: يثبت ملكيته برقم الطلب.
5) يضغط «نشر» فيذهب الإعلان لإشعارات الأدمن بكل تفاصيله.
6) الأدمن يحدد عمولته ويسمح بالنشر، فتُضاف العمولة فوق السعر.
7) يظهر الإعلان لكل المستخدمين بزر «شراء» واحد.
8) الشراء يتطلب رصيداً كافياً، ويُخصم فوراً ويُحجز عند البوت.
9) الأدمن وسيط: يؤكد التسليم فتُفرج الأموال للبائع ناقص العمولة.

لماذا لا يضيع حق أحد:
- الكود الرقمي بيد البوت مشفراً، فلا يستطيع البائع سحبه بعد البيع.
- الأموال تُحجز قبل إعلام البائع، فلا يستطيع المشتري التنصل.
- العمولة تُقتطع عند الإفراج، وهو المسار الوحيد الذي يدفع للبائع.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.models import EscrowStatus, MarketListing, MarketListingPhoto, MarketListingStatus, MarketTransaction, User
from keyboards.main_menu import back_to_main_kb
from services.feature_service import FeatureService
from services.i18n_service import I18nService
from services.market_profile_service import MarketProfileError, MarketProfileService
from services.marketplace_service import MarketplaceService, MarketError
from services.notification_service import NotificationService
from services.points_service import PointsService
from sqlalchemy import select
from states.states import MarketCreateStates, MarketProfileStates
from decimal import Decimal, InvalidOperation
router = Router(name='marketplace')


def _auto_lang(scope=None) -> str:
    user = (scope or {}).get("db_user")
    if user is None:
        callback = (scope or {}).get("callback")
        user = getattr(callback, "from_user", None)
    return getattr(user, "language_code", "ar") or "ar"
_KIND_LABELS = {'game_account': '🎮 حساب لعبة', 'digital_code': '🔑 كود / بطاقة رقمية', 'sms_number': '📱 رقم SMS', 'service': '🛠 خدمة أنفّذها', 'other': '📦 شيء آخر'}
_KIND_HINTS = {'digital_code': 'الكود يُحفظ مشفراً عندنا ولا يُكشف إلا للمشتري بعد الدفع، فحقك مضمون.', 'sms_number': 'ستحتاج رقم الطلب الذي اشتريت به الرقم لإثبات ملكيته.', 'game_account': 'البوت وسيط: تُحجز أموال المشتري حتى يؤكد الأدمن التسليم.', 'service': 'البوت وسيط: تُحجز أموال المشتري حتى تؤكد تنفيذ الخدمة.', 'other': 'البوت وسيط: تُحجز أموال المشتري حتى يؤكد الأدمن التسليم.'}

def _lang(db_user) -> str:
    return getattr(db_user, 'language_code', 'ar') or 'ar'

@router.callback_query(F.data == 'market:home')
async def market_home(callback: CallbackQuery, db_user, session):
    if not await MarketplaceService.enabled():
        await callback.answer(I18nService.t('ux_marketplace_74_1', _auto_lang(locals())), show_alert=True)
        return
    profile = await MarketProfileService.get(session, db_user.id)
    if profile is None:
        await callback.message.edit_text(I18nService.t('ux_marketplace_79_2', _auto_lang(locals())), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=I18nService.t('ux_marketplace_84_3', _auto_lang(locals())), callback_data='market_profile_create', style="success")], [InlineKeyboardButton(text=I18nService.t('ux_marketplace_85_4', _auto_lang(locals())), callback_data='back_to_main')]]))
        await callback.answer()
        return
    language = _lang(db_user)
    rows = [[InlineKeyboardButton(text=I18nService.t('market_browse', language), callback_data='market_browse:all:0', style="success")], [InlineKeyboardButton(text=I18nService.t('market_sell', language), callback_data='market_sell', style="success")], [InlineKeyboardButton(text=I18nService.t('market_my_listings', language), callback_data='market_mine', style="success")], [InlineKeyboardButton(text=I18nService.t('market_my_purchases', language), callback_data='market_bought', style="success")], [InlineKeyboardButton(text='⬅️', callback_data='menu:main')]]
    await callback.message.edit_text(f"{I18nService.t('market_home_title', language)}{I18nService.t('ux_marketplace_100_5', _auto_lang(locals()))}{profile.alias}{I18nService.t('ux_marketplace_100_6', _auto_lang(locals()))}{profile.successful_sales}{I18nService.t('ux_marketplace_100_7', _auto_lang(locals()))}{profile.failed_sales}\n\n{I18nService.t('market_home_desc', language)}{I18nService.t('ux_marketplace_100_8', _auto_lang(locals()))}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data == 'market_profile_create')
async def market_profile_create(callback: CallbackQuery, state: FSMContext):
    await state.set_state(MarketProfileStates.waiting_alias)
    await callback.message.answer(I18nService.t('ux_marketplace_115_9', _auto_lang(locals())))
    await callback.answer()

@router.message(MarketProfileStates.waiting_alias)
async def market_profile_alias(message: Message, state: FSMContext):
    alias = (message.text or '').strip()
    await state.update_data(alias=alias)
    await state.set_state(MarketProfileStates.waiting_password)
    await message.answer(I18nService.t('ux_marketplace_126_10', _auto_lang(locals())))

@router.message(MarketProfileStates.waiting_password)
async def market_profile_password(message: Message, state: FSMContext, session, db_user):
    data = await state.get_data()
    try:
        profile = await MarketProfileService.create(session, db_user.id, data.get('alias', ''), (message.text or '').strip())
    except MarketProfileError as exc:
        await message.answer(f"⚠️ {exc}{I18nService.t('ux_marketplace_140_11', _auto_lang(locals()))}")
        await state.set_state(MarketProfileStates.waiting_alias)
        return
    try:
        await message.delete()
    except Exception:
        pass
    await state.clear()
    await message.answer(f"{I18nService.t('ux_marketplace_149_12', _auto_lang(locals()))}{profile.alias}{I18nService.t('ux_marketplace_149_13', _auto_lang(locals()))}")

@router.callback_query(F.data.startswith('market_browse:'))
async def browse(callback: CallbackQuery, session, db_user):
    if not await MarketplaceService.enabled():
        await callback.answer(I18nService.t('ux_marketplace_160_14', _auto_lang(locals())), show_alert=True)
        return
    _, kind, page = callback.data.split(':')
    listings, total = await MarketplaceService.browse(session, kind, int(page))
    language = _lang(db_user)
    if not listings:
        await callback.message.edit_text(I18nService.t('ux_marketplace_168_15', _auto_lang(locals())), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=I18nService.t('market_sell', language), callback_data='market_sell', style="success")], [InlineKeyboardButton(text='⬅️', callback_data='market:home')]]))
        await callback.answer()
        return
    kind_rows = [[InlineKeyboardButton(text='🌐 الكل', callback_data='market_browse:all:0', style="success"), InlineKeyboardButton(text='🎮 حسابات', callback_data='market_browse:game_account:0', style="primary"), InlineKeyboardButton(text='🔑 أكواد', callback_data='market_browse:digital_code:0', style="success")], [InlineKeyboardButton(text='🛠 خدمات', callback_data='market_browse:service:0', style="success"), InlineKeyboardButton(text='📱 أرقام', callback_data='market_browse:sms_number:0', style="success"), InlineKeyboardButton(text='📦 أخرى', callback_data='market_browse:other:0', style="success")]]
    rows = list(kind_rows)
    for listing in listings:
        total_price = await MarketplaceService.total_price(listing)
        rows.append([InlineKeyboardButton(text=f"{_KIND_LABELS.get(listing.kind, '📦')} {listing.title[:20]} — {total_price}$", callback_data=f'market_view:{listing.id}', style="success")])
    nav = []
    if int(page) > 0:
        nav.append(InlineKeyboardButton(text='◀️', callback_data=f'market_browse:{kind}:{int(page) - 1}'))
    if (int(page) + 1) * 8 < total:
        nav.append(InlineKeyboardButton(text='▶️', callback_data=f'market_browse:{kind}:{int(page) + 1}'))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text='⬅️', callback_data='market:home')])
    await callback.message.edit_text(f"{I18nService.t('ux_marketplace_208_16', _auto_lang(locals()))}{total}{I18nService.t('ux_marketplace_208_17', _auto_lang(locals()))}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data.startswith('market_view:'))
async def view_listing(callback: CallbackQuery, session, db_user):
    listing = await session.get(MarketListing, int(callback.data.split(':')[1]))
    if listing is None or listing.status != MarketListingStatus.APPROVED:
        await callback.answer(I18nService.t('ux_marketplace_218_18', _auto_lang(locals())), show_alert=True)
        return
    listing.view_count = (listing.view_count or 0) + 1
    await session.commit()
    total = await MarketplaceService.total_price(listing)
    language = _lang(db_user)
    balance = Decimal(str(db_user.balance or 0))
    enough = balance >= total
    photos = (await session.execute(select(MarketListingPhoto).where(MarketListingPhoto.listing_id == listing.id).order_by(MarketListingPhoto.sort_order))).scalars().all()
    for photo in photos:
        try:
            await callback.message.answer_photo(photo.file_id)
        except Exception:
            pass
    seller_profile = await MarketProfileService.get(session, listing.seller_id)
    seller_stats = MarketProfileService.stats(seller_profile)
    rows = []
    rows.append([InlineKeyboardButton(text=f"👤 ملف البائع: {seller_stats['alias']}", callback_data=f'market_seller:{listing.seller_id}:{listing.id}', style="success")])
    if listing.seller_id == db_user.id:
        rows.append([InlineKeyboardButton(text='🗑 سحب إعلاني', callback_data=f'market_cancel:{listing.id}', style="danger")])
    else:
        rows.append([InlineKeyboardButton(text=I18nService.t('market_buy_button', language), callback_data=f'market_askbuy:{listing.id}:cash', style="success")])
        if await PointsService.enabled() and (db_user.loyalty_points or 0) > 0:
            rows.append([InlineKeyboardButton(text=I18nService.t('market_buy_with_points', language), callback_data=f'market_askbuy:{listing.id}:points', style="primary")])
    rows.append([InlineKeyboardButton(text='⬅️', callback_data='market_browse:all:0')])
    delivery_note = '🔐 <b>تسليم فوري وآلي</b> — الكود مشفر عندنا ويصلك لحظة الدفع.' if listing.secret_payload else '🤝 <b>بوساطة الإدارة</b> — أموالك محجوزة حتى يؤكد الأدمن التسليم.'
    await callback.message.edit_text(f"{_KIND_LABELS.get(listing.kind, '📦')} <b>{listing.title}</b>\n\n📝 {(listing.description or '—')[:1500]}{I18nService.t('ux_marketplace_277_19', _auto_lang(locals()))}{total}{I18nService.t('ux_marketplace_277_20', _auto_lang(locals()))}{seller_stats['alias']}</b> · {seller_stats['tier']}{I18nService.t('ux_marketplace_277_21', _auto_lang(locals()))}{seller_stats['success_rate']}%</b> ({seller_stats['successful_sales']}{I18nService.t('ux_marketplace_277_22', _auto_lang(locals()))}{seller_stats['failed_sales']}{I18nService.t('ux_marketplace_277_23', _auto_lang(locals()))}{listing.view_count}\n\n{delivery_note}\n\n" + ('' if enough else f"{I18nService.t('ux_marketplace_285_24', _auto_lang(locals()))}{balance}{I18nService.t('ux_marketplace_285_25', _auto_lang(locals()))}{total}{I18nService.t('ux_marketplace_285_26', _auto_lang(locals()))}"), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data.startswith('market_seller:'))
async def seller_profile_view(callback: CallbackQuery, session):
    parts = callback.data.split(':')
    seller_id = int(parts[1])
    back_listing_id = int(parts[2]) if len(parts) > 2 else 0
    profile = await MarketProfileService.get(session, seller_id)
    stats = MarketProfileService.stats(profile)
    active = list((await session.execute(select(MarketListing).where(MarketListing.seller_id == seller_id, MarketListing.status == MarketListingStatus.APPROVED).order_by(MarketListing.published_at.desc()).limit(5))).scalars().all())
    lines = ['👤 <b>ملف البائع</b>', '', f"الاسم المستعار: <b>{stats['alias']}</b>", f"المستوى: {stats['tier']}", f"✅ مبيعات ناجحة: <b>{stats['successful_sales']}</b>", f"⚠️ فشل/نزاعات: <b>{stats['failed_sales']}</b>", f"📊 نسبة النجاح: <b>{stats['success_rate']}%</b>"]
    if active:
        lines.append('\n📦 <b>معروضات نشطة من هذا البائع:</b>')
        for item in active:
            total = await MarketplaceService.total_price(item)
            lines.append(f'• #{item.id} {item.title[:32]} — {total}$')
    else:
        lines.append('\nلا توجد معروضات نشطة أخرى حالياً.')
    rows = []
    for item in active[:5]:
        rows.append([InlineKeyboardButton(text=f'فتح #{item.id} {item.title[:20]}', callback_data=f'market_view:{item.id}', style="success")])
    if back_listing_id:
        rows.append([InlineKeyboardButton(text='⬅️ رجوع للمعروض', callback_data=f'market_view:{back_listing_id}')])
    rows.append([InlineKeyboardButton(text='⬅️ السوق', callback_data='market:home')])
    await callback.message.edit_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data.startswith('market_askbuy:'))
async def ask_buy(callback: CallbackQuery, session, db_user):
    _, listing_id, mode = callback.data.split(':')
    listing = await session.get(MarketListing, int(listing_id))
    if listing is None or listing.status != MarketListingStatus.APPROVED:
        await callback.answer(I18nService.t('ux_marketplace_359_27', _auto_lang(locals())), show_alert=True)
        return
    if listing.seller_id == db_user.id:
        await callback.answer(I18nService.t('ux_marketplace_362_28', _auto_lang(locals())), show_alert=True)
        return
    total = await MarketplaceService.total_price(listing)
    language = _lang(db_user)
    balance = Decimal(str(db_user.balance or 0))
    summary = ''
    if mode == 'points' and await PointsService.enabled():
        quote = await PointsService.quote(session, db_user.id, total)
        if quote['points_used'] > 0:
            summary = f"\n⭐ ستُدفع {quote['points_used']} نقطة ({quote['points_usd']}$)\n💵 والباقي {quote['cash_usd']}$ من رصيدك"
    rows = [[InlineKeyboardButton(text='✅ تأكيد الشراء', callback_data=f'market_buy:{listing.id}:{mode}', style="primary")], [InlineKeyboardButton(text=I18nService.t('market_buy_cancel', language), callback_data=f'market_view:{listing.id}', style="success")]]
    await callback.message.edit_text(I18nService.t('market_buy_confirm', language, title=listing.title, total=f'{total}', balance=f'{balance}') + summary, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data.startswith('market_buy:'))
async def do_buy(callback: CallbackQuery, session, db_user, bot):
    _, listing_id, mode = callback.data.split(':')
    try:
        transaction = await MarketplaceService.purchase(session, int(listing_id), db_user.id, use_points=mode == 'points')
    except MarketError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    language = _lang(db_user)
    listing = await session.get(MarketListing, transaction.listing_id)
    notifier = NotificationService(bot)
    await notifier.notify_admin(f"🏪 <b>عملية جديدة في سوق المستخدمين</b>\n\n🆔 العملية: #{transaction.id}\n📄 الإعلان: {(listing.title if listing else '—')}\n👤 البائع: <code>{transaction.seller_id}</code>\n🛒 المشتري: <code>{transaction.buyer_id}</code>\n💵 سعر البائع: {transaction.seller_price_usd}$\n🏷 عمولتك: {transaction.commission_usd}$\n💰 المحجوز: <b>{transaction.total_charged_usd}$</b>\n\n🔒 الأموال محجوزة عندك. أكّد التسليم من لوحة السوق لتُفرج.")
    if listing and listing.secret_payload:
        try:
            code = await MarketplaceService.reveal_secret(session, transaction.id, db_user.id)
        except MarketError:
            code = None
        if code:
            await MarketplaceService.mark_delivered(session, transaction.id, db_user.id)
            await callback.message.edit_text(I18nService.t('market_secret_revealed', language, code=code) + I18nService.t('ux_marketplace_437_29', _auto_lang(locals())), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=I18nService.t('ux_marketplace_441_30', _auto_lang(locals())), callback_data=f'market_good:{transaction.id}', style="success")], [InlineKeyboardButton(text=I18nService.t('ux_marketplace_442_31', _auto_lang(locals())), callback_data=f'market_bad:{transaction.id}', style="success")]]))
            await notifier.notify_admin(f'✅ العملية #{transaction.id} سُلّمت آلياً.\nبانتظار تأكيد المشتري أو فتح نزاع.')
            return
    await callback.message.edit_text(I18nService.t('market_bought', language, tx_id=transaction.id) + I18nService.t('ux_marketplace_454_32', _auto_lang(locals())))
    try:
        await bot.send_message(transaction.seller_id, f"{I18nService.t('ux_marketplace_459_33', _auto_lang(locals()))}{(listing.title if listing else '—')}{I18nService.t('ux_marketplace_459_34', _auto_lang(locals()))}{transaction.seller_price_usd}{I18nService.t('ux_marketplace_459_35', _auto_lang(locals()))}{transaction.buyer_id}</code>")
    except Exception:
        pass
    await callback.answer(I18nService.t('ux_marketplace_467_36', _auto_lang(locals())))

@router.callback_query(F.data.startswith('market_good:'))
async def buyer_confirms_good(callback: CallbackQuery, session, db_user, bot):
    tx_id = int(callback.data.split(':')[1])
    tx = await session.get(MarketTransaction, tx_id)
    if tx is None or tx.buyer_id != db_user.id:
        await callback.answer(I18nService.t('ux_marketplace_475_37', _auto_lang(locals())), show_alert=True)
        return
    if tx.status not in (EscrowStatus.FUNDED, EscrowStatus.DELIVERED):
        await callback.answer(I18nService.t('ux_marketplace_478_38', _auto_lang(locals())), show_alert=True)
        return
    ok = await MarketplaceService.release(session, tx_id, admin_id=None)
    if not ok:
        await callback.answer(I18nService.t('ux_marketplace_482_39', _auto_lang(locals())), show_alert=True)
        return
    await callback.message.edit_text(f"{I18nService.t('ux_marketplace_485_40', _auto_lang(locals()))}{tx_id}{I18nService.t('ux_marketplace_485_41', _auto_lang(locals()))}{tx.seller_price_usd}{I18nService.t('ux_marketplace_485_42', _auto_lang(locals()))}{tx.commission_usd}$</b>.")
    try:
        await bot.send_message(tx.seller_id, f"{I18nService.t('ux_marketplace_491_43', _auto_lang(locals()))}{tx_id}{I18nService.t('ux_marketplace_491_44', _auto_lang(locals()))}{tx.seller_price_usd}$</b>")
    except Exception:
        pass
    notifier = NotificationService(bot)
    await notifier.notify_admin(f'✅ <b>إفراج تلقائي من المشتري</b>\n\nالعملية: #{tx_id}\nللبائع: {tx.seller_price_usd}$\nعمولتك: {tx.commission_usd}$')
    listing = await session.get(MarketListing, tx.listing_id)
    seller_profile = await MarketProfileService.get(session, tx.seller_id)
    seller_stats = MarketProfileService.stats(seller_profile)
    await notifier.notify_successful_market_sale(seller_alias=seller_stats['alias'], listing_title=listing.title if listing else f'عملية #{tx_id}', price_usd=str(tx.total_charged_usd))
    await callback.answer(I18nService.t('ux_marketplace_511_45', _auto_lang(locals())))

@router.callback_query(F.data.startswith('market_bad:'))
async def buyer_reports_bad(callback: CallbackQuery, session, db_user, bot):
    tx_id = int(callback.data.split(':')[1])
    ok = await MarketplaceService.dispute(session, tx_id, db_user.id, 'المشتري أكد أن معلومات المنتج/الحساب خاطئة أو لا تعمل')
    if not ok:
        await callback.answer(I18nService.t('ux_marketplace_524_46', _auto_lang(locals())), show_alert=True)
        return
    tx = await session.get(MarketTransaction, tx_id)
    listing = await session.get(MarketListing, tx.listing_id) if tx else None
    await callback.message.edit_text(f"{I18nService.t('ux_marketplace_529_47', _auto_lang(locals()))}{tx_id}{I18nService.t('ux_marketplace_529_48', _auto_lang(locals()))}")
    await NotificationService(bot).notify_admin(f"⚠️ <b>بلاغ معلومات خاطئة في سوق المستخدمين</b>\n\n🆔 العملية: #{tx_id}\n📄 الإعلان: {(listing.title if listing else '—')}\n👤 البائع: <code>{(tx.seller_id if tx else '—')}</code>\n🛒 المشتري: <code>{db_user.id}</code>\n💰 المبلغ المحجوز: {(tx.total_charged_usd if tx else '—')}$\n\nاختر استرجاع المال للشاري أو الانتظار للتحقق.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='↩️ إعادة المال للشاري', callback_data=f'mkt_refund:{tx_id}', style="danger")], [InlineKeyboardButton(text='⏳ الانتظار للتحقق', callback_data=f'mkt_wait:{tx_id}')], [InlineKeyboardButton(text='🔎 فتح العملية', callback_data=f'mkt_tx:{tx_id}')]]))
    await callback.answer(I18nService.t('ux_marketplace_548_49', _auto_lang(locals())))

@router.callback_query(F.data == 'market_sell')
async def sell_start(callback: CallbackQuery, state: FSMContext):
    if not await MarketplaceService.enabled():
        await callback.answer(I18nService.t('ux_marketplace_557_50', _auto_lang(locals())), show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=label, callback_data=f'market_kind:{key}', style="success")] for key, label in _KIND_LABELS.items()]
    rows.append([InlineKeyboardButton(text='⬅️', callback_data='market:home')])
    await callback.message.edit_text(I18nService.t('ux_marketplace_565_51', _auto_lang(locals())) + I18nService.t('market_kind_question', 'ar'), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data.startswith('market_kind:'))
async def sell_kind(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.split(':')[1]
    if kind not in _KIND_LABELS:
        await callback.answer(I18nService.t('ux_marketplace_575_52', _auto_lang(locals())), show_alert=True)
        return
    await state.set_state(MarketCreateStates.waiting_title)
    await state.update_data(kind=kind, photo_ids=[])
    await callback.message.answer(f'{_KIND_LABELS[kind]}\n\n💡 {_KIND_HINTS[kind]}\n\n' + I18nService.t('market_ask_title', 'ar'))
    await callback.answer()

@router.message(MarketCreateStates.waiting_title)
async def sell_title(message: Message, state: FSMContext):
    title = (message.text or '').strip()
    if not title:
        return await message.answer(I18nService.t('ux_marketplace_590_53', _auto_lang(locals())))
    if title in ('إلغاء', 'cancel'):
        await state.clear()
        return await message.answer(I18nService.t('ux_marketplace_593_54', _auto_lang(locals())))
    await state.update_data(title=title[:128])
    await state.set_state(MarketCreateStates.waiting_description)
    await message.answer(I18nService.t('market_ask_description', 'ar'))

@router.message(MarketCreateStates.waiting_description)
async def sell_description(message: Message, state: FSMContext):
    description = (message.text or '').strip()
    if not description:
        return await message.answer(I18nService.t('ux_marketplace_603_55', _auto_lang(locals())))
    await state.update_data(description=description[:4000])
    await state.set_state(MarketCreateStates.waiting_price)
    await message.answer(I18nService.t('market_ask_price', 'ar'))

@router.message(MarketCreateStates.waiting_price)
async def sell_price(message: Message, state: FSMContext):
    text = (message.text or '').strip().replace('$', '')
    try:
        price = Decimal(text)
    except (InvalidOperation, ValueError):
        return await message.answer(I18nService.t('ux_marketplace_615_56', _auto_lang(locals())))
    if price <= 0:
        return await message.answer(I18nService.t('ux_marketplace_617_57', _auto_lang(locals())))
    await state.update_data(price=str(price))
    data = await state.get_data()
    if data.get('kind') in ('digital_code', 'game_account'):
        await state.set_state(MarketCreateStates.waiting_secret)
        prompt = '🔐 أرسل بيانات التسليم السرية التي ستُفتح للمشتري فقط بعد الدفع.\n\nمثال لحساب: username / password / email / ملاحظات.\nمثال لكود: الكود أو البطاقة.\n\nسيتم حفظها مشفرة، وبعد الشراء يظهر للمشتري زران: المعلومات صحيحة أو خاطئة.'
        return await message.answer(prompt)
    if data.get('kind') == 'sms_number':
        await state.set_state(MarketCreateStates.waiting_proof)
        return await message.answer(I18nService.t('market_ask_proof', 'ar'))
    await state.set_state(MarketCreateStates.waiting_photos)
    await message.answer(I18nService.t('market_ask_photos', 'ar'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=I18nService.t('market_photos_done', 'ar'), callback_data='market_photos_done', style="success")]]))

@router.message(MarketCreateStates.waiting_secret)
async def sell_secret(message: Message, state: FSMContext):
    secret = (message.text or '').strip()
    if not secret:
        return await message.answer(I18nService.t('ux_marketplace_649_58', _auto_lang(locals())))
    await state.update_data(secret=secret)
    await state.set_state(MarketCreateStates.waiting_photos)
    await message.answer(I18nService.t('ux_marketplace_653_59', _auto_lang(locals())) + I18nService.t('market_ask_photos', 'ar'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=I18nService.t('market_photos_done', 'ar'), callback_data='market_photos_done', style="success")]]))

@router.message(MarketCreateStates.waiting_proof)
async def sell_proof(message: Message, state: FSMContext):
    proof = (message.text or '').strip()
    if not proof.isdigit():
        return await message.answer(I18nService.t('ux_marketplace_666_60', _auto_lang(locals())))
    await state.update_data(proof=proof)
    await state.set_state(MarketCreateStates.waiting_photos)
    await message.answer(I18nService.t('market_ask_photos', 'ar'), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=I18nService.t('market_photos_done', 'ar'), callback_data='market_photos_done', style="success")]]))

@router.message(MarketCreateStates.waiting_photos, F.photo)
async def sell_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = list(data.get('photo_ids') or [])
    if len(photos) >= 5:
        return await message.answer(I18nService.t('ux_marketplace_684_61', _auto_lang(locals())))
    photos.append(message.photo[-1].file_id)
    await state.update_data(photo_ids=photos)
    await message.answer(f"{I18nService.t('ux_marketplace_687_62', _auto_lang(locals()))}{len(photos)}/5)")

@router.callback_query(MarketCreateStates.waiting_photos, F.data == 'market_photos_done')
async def sell_photos_done(callback: CallbackQuery, state: FSMContext, session, db_user, bot):
    data = await state.get_data()
    try:
        listing = await MarketplaceService.create_listing(session, seller_id=db_user.id, kind=data.get('kind', 'other'), title=data.get('title', ''), description=data.get('description', ''), price_usd=Decimal(data.get('price', '0')), photo_file_ids=data.get('photo_ids') or [], secret_code=data.get('secret'), ownership_proof=data.get('proof'))
    except MarketError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception as exc:
        await callback.answer(I18nService.t('ux_marketplace_709_63', _auto_lang(locals())), show_alert=True)
        return
    await state.clear()
    notifier = NotificationService(bot)
    photos = (await session.execute(select(MarketListingPhoto).where(MarketListingPhoto.listing_id == listing.id))).scalars().all()
    auto = listing.status == MarketListingStatus.APPROVED
    text = ('✅ <b>إعلان سوق نُشر تلقائياً لبائع موثوق</b>\n\n' if auto else '🏪 <b>إعلان جديد بانتظار موافقتك</b>\n\n') + f"🆔 الإعلان: #{listing.id}\n📦 النوع: {_KIND_LABELS.get(listing.kind, listing.kind)}\n📄 العنوان: <b>{listing.title}</b>\n💵 سعر البائع: {listing.seller_price_usd}$\n👤 البائع: <code>{listing.seller_id}</code> (@{db_user.username or '-'})\n📸 صور: {len(photos)}\n" + ('🔐 يحتوي كوداً مشفراً\n' if listing.secret_payload else '') + (f'🧾 إثبات ملكية: <code>{listing.ownership_proof}</code>\n' if listing.ownership_proof else '') + f"\n📝 <b>الوصف:</b>\n{(listing.description or '—')[:800]}\n\n" + ('تم نشره تلقائياً حسب سجل ثقة البائع.' if auto else 'افتح «🏪 سوق المستخدمين» من لوحة الأدمن لتحديد العمولة والنشر.')
    for photo in photos[:1]:
        try:
            await notifier.notify_admin_photo(photo.file_id, text)
            break
        except Exception:
            continue
    else:
        await notifier.notify_admin(text)
    if auto:
        await callback.message.edit_text(I18nService.t('ux_marketplace_745_64', _auto_lang(locals())))
        await callback.answer(I18nService.t('ux_marketplace_746_65', _auto_lang(locals())))
    else:
        await callback.message.edit_text(I18nService.t('market_published', _lang(db_user)))
        await callback.answer(I18nService.t('ux_marketplace_749_66', _auto_lang(locals())))

@router.callback_query(F.data == 'market_mine')
async def my_listings(callback: CallbackQuery, session, db_user):
    result = await session.execute(select(MarketListing).where(MarketListing.seller_id == db_user.id).order_by(MarketListing.created_at.desc()).limit(15))
    listings = list(result.scalars().all())
    if not listings:
        await callback.message.edit_text(I18nService.t('ux_marketplace_766_67', _auto_lang(locals())), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️', callback_data='market:home')]]))
        await callback.answer()
        return
    status_labels = {MarketListingStatus.PENDING_REVIEW: '⏳ قيد المراجعة', MarketListingStatus.APPROVED: '🟢 منشور', MarketListingStatus.REJECTED: '❌ مرفوض', MarketListingStatus.SOLD: '✅ بِيع', MarketListingStatus.CANCELLED: '🗑 مسحوب', MarketListingStatus.EXPIRED: '⌛ منتهي', MarketListingStatus.DRAFT: '📝 مسودة'}
    lines = [f'#{l.id} {l.title[:24]} — {l.seller_price_usd}$ · {status_labels.get(l.status, l.status.value)}' for l in listings]
    await callback.message.edit_text(I18nService.t('ux_marketplace_787_68', _auto_lang(locals())) + '\n'.join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️', callback_data='market:home')]]))
    await callback.answer()

@router.callback_query(F.data == 'market_bought')
async def my_purchases(callback: CallbackQuery, session, db_user):
    result = await session.execute(select(MarketTransaction).where(MarketTransaction.buyer_id == db_user.id).order_by(MarketTransaction.created_at.desc()).limit(15))
    transactions = list(result.scalars().all())
    if not transactions:
        await callback.message.edit_text(I18nService.t('ux_marketplace_806_69', _auto_lang(locals())), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️', callback_data='market:home')]]))
        await callback.answer()
        return
    status_labels = {EscrowStatus.FUNDED: '🔒 محجوز', EscrowStatus.DELIVERED: '📤 سُلّم', EscrowStatus.RELEASED: '✅ مكتمل', EscrowStatus.REFUNDED: '↩️ مسترد', EscrowStatus.DISPUTED: '⚠️ نزاع', EscrowStatus.AWAITING_BUYER: '⏳'}
    rows = []
    lines = []
    for tx in transactions:
        listing = await session.get(MarketListing, tx.listing_id)
        lines.append(f"#{tx.id} {(listing.title[:20] if listing else '—')} — {tx.total_charged_usd}$ · {status_labels.get(tx.status, tx.status.value)}")
        if tx.status in (EscrowStatus.FUNDED, EscrowStatus.DELIVERED):
            rows.append([InlineKeyboardButton(text=f'⚠️ فتح نزاع #{tx.id}', callback_data=f'market_dispute:{tx.id}', style="success")])
    rows.append([InlineKeyboardButton(text='⬅️', callback_data='market:home')])
    await callback.message.edit_text(I18nService.t('ux_marketplace_835_70', _auto_lang(locals())) + '\n'.join(lines) + I18nService.t('ux_marketplace_836_71', _auto_lang(locals())), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data.startswith('market_dispute:'))
async def open_dispute(callback: CallbackQuery, session, db_user, bot):
    tx_id = int(callback.data.split(':')[1])
    ok = await MarketplaceService.dispute(session, tx_id, db_user.id, 'لم يصلني المعروض')
    if not ok:
        await callback.answer(I18nService.t('ux_marketplace_847_72', _auto_lang(locals())), show_alert=True)
        return
    await NotificationService(bot).notify_admin(f'⚠️ <b>نزاع جديد في السوق</b>\n\n🆔 العملية: #{tx_id}\n🛒 المشتري: <code>{db_user.id}</code>\n\nالأموال مجمّدة حتى قرارك من لوحة السوق.')
    await callback.answer(I18nService.t('ux_marketplace_855_73', _auto_lang(locals())))
    await my_purchases(callback)

@router.callback_query(F.data.startswith('market_cancel:'))
async def cancel_listing(callback: CallbackQuery, session, db_user):
    listing = await session.get(MarketListing, int(callback.data.split(':')[1]))
    if listing is None or listing.seller_id != db_user.id:
        await callback.answer(I18nService.t('ux_marketplace_863_74', _auto_lang(locals())), show_alert=True)
        return
    if listing.status not in (MarketListingStatus.APPROVED, MarketListingStatus.PENDING_REVIEW):
        await callback.answer(I18nService.t('ux_marketplace_866_75', _auto_lang(locals())), show_alert=True)
        return
    listing.status = MarketListingStatus.CANCELLED
    await session.commit()
    await callback.answer(I18nService.t('ux_marketplace_870_76', _auto_lang(locals())))
    await market_home(callback)

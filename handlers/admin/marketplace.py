"""
إدارة سوق المستخدمين — لوحة الأدمن.

الأدمن هنا هو الوسيط والمراقب:
1) يراجع كل إعلان قبل نشره ويحدد نسبته من العمولة.
2) يتابع العمليات المحجوزة (Escrow) ويؤكد التسليم فيفرج الأموال.
3) يحسم النزاعات: إفراج للبائع أو استرداد للمشتري.

لا يصل أي مبلغ للبائع إلا عبر زر الإفراج هنا، ولا تُصرف العمولة
إلا في نفس اللحظة، فلا تضيع ولا تُحتسب مرتين.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import (
    EscrowStatus,
    MarketListing,
    MarketListingPhoto,
    MarketListingStatus,
    MarketTransaction,
    User,
)
from filters.admin_filter import IsAdmin
from services.audit_service import AuditAction, AuditService
from services.feature_service import FeatureService
from services.market_profile_service import MarketProfileService
from services.marketplace_service import MarketplaceService, MarketError
from services.notification_service import NotificationService
from sqlalchemy import select
from states.states import AdminMarketStates

from decimal import Decimal

router = Router(name="admin_marketplace")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_KIND_LABELS = {
    "game_account": "🎮 حساب لعبة",
    "digital_code": "🔑 كود/بطاقة",
    "sms_number": "📱 رقم SMS",
    "service": "🛠 خدمة",
    "other": "📦 أخرى",
}

_STATUS_LABELS = {
    EscrowStatus.FUNDED: "🔒 محجوز",
    EscrowStatus.DELIVERED: "📤 سُلّم",
    EscrowStatus.RELEASED: "✅ أُفرج",
    EscrowStatus.REFUNDED: "↩️ مسترد",
    EscrowStatus.DISPUTED: "⚠️ نزاع",
    EscrowStatus.AWAITING_BUYER: "⏳ بانتظار مشترٍ",
}


# ══════════════ اللوحة الرئيسية ══════════════


@router.callback_query(F.data == "admin:marketplace")
async def market_home(callback: CallbackQuery, session):
    stats = await MarketplaceService.stats(session)
    default_commission = await MarketplaceService.default_commission()
    rows = [
        [InlineKeyboardButton(text=f"📥 موافقات معلّقة ({stats['pending_review']})", callback_data="mkt_pending")],
        [InlineKeyboardButton(text="⚠️ النزاعات المفتوحة", callback_data="mkt_disputes")],
        [InlineKeyboardButton(text="🔒 عمليات محجوزة", callback_data="mkt_escrow")],
        [InlineKeyboardButton(text="🏪 الإعلانات المنشورة", callback_data="mkt_listings")],
        [InlineKeyboardButton(text="🟢 إعلاناتي المنشورة", callback_data="admin:main")],
    ]
    await callback.message.edit_text(
        "🏪 <b>سوق المستخدمين</b>\n\n"
        f"الإعلانات: <b>{stats['listings']}</b>\n"
        f"بانتظار موافقتك: <b>{stats['pending_review']}</b>\n"
        f"حجم التداول: <b>{stats['volume_usd']}$</b>\n"
        f"عمولاتك المحصّلة: <b>{stats['commission_usd']}$</b>\n"
        f"نزاعات مفتوحة: <b>{stats['open_disputes']}</b>\n\n"
        f"العمولة الافتراضية المقترحة: <b>{default_commission}%</b>\n\n"
        "أنت الوسيط: لا يصل مبلغ للبائع إلا بعد تأكيدك.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows[:-1] + [[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")]]),
    )
    await callback.answer()


# ══════════════ الموافقات المعلّقة ══════════════


@router.callback_query(F.data == "mkt_pending")
async def pending_list(callback: CallbackQuery, session):
    listings = await MarketplaceService.pending_listings(session, 15)
    if not listings:
        await callback.message.edit_text(
            "📥 لا إعلانات بانتظار المراجعة.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:marketplace")]]
            ),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=f"#{l.id} {_KIND_LABELS.get(l.kind, l.kind)} — {l.seller_price_usd}$",
                              callback_data=f"mkt_review:{l.id}")]
        for l in listings
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:marketplace")])
    await callback.message.edit_text(
        f"📥 <b>إعلانات بانتظار موافقتك</b> ({len(listings)})\n\n"
        "افتح الإعلان، حدد عمولتك، ثم اسمح بالنشر.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mkt_review:"))
async def review_listing(callback: CallbackQuery, session):
    listing = await session.get(MarketListing, int(callback.data.split(":")[1]))
    if listing is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    seller = await session.get(User, listing.seller_id)
    photos = (
        await session.execute(
            select(MarketListingPhoto)
            .where(MarketListingPhoto.listing_id == listing.id)
            .order_by(MarketListingPhoto.sort_order)
        )
    ).scalars().all()

    # نرسل الصور إن وُجدت حتى يرى الأدمن ما سيُنشر
    for photo in photos:
        try:
            await callback.message.answer_photo(photo.file_id)
        except Exception:
            pass

    rows = [
        [InlineKeyboardButton(text="✅ السماح بالنشر (تحديد العمولة)", callback_data=f"mkt_comm:{listing.id}")],
        [InlineKeyboardButton(text="❌ رفض الإعلان", callback_data=f"mkt_reject:{listing.id}")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="mkt_pending")],
    ]
    await callback.message.edit_text(
        f"🔎 <b>إعلان #{listing.id}</b>\n\n"
        f"النوع: {_KIND_LABELS.get(listing.kind, listing.kind)}\n"
        f"العنوان: <b>{listing.title}</b>\n"
        f"سعر البائع: <b>{listing.seller_price_usd}$</b>\n"
        f"البائع: <code>{listing.seller_id}</code>"
        + (f" (@{seller.username})" if seller and seller.username else "")
        + "\n\n"
        f"📝 <b>الوصف:</b>\n{(listing.description or '—')[:1200]}\n\n"
        + ("🔐 يحتوي كوداً مشفراً يُسلَّم للمشتري تلقائياً.\n" if listing.secret_payload else "")
        + (f"🧾 إثبات الملكية: <code>{listing.ownership_proof}</code>\n" if listing.ownership_proof else ""),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mkt_comm:"))
async def ask_commission(callback: CallbackQuery, state: FSMContext, session):
    listing_id = int(callback.data.split(":")[1])
    listing = await session.get(MarketListing, listing_id)
    if listing is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    default = await MarketplaceService.default_commission()
    await state.set_state(AdminMarketStates.waiting_commission)
    await state.update_data(listing_id=listing_id)
    preview = []
    for percent in (Decimal("3"), Decimal("5"), Decimal("10"), default):
        total = listing.seller_price_usd + (listing.seller_price_usd * percent / Decimal("100"))
        preview.append(f"• {percent}% → المشتري يدفع {total.quantize(Decimal('0.01'))}$")
    await callback.message.answer(
        f"💰 حدد عمولتك على الإعلان #{listing_id}\n"
        f"سعر البائع: <b>{listing.seller_price_usd}$</b>\n\n"
        + "\n".join(preview)
        + "\n\nأرسل النسبة المئوية (مثال: <code>7</code>) أو «إلغاء»:"
    )
    await callback.answer()


@router.message(AdminMarketStates.waiting_commission)
async def save_commission(message: Message, state: FSMContext, session, db_user, bot):
    data = await state.get_data()
    text = (message.text or "").strip().replace("٪", "").replace("%", "")
    if text in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    try:
        percent = Decimal(text)
    except Exception:
        return await message.answer("⚠️ أرسل رقماً مثل <code>7</code>")
    if percent < 0 or percent > 90:
        return await message.answer("⚠️ النسبة يجب أن تكون بين 0 و90.")

    listing_id = data.get("listing_id")
    try:
        listing = await MarketplaceService.approve(session, listing_id, db_user.id, percent)
    except MarketError as exc:
        await state.clear()
        return await message.answer(f"⚠️ {exc}")
    if listing is None:
        await state.clear()
        return await message.answer("⚠️ الإعلان لم يعد بانتظار المراجعة.")

    total = await MarketplaceService.total_price(listing)
    commission = await MarketplaceService.commission_amount(listing)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.ACTIVATE,
        entity_type="market_listing",
        entity_id=listing.id,
        entity_name=listing.title,
        new_value=f"commission={percent}%",
        description="نشر إعلان في سوق المستخدمين",
        session=session,
    )
    await state.clear()

    # إشعار البائع بالنشر
    try:
        await bot.send_message(
            listing.seller_id,
            f"✅ <b>نُشر إعلانك في السوق</b>\n\n"
            f"📄 {listing.title}\n"
            f"💵 ستستلم: {listing.seller_price_usd}$\n"
            f"🏷 يشتريه الآخرون بـ: {total}$\n\n"
            "سيصلك إشعار عند البيع.",
        )
    except Exception:
        pass

    await message.answer(
        f"✅ <b>نُشر الإعلان #{listing.id}</b>\n\n"
        f"سعر البائع: {listing.seller_price_usd}$\n"
        f"عمولتك: {commission}$ ({percent}%)\n"
        f"يدفع المشتري: <b>{total}$</b>"
    )


@router.callback_query(F.data.startswith("mkt_reject:"))
async def reject_listing(callback: CallbackQuery, session, db_user, bot):
    listing_id = int(callback.data.split(":")[1])
    listing = await MarketplaceService.reject(
        session, listing_id, db_user.id, "مرفوض من الإدارة"
    )
    if listing is None:
        await callback.answer("سبق معالجته.", show_alert=True)
        return
    try:
        await bot.send_message(
            listing.seller_id, f"❌ رُفض إعلانك «{listing.title}» من الإدارة."
        )
    except Exception:
        pass
    await callback.answer("❌ رُفض الإعلان.")
    await pending_list(callback)


# ══════════════ العمليات المحجوزة ══════════════


@router.callback_query(F.data == "mkt_escrow")
async def escrow_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(MarketTransaction)
        .where(MarketTransaction.status.in_([EscrowStatus.FUNDED, EscrowStatus.DELIVERED]))
        .order_by(MarketTransaction.created_at.desc())
        .limit(15)
    )
    transactions = list(result.scalars().all())
    if not transactions:
        await callback.message.edit_text(
            "🔒 لا عمليات محجوزة حالياً.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:marketplace")]]
            ),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(
            text=f"#{t.id} {_STATUS_LABELS.get(t.status, t.status.value)} — {t.total_charged_usd}$",
            callback_data=f"mkt_tx:{t.id}",
        )]
        for t in transactions
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:marketplace")])
    await callback.message.edit_text(
        f"🔒 <b>عمليات محجوزة</b> ({len(transactions)})\n\n"
        "الأموال عندك كوسيط. أكد التسليم لتُفرج للبائع ناقص عمولتك.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mkt_tx:"))
async def transaction_detail(callback: CallbackQuery, session):
    tx = await session.get(MarketTransaction, int(callback.data.split(":")[1]))
    if tx is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    listing = await session.get(MarketListing, tx.listing_id)
    seller = await session.get(User, tx.seller_id)
    buyer = await session.get(User, tx.buyer_id)

    rows = []
    if tx.status == EscrowStatus.FUNDED:
        rows.append([InlineKeyboardButton(text="📤 تأكيد أن البائع سلّم", callback_data=f"mkt_deliver:{tx.id}")])
    if tx.status in (EscrowStatus.FUNDED, EscrowStatus.DELIVERED):
        rows.append([
            InlineKeyboardButton(text="✅ إفراج للبائع", callback_data=f"mkt_release:{tx.id}"),
            InlineKeyboardButton(text="↩️ استرداد للمشتري", callback_data=f"mkt_refund:{tx.id}"),
        ])
    if listing and listing.secret_payload:
        rows.append([InlineKeyboardButton(text="🔑 عرض الكود (وسيط)", callback_data=f"mkt_peek:{tx.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="mkt_escrow")])

    await callback.message.edit_text(
        f"🧾 <b>عملية #{tx.id}</b>\n\n"
        f"الإعلان: {listing.title if listing else '—'}\n"
        f"النوع: {_KIND_LABELS.get(listing.kind, listing.kind) if listing else '—'}\n\n"
        f"👤 البائع: <code>{tx.seller_id}</code>"
        + (f" (@{seller.username})" if seller and seller.username else "")
        + f"\n🛒 المشتري: <code>{tx.buyer_id}</code>"
        + (f" (@{buyer.username})" if buyer and buyer.username else "")
        + f"\n\n💵 سعر البائع: {tx.seller_price_usd}$\n"
        f"🏷 عمولتك: {tx.commission_usd}$ ({tx.commission_percent}%)\n"
        f"💰 دُفع إجمالاً: <b>{tx.total_charged_usd}$</b>\n"
        + (f"⭐ منها بالنقاط: {tx.points_used} ({tx.points_usd}$)\n" if tx.points_used else "")
        + f"\nالحالة: {_STATUS_LABELS.get(tx.status, tx.status.value)}"
        + (f"\n📝 ملاحظة: {tx.dispute_note}" if tx.dispute_note else ""),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mkt_deliver:"))
async def mark_delivered(callback: CallbackQuery, session, db_user):
    ok = await MarketplaceService.mark_delivered(session, int(callback.data.split(":")[1]), db_user.id)
    await callback.answer("📤 سُجّل التسليم." if ok else "⚠️ الحالة لا تسمح.")
    if ok:
        await transaction_detail(callback)


@router.callback_query(F.data.startswith("mkt_release:"))
async def release_tx(callback: CallbackQuery, session, db_user, bot):
    tx_id = int(callback.data.split(":")[1])
    tx = await session.get(MarketTransaction, tx_id)
    ok = await MarketplaceService.release(session, tx_id, admin_id=db_user.id)
    if not ok:
        await callback.answer("⚠️ الحالة لا تسمح بالإفراج.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="market_transaction",
        entity_id=tx_id,
        entity_name=f"عملية سوق #{tx_id}",
        new_value="released",
        description=f"إفراج {tx.seller_price_usd}$ للبائع، عمولة {tx.commission_usd}$",
        session=session,
    )
    try:
        await bot.send_message(
            tx.seller_id,
            f"💰 <b>تم بيع إعلانك</b>\n\n"
            f"أُضيف إلى رصيدك: <b>{tx.seller_price_usd}$</b>\n"
            f"العملية #{tx_id}",
        )
        await bot.send_message(tx.buyer_id, f"✅ اكتملت عملية الشراء #{tx_id}.")
    except Exception:
        pass
    listing = await session.get(MarketListing, tx.listing_id)
    seller_profile = await MarketProfileService.get(session, tx.seller_id)
    seller_stats = MarketProfileService.stats(seller_profile)
    await NotificationService(bot).notify_successful_market_sale(
        seller_alias=seller_stats["alias"],
        listing_title=listing.title if listing else f"عملية #{tx_id}",
        price_usd=str(tx.total_charged_usd),
    )
    await callback.answer(f"✅ أُفرج {tx.seller_price_usd}$ للبائع.")
    await escrow_list(callback)


@router.callback_query(F.data.startswith("mkt_wait:"))
async def wait_verify_tx(callback: CallbackQuery, session, db_user):
    tx_id = int(callback.data.split(":")[1])
    tx = await session.get(MarketTransaction, tx_id)
    if tx is None or tx.status != EscrowStatus.DISPUTED:
        await callback.answer("العملية ليست نزاعاً مفتوحاً.", show_alert=True)
        return
    tx.dispute_note = (tx.dispute_note or "") + " | الإدارة اختارت الانتظار للتحقق"
    await session.commit()
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="market_transaction",
        entity_id=tx_id,
        entity_name=f"عملية سوق #{tx_id}",
        new_value="wait_verify",
        description="تأجيل حسم نزاع السوق للتحقق",
        session=session,
    )
    await callback.answer("⏳ تم ترك المبلغ محجوزاً لحين التحقق.")
    await transaction_detail(callback, session)


@router.callback_query(F.data.startswith("mkt_refund:"))
async def refund_tx(callback: CallbackQuery, session, db_user, bot):
    tx_id = int(callback.data.split(":")[1])
    tx = await session.get(MarketTransaction, tx_id)
    ok = await MarketplaceService.refund(session, tx_id, admin_id=db_user.id, note="قرار الإدارة")
    if not ok:
        await callback.answer("⚠️ الحالة لا تسمح بالاسترداد.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="market_transaction",
        entity_id=tx_id,
        entity_name=f"عملية سوق #{tx_id}",
        new_value="refunded",
        description="استرداد عملية سوق للمشتري",
        session=session,
    )
    try:
        await bot.send_message(tx.buyer_id, f"↩️ أُعيد مبلغ عملية #{tx_id} إلى رصيدك.")
        await bot.send_message(tx.seller_id, f"↩️ أُلغيت عملية البيع #{tx_id} وأُعيد المبلغ للمشتري.")
    except Exception:
        pass
    await callback.answer("↩️ استُرد المبلغ.")
    await escrow_list(callback)


@router.callback_query(F.data.startswith("mkt_peek:"))
async def peek_secret(callback: CallbackQuery, session):
    """الأدمن وسيط، فيمكنه رؤية الكود عند الحاجة لحل نزاع."""
    tx = await session.get(MarketTransaction, int(callback.data.split(":")[1]))
    if tx is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    try:
        code = await MarketplaceService.reveal_secret(session, tx.id, tx.buyer_id)
    except MarketError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("🔑 الكود في الرسالة التالية.", show_alert=False)
    await callback.message.answer(f"🔑 كود العملية #{tx.id}:\n<code>{code}</code>")


# ══════════════ النزاعات ══════════════


@router.callback_query(F.data == "mkt_disputes")
async def disputes_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(MarketTransaction)
        .where(MarketTransaction.status == EscrowStatus.DISPUTED)
        .order_by(MarketTransaction.created_at.desc())
        .limit(15)
    )
    disputes = list(result.scalars().all())
    if not disputes:
        await callback.message.edit_text(
            "⚠️ لا نزاعات مفتوحة.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:marketplace")]]
            ),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=f"⚠️ نزاع #{t.id} — {t.total_charged_usd}$", callback_data=f"mkt_tx:{t.id}")]
        for t in disputes
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:marketplace")])
    await callback.message.edit_text(
        f"⚠️ <b>نزاعات مفتوحة</b> ({len(disputes)})\n\n"
        "الأموال مجمّدة. افتح النزاع واختر: إفراج للبائع أو استرداد للمشتري.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ══════════════ الإعلانات المنشورة ══════════════


@router.callback_query(F.data == "mkt_listings")
async def listings_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(MarketListing)
        .where(MarketListing.status == MarketListingStatus.APPROVED)
        .order_by(MarketListing.published_at.desc())
        .limit(15)
    )
    listings = list(result.scalars().all())
    if not listings:
        await callback.message.edit_text(
            "🏪 لا إعلانات منشورة.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:marketplace")]]
            ),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(
            text=f"#{l.id} {l.title[:22]} — {l.seller_price_usd}$ (+{l.commission_percent}%)",
            callback_data=f"mkt_manage:{l.id}",
        )]
        for l in listings
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:marketplace")])
    await callback.message.edit_text(
        f"🏪 <b>الإعلانات المنشورة</b> ({len(listings)})",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mkt_manage:"))
async def manage_listing(callback: CallbackQuery, session):
    listing_id = int(callback.data.split(":")[1])
    listing = await session.get(MarketListing, listing_id)
    if listing is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    listing.status = MarketListingStatus.CANCELLED
    await session.commit()
    await callback.answer("🗑 أُزيل الإعلان من العرض.")
    await listings_list(callback)

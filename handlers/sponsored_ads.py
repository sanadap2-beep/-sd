"""User flow for paid sponsored ads."""

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, select

from database.models import SponsoredAd
from keyboards.main_menu import back_to_main_kb
from services.currency_service import CurrencyService
from services.notification_service import NotificationService
from services.sponsored_ad_service import SponsoredAdError, SponsoredAdService
from states.states import SponsoredAdStates

router = Router(name="sponsored_ads")


def _ads_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ إضافة إعلان جديد", callback_data="ads:new")],
        [InlineKeyboardButton(text="📋 إعلاناتي الحالية", callback_data="ads:mine")],
        [InlineKeyboardButton(text="📢 الإعلانات المنشورة", callback_data="ads:active")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="back_to_main")],
    ])


@router.callback_query(F.data == "ads:home")
async def ads_home(callback: CallbackQuery, session, db_user):
    base = await CurrencyService.format_dual(SponsoredAdService.BASE_PRICE_USD, db_user, session)
    renew = await CurrencyService.format_dual(SponsoredAdService.RENEW_PRICE_USD, db_user, session)
    await callback.message.edit_text(
        "📢 <b>إعلانات المستخدمين</b>\n\n"
        f"سعر نشر الإعلان: <b>{base}</b> لمدة 24 ساعة.\n"
        f"تجديد الإعلان: <b>{renew}</b> يضيف 15 ساعة.\n\n"
        "الإعلان يحتاج موافقة الإدارة، ثم يظهر داخل البوت ويعاد نشره في قناة الإشعارات كل نصف ساعة.",
        reply_markup=_ads_home_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "ads:new")
async def ad_new(callback: CallbackQuery, state: FSMContext, session, db_user):
    if Decimal(str(db_user.balance or 0)) < SponsoredAdService.BASE_PRICE_USD:
        price = await CurrencyService.format_dual(SponsoredAdService.BASE_PRICE_USD, db_user, session)
        await callback.answer(f"رصيدك غير كافٍ. سعر الإعلان {price}", show_alert=True)
        return
    await state.clear()
    await state.set_state(SponsoredAdStates.waiting_title)
    await callback.message.answer("📢 أرسل عنوان الإعلان المختصر:")
    await callback.answer()


@router.message(SponsoredAdStates.waiting_title)
async def ad_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if len(title) < 3:
        await message.answer("⚠️ العنوان قصير جداً.")
        return
    await state.update_data(title=title[:128])
    await state.set_state(SponsoredAdStates.waiting_body)
    await message.answer("📝 أرسل نص الإعلان والمعلومات المهمة:")


@router.message(SponsoredAdStates.waiting_body)
async def ad_body(message: Message, state: FSMContext):
    body = (message.text or "").strip()
    if len(body) < 5:
        await message.answer("⚠️ النص قصير جداً.")
        return
    await state.update_data(body=body[:4000])
    await state.set_state(SponsoredAdStates.waiting_item_type)
    await message.answer("🏷 ما نوع الإعلان؟ مثال: منتج، حساب، كود، خدمة، عرض خاص")


@router.message(SponsoredAdStates.waiting_item_type)
async def ad_item_type(message: Message, state: FSMContext):
    await state.update_data(item_type=(message.text or "").strip()[:64])
    await state.set_state(SponsoredAdStates.waiting_price)
    await message.answer("💰 أرسل السعر النهائي الذي تريد عرضه في الإعلان، مثال: 10$ أو 1300 ل.س")


@router.message(SponsoredAdStates.waiting_price)
async def ad_price(message: Message, state: FSMContext):
    await state.update_data(price_text=(message.text or "").strip()[:64])
    await state.set_state(SponsoredAdStates.waiting_contact)
    await message.answer("📞 أرسل رقم أو يوزر التواصل الذي سيظهر بالإعلان:")


@router.message(SponsoredAdStates.waiting_contact)
async def ad_contact(message: Message, state: FSMContext):
    contact = (message.text or "").strip()
    if len(contact) < 3:
        await message.answer("⚠️ معلومات التواصل قصيرة جداً.")
        return
    await state.update_data(contact=contact[:128], photos=[])
    await state.set_state(SponsoredAdStates.waiting_photos)
    await message.answer(
        "🖼 أرسل صور الإعلان حتى 5 صور، أو اضغط تم بدون صور.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ تم", callback_data="ads:photos_done")]]),
    )


@router.message(SponsoredAdStates.waiting_photos, F.photo)
async def ad_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = list(data.get("photos") or [])
    if len(photos) >= 5:
        await message.answer("وصلت للحد الأقصى 5 صور. اضغط تم.")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(f"📸 أُضيفت الصورة ({len(photos)}/5)")


@router.callback_query(SponsoredAdStates.waiting_photos, F.data == "ads:photos_done")
async def ad_confirm(callback: CallbackQuery, state: FSMContext, session, db_user):
    data = await state.get_data()
    price = await CurrencyService.format_dual(SponsoredAdService.BASE_PRICE_USD, db_user, session)
    summary = (
        "📢 <b>تأكيد الإعلان</b>\n\n"
        f"العنوان: <b>{data.get('title')}</b>\n"
        f"النوع: {data.get('item_type')}\n"
        f"السعر الظاهر: {data.get('price_text')}\n"
        f"التواصل: <code>{data.get('contact')}</code>\n"
        f"الصور: {len(data.get('photos') or [])}\n\n"
        f"سيتم خصم <b>{price}</b> وإرسال الإعلان للإدارة للمراجعة."
    )
    await state.set_state(SponsoredAdStates.waiting_confirm)
    await callback.message.edit_text(
        summary,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تأكيد ودفع", callback_data="ads:submit")],
            [InlineKeyboardButton(text="❌ إلغاء", callback_data="ads:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(SponsoredAdStates.waiting_confirm, F.data == "ads:submit")
async def ad_submit(callback: CallbackQuery, state: FSMContext, session, db_user, bot):
    data = await state.get_data()
    try:
        ad = await SponsoredAdService.create_pending(
            session,
            db_user.id,
            title=data.get("title", ""),
            body=data.get("body", ""),
            item_type=data.get("item_type"),
            price_text=data.get("price_text"),
            contact=data.get("contact", ""),
            photo_file_ids=data.get("photos") or [],
        )
    except SponsoredAdError as exc:
        await callback.answer(str(exc), show_alert=True)
        await state.clear()
        return
    await state.clear()
    await callback.message.edit_text(
        f"✅ تم إرسال إعلانك للمراجعة.\nرقم الإعلان: #{ad.id}\nسيصلك إشعار عند القبول أو الرفض.",
        reply_markup=back_to_main_kb(),
    )
    notifier = NotificationService(bot)
    text = (
        "📢 <b>إعلان جديد بانتظار المراجعة</b>\n\n"
        f"🆔 الإعلان: #{ad.id}\n"
        f"👤 المستخدم: <code>{db_user.telegram_id}</code> (@{db_user.username or '-'})\n"
        f"💰 المدفوع: {SponsoredAdService.BASE_PRICE_USD}$\n\n"
        f"{SponsoredAdService.render(ad)}"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ قبول ونشر", callback_data=f"admin:ad_accept:{ad.id}")],
        [InlineKeyboardButton(text="❌ رفض وإرجاع المال", callback_data=f"admin:ad_reject:{ad.id}")],
    ])
    photos = SponsoredAdService.photos(ad)
    if photos:
        await notifier.notify_admin_photo(photos[0], text, reply_markup=markup)
    else:
        await notifier.notify_admin(text, reply_markup=markup)


@router.callback_query(F.data == "ads:active")
async def active_ads(callback: CallbackQuery, session):
    ads = list((await session.execute(select(SponsoredAd).where(SponsoredAd.status == "active").order_by(desc(SponsoredAd.starts_at)).limit(10))).scalars().all())
    if not ads:
        await callback.message.edit_text("📢 لا توجد إعلانات منشورة حالياً.", reply_markup=_ads_home_kb())
        await callback.answer()
        return
    rows = [[InlineKeyboardButton(text=f"#{ad.id} {ad.title[:28]}", callback_data=f"ads:view:{ad.id}")] for ad in ads]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="ads:home")])
    await callback.message.edit_text("📢 <b>الإعلانات المنشورة داخل البوت</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("ads:view:"))
async def view_ad(callback: CallbackQuery, session):
    ad = await session.get(SponsoredAd, int(callback.data.rsplit(":", 1)[1]))
    if not ad or ad.status != "active":
        await callback.answer("الإعلان غير متاح.", show_alert=True)
        return
    await callback.message.edit_text(SponsoredAdService.render(ad), reply_markup=_ads_home_kb())
    await callback.answer()


@router.callback_query(F.data == "ads:mine")
async def my_ads(callback: CallbackQuery, session, db_user):
    ads = list((await session.execute(select(SponsoredAd).where(SponsoredAd.user_id == db_user.id).order_by(desc(SponsoredAd.created_at)).limit(15))).scalars().all())
    if not ads:
        await callback.message.edit_text("📋 لا توجد إعلانات لك بعد.", reply_markup=_ads_home_kb())
        await callback.answer()
        return
    rows = []
    lines = ["📋 <b>إعلاناتي</b>", ""]
    for ad in ads:
        lines.append(f"#{ad.id} {ad.title[:24]} — {ad.status} — دفع: {ad.amount_paid_usd}$")
        if ad.status in {"active", "expired"}:
            rows.append([InlineKeyboardButton(text=f"🔁 تجديد #{ad.id} بـ 1$", callback_data=f"ads:renew:{ad.id}")])
    rows.append([InlineKeyboardButton(text="➕ إضافة إعلان", callback_data="ads:new")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="ads:home")])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("ads:renew:"))
async def renew_ad(callback: CallbackQuery, session, db_user, bot):
    ad_id = int(callback.data.rsplit(":", 1)[1])
    try:
        ad = await SponsoredAdService.renew(session, ad_id, db_user.id, bot)
    except SponsoredAdError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("✅ تم تجديد الإعلان ونشره فوراً.")
    await callback.message.edit_text(
        f"✅ تم تجديد إعلانك #{ad.id}.\nينتهي الآن في: {ad.ends_at.strftime('%Y-%m-%d %H:%M')} UTC",
        reply_markup=_ads_home_kb(),
    )

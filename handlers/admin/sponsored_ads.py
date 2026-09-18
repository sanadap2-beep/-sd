"""Admin management for sponsored ads."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, select

from database.models import SponsoredAd, User
from filters.admin_filter import IsAdmin
from services.sponsored_ad_service import SponsoredAdService
from states.states import AdminSponsoredAdStates

router = Router(name="admin_sponsored_ads")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:ads")
async def admin_ads_home(callback: CallbackQuery, session):
    stats = await SponsoredAdService.admin_stats(session)
    rows = [
        [InlineKeyboardButton(text=f"📥 بانتظار المراجعة ({stats['pending']})", callback_data="admin:ads_pending")],
        [InlineKeyboardButton(text=f"🟢 الإعلانات النشطة ({stats['active']})", callback_data="admin:ads_active")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")],
    ]
    await callback.message.edit_text(
        "📢 <b>إدارة الإعلانات المدفوعة</b>\n\n"
        f"إعلانات اليوم: <b>{stats['today']}</b>\n"
        f"ربح اليوم: <b>{stats['money_today']}$</b>\n"
        f"بانتظار المراجعة: <b>{stats['pending']}</b>\n"
        f"نشطة الآن: <b>{stats['active']}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def _ads_list(callback: CallbackQuery, session, status: str):
    ads = list((await session.execute(select(SponsoredAd).where(SponsoredAd.status == status).order_by(desc(SponsoredAd.created_at)).limit(20))).scalars().all())
    if not ads:
        await callback.message.edit_text(f"لا توجد إعلانات بحالة {status}.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ads")]]))
        await callback.answer()
        return
    rows = [[InlineKeyboardButton(text=f"#{ad.id} {ad.title[:28]} · {ad.amount_paid_usd}$", callback_data=f"admin:ad_view:{ad.id}")] for ad in ads]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ads")])
    await callback.message.edit_text(f"📢 إعلانات {status}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "admin:ads_pending")
async def admin_ads_pending(callback: CallbackQuery, session):
    await _ads_list(callback, session, "pending")


@router.callback_query(F.data == "admin:ads_active")
async def admin_ads_active(callback: CallbackQuery, session):
    await _ads_list(callback, session, "active")


@router.callback_query(F.data.startswith("admin:ad_view:"))
async def admin_ad_view(callback: CallbackQuery, session):
    ad = await session.get(SponsoredAd, int(callback.data.rsplit(":", 1)[1]))
    if ad is None:
        await callback.answer("الإعلان غير موجود.", show_alert=True)
        return
    left = "—"
    if ad.ends_at:
        from datetime import datetime
        remaining = ad.ends_at - datetime.utcnow()
        left = f"{max(0, int(remaining.total_seconds() // 3600))} ساعة"
    user = await session.get(User, ad.user_id)
    rows = []
    if ad.status == "pending":
        rows.append([InlineKeyboardButton(text="✅ قبول ونشر", callback_data=f"admin:ad_accept:{ad.id}")])
        rows.append([InlineKeyboardButton(text="❌ رفض وإرجاع المال", callback_data=f"admin:ad_reject:{ad.id}", style="danger")])
    if ad.status == "active":
        rows.append([InlineKeyboardButton(text="📝 تعديل النص", callback_data=f"admin:ad_edit:{ad.id}")])
        rows.append([InlineKeyboardButton(text="🗑 حذف الإعلان", callback_data=f"admin:ad_delete:{ad.id}", style="danger")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ads")])
    await callback.message.edit_text(
        f"📢 <b>إعلان #{ad.id}</b>\n\n"
        f"المستخدم: <code>{user.telegram_id if user else ad.user_id}</code>\n"
        f"الحالة: {ad.status}\n"
        f"المدفوع: {ad.amount_paid_usd}$\n"
        f"الوقت المتبقي: {left}\n\n"
        f"{SponsoredAdService.render(ad)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:ad_accept:"))
async def admin_ad_accept(callback: CallbackQuery, session, db_user, bot):
    ad_id = int(callback.data.rsplit(":", 1)[1])
    ad = await SponsoredAdService.approve(session, ad_id, db_user.id, bot)
    if ad is None:
        await callback.answer("لا يمكن قبول هذا الإعلان.", show_alert=True)
        return
    await callback.message.edit_text(
        f"✅ تم قبول ونشر الإعلان #{ad.id}.\n"
        f"وقت النشر: {ad.starts_at.strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"ينتهي: {ad.ends_at.strftime('%Y-%m-%d %H:%M')} UTC",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ إدارة الإعلانات", callback_data="admin:ads")]]),
    )
    await callback.answer("✅ نُشر الإعلان.")


@router.callback_query(F.data.startswith("admin:ad_reject:"))
async def admin_ad_reject(callback: CallbackQuery, session, db_user, bot):
    ad_id = int(callback.data.rsplit(":", 1)[1])
    ad = await SponsoredAdService.reject(session, ad_id, db_user.id, bot)
    if ad is None:
        await callback.answer("لا يمكن رفض هذا الإعلان.", show_alert=True)
        return
    await callback.answer("↩️ رُفض الإعلان وأُعيد المال.")
    await admin_ads_home(callback, session)


@router.callback_query(F.data.startswith("admin:ad_delete:"))
async def admin_ad_delete(callback: CallbackQuery, session):
    ad = await session.get(SponsoredAd, int(callback.data.rsplit(":", 1)[1]))
    if ad is None:
        await callback.answer("الإعلان غير موجود.", show_alert=True)
        return
    ad.status = "deleted"
    await session.commit()
    await callback.answer("🗑 تم حذف الإعلان من البوت وإيقاف إعادة نشره.")
    await admin_ads_home(callback, session)


@router.callback_query(F.data.startswith("admin:ad_edit:"))
async def admin_ad_edit(callback: CallbackQuery, state: FSMContext):
    ad_id = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(edit_ad_id=ad_id)
    await state.set_state(AdminSponsoredAdStates.waiting_body)
    await callback.message.answer("📝 أرسل النص الجديد للإعلان. سيتم استبدال نص الإعلان فقط مع بقاء العنوان والتواصل.")
    await callback.answer()


@router.message(AdminSponsoredAdStates.waiting_body)
async def admin_ad_edit_save(message: Message, state: FSMContext, session):
    data = await state.get_data()
    ad_id = data.get("edit_ad_id")
    if not ad_id:
        return
    ad = await session.get(SponsoredAd, int(ad_id))
    if ad is None:
        await message.answer("الإعلان غير موجود.")
        await state.clear()
        return
    ad.body = (message.text or "")[:4000]
    await session.commit()
    await state.clear()
    await message.answer("✅ تم تعديل نص الإعلان.")

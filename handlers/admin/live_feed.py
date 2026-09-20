"""
📡 مباشر البوت — شاشة لوحة الأدمن لأحداث الشراء والاسترجاع الفورية.

كل عملية شراء تنجح وتُفعَّل (خُصم الرصيد) وكل استرجاع/فشل (رجوع الرصيد
للمستخدم) يصل إشعار مباشر لقناة الأدمن فقط، بلا إزعاج القناة العامة.
من هنا يتحكم صاحب البوت بالتفعيل ونوع الأحداث مع أزرار تجربة فورية.

الإعدادات مخزّنة في إضافة ``live_bot_feed`` (فئة «الإشعارات» في مركز
الإضافات) لتبقى موحّدة مع بقية مفاتيح البوت.
"""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from filters.admin_filter import IsAdmin
from services.audit_service import AuditAction, AuditService
from services.feature_service import FeatureService
from services.notification_service import NotificationService

router = Router(name="admin_live_feed")
router.callback_query.filter(IsAdmin())

FEATURE_KEY = "live_bot_feed"


async def _current_state() -> dict:
    return {
        "enabled": await FeatureService.enabled(FEATURE_KEY, default=True),
        "success": await FeatureService.config_bool(FEATURE_KEY, "notify_success", True),
        "refund": await FeatureService.config_bool(FEATURE_KEY, "notify_refund", True),
    }


def _markup(state: dict) -> InlineKeyboardMarkup:
    enabled, success, refund = state["enabled"], state["success"], state["refund"]
    rows = [
        [
            InlineKeyboardButton(
                text=("🔴 إيقاف مباشر البوت" if enabled else "🟢 تشغيل مباشر البوت"),
                callback_data="live:master",
            )
        ],
        [
            InlineKeyboardButton(
                text=("✅ إشعارات الشراء المكتمل: مفعّل" if success else "⚪ إشعارات الشراء المكتمل: موقف"),
                callback_data="live:success",
            )
        ],
        [
            InlineKeyboardButton(
                text=("✅ إشعارات الاسترجاع/الفشل: مفعّلة" if refund else "⚪ إشعارات الاسترجاع/الفشل: موقفة"),
                callback_data="live:refund", style="danger",
            )
        ],
        [
            InlineKeyboardButton(text="🔔 تجربة إشعار شراء", callback_data="live:test_buy", style="primary"),
            InlineKeyboardButton(text="🔔 تجربة إشعار استرجاع", callback_data="live:test_refund", style="danger"),
        ],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text(state: dict) -> str:
    enabled, success, refund = state["enabled"], state["success"], state["refund"]
    return (
        "📡 <b>مباشر البوت</b>\n\n"
        "يصلك هنا فوراً (قناة الأدمن فقط) إشعار بكل حدث مالي حقيقي:\n"
        "• ✅ شراء <b>اكتمل وتفعّل</b> — خُصم من رصيد المستخدم.\n"
        "• ↩️ <b>استرجاع/فشل</b> — رجع الرصيد للمستخدم تلقائياً "
        "(رقم لم يتفعّل، فشل تنفيذ، منجز جزئياً).\n\n"
        "⚙️ الحالة الحالية:\n"
        f"{'🟢 مباشر البوت مفعّل' if enabled else '🔴 مباشر البوت موقف'}\n"
        f"{'✅' if success else '⚪'} إشعارات الشراء المكتمل\n"
        f"{'✅' if refund else '⚪'} إشعارات الاسترجاع/الفشل"
    )


async def _show(callback: CallbackQuery) -> None:
    state = await _current_state()
    await callback.message.edit_text(_text(state), reply_markup=_markup(state))


@router.callback_query(F.data == "admin:live_feed")
async def live_feed_home(callback: CallbackQuery, session, db_user):
    await callback.answer()
    await _show(callback)


async def _audit(session, db_user, key: str, old: str, new: str, desc: str) -> None:
    try:
        await AuditService.log(
            admin_id=db_user.id,
            action=AuditAction.UPDATE,
            entity_type="live_feed",
            entity_name=key,
            old_value=old,
            new_value=new,
            description=desc,
            session=session,
        )
    except Exception:
        pass


@router.callback_query(F.data == "live:master")
async def live_toggle_master(callback: CallbackQuery, session, db_user):
    current = await FeatureService.enabled(FEATURE_KEY, default=True)
    await FeatureService.set_enabled(session, FEATURE_KEY, not current)
    await _audit(
        session, db_user, "live_bot_feed", str(current), str(not current),
        f"{'إيقاف' if current else 'تشغيل'} مباشر البوت",
    )
    await callback.answer("✅ تم التحديث.")
    await _show(callback)


@router.callback_query(F.data == "live:success")
async def live_toggle_success(callback: CallbackQuery, session, db_user):
    current = await FeatureService.config_bool(FEATURE_KEY, "notify_success", True)
    await FeatureService.set_option(session, FEATURE_KEY, "notify_success", not current)
    await _audit(
        session, db_user, "notify_success", str(current), str(not current),
        "تغيير إشعارات الشراء المكتمل",
    )
    await callback.answer("✅ تم التحديث.")
    await _show(callback)


@router.callback_query(F.data == "live:refund")
async def live_toggle_refund(callback: CallbackQuery, session, db_user):
    current = await FeatureService.config_bool(FEATURE_KEY, "notify_refund", True)
    await FeatureService.set_option(session, FEATURE_KEY, "notify_refund", not current)
    await _audit(
        session, db_user, "notify_refund", str(current), str(not current),
        "تغيير إشعارات الاسترجاع/الفشل",
    )
    await callback.answer("✅ تم التحديث.")
    await _show(callback)


@router.callback_query(F.data == "live:test_buy")
async def live_test_buy(callback: CallbackQuery, bot):
    state = await _current_state()
    if not state["enabled"] or not state["success"]:
        await callback.answer("⚠️ إشعارات الشراء موقفة حالياً — فعّلها أولاً.", show_alert=True)
        return
    notifier = NotificationService(bot)
    await notifier.live_purchase_success(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
        item="تجربة — منتج اختباري",
        amount_usd="1.00",
        order_id=0,
    )
    await callback.answer("📨 أُرسل إشعار شراء تجريبي لقناة الأدمن.", show_alert=True)


@router.callback_query(F.data == "live:test_refund")
async def live_test_refund(callback: CallbackQuery, bot):
    state = await _current_state()
    if not state["enabled"] or not state["refund"]:
        await callback.answer("⚠️ إشعارات الاسترجاع موقفة حالياً — فعّلها أولاً.", show_alert=True)
        return
    notifier = NotificationService(bot)
    await notifier.live_refund(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
        item="تجربة — منتج اختباري",
        amount_usd="1.00",
        reason="تجربة",
        order_id=0,
    )
    await callback.answer("📨 أُرسل إشعار استرجاع تجريبي لقناة الأدمن.", show_alert=True)

"""
عرض معلومات مزودي الأرقام المبرمجين مسبقاً.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from database.models import ProviderName, ProviderStatus
from services.feature_service import FeatureService
from services.number_provider_stats_service import NumberProviderStatsService
from services.settings_service import SettingsService
from keyboards.admin import admin_back_kb
from filters.admin_filter import IsAdmin

router = Router(name="admin_providers")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:providers")
async def providers_info(callback: CallbackQuery, session):
    result = await session.execute(select(ProviderStatus))
    providers = result.scalars().all()

    threshold = await SettingsService.get_decimal("provider_low_balance_threshold")

    text = "🌐 <b>مزودو الأرقام</b>\n\n"
    for p in providers:
        status_emoji = "🟢" if p.is_online else "🔴"
        balance_text = f"{p.balance}$" if p.balance is not None else "غير معروف"

        low_warning = ""
        if p.balance is not None and p.is_online and p.balance < threshold:
            low_warning = " ⚠️ رصيد منخفض!"

        text += (
            f"{status_emoji} <b>{p.provider.value}</b>\n"
            f"💰 الرصيد: {balance_text}{low_warning}\n"
            f"🕐 آخر تحديث: "
            f"{p.last_checked_at.strftime('%Y-%m-%d %H:%M') if p.last_checked_at else '—'}\n"
        )
        if p.last_error:
            text += f"⚠️ آخر خطأ: {p.last_error[:100]}\n"
        text += "\n"

    text += f"🚨 حد التنبيه: {threshold}$"

    await callback.message.edit_text(text, reply_markup=admin_back_kb())


def _provider_quality_kb(stats: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔄 تحديث", callback_data="admin:number_provider_quality")],
        [InlineKeyboardButton(text="🧠 إعدادات التوجيه الذكي", callback_data="feat_item:smart_number_routing")],
    ]
    for row in stats:
        provider = row["provider"]
        if row.get("online"):
            rows.append([
                InlineKeyboardButton(
                    text=f"⏸ تعطيل مؤقت: {provider}",
                    callback_data=f"admin:num_provider_off:{provider}",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text=f"▶️ إعادة تفعيل: {provider}",
                    callback_data=f"admin:num_provider_on:{provider}",
                )
            ])
    rows.append([InlineKeyboardButton(text="⬅️ لوحة الإدارة", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "admin:number_provider_quality")
async def number_provider_quality(callback: CallbackQuery, session):
    stats = await NumberProviderStatsService.report(session, days=14)
    scores = await NumberProviderStatsService.smart_routing_scores(session)
    status_rows = (await session.execute(select(ProviderStatus))).scalars().all()
    status_map = {row.provider.value: row for row in status_rows}
    smart_enabled = await FeatureService.enabled("smart_number_routing")

    lines = [
        "📊 <b>جودة مزودي الأرقام</b>",
        "",
        f"🧠 التوجيه الذكي: {'🟢 مفعّل' if smart_enabled else '⚪ موقوف'}",
        "يعتمد الاختيار على السعر + نسبة النجاح + سرعة وصول الكود.",
        "",
    ]
    enriched = []
    for row in stats:
        provider = row["provider"]
        status = status_map.get(provider)
        online = bool(status and status.is_online)
        score = scores.get(ProviderName(provider), 1.0) if provider in ProviderName._value2member_map_ else 1.0
        avg = row["average_completion_seconds"]
        avg_text = f"{avg}s" if avg is not None else "—"
        icon = "🟢" if online else "🔴"
        if row["total"] and row["success_rate"] < 55:
            quality = "سيئ"
        elif row["total"] and row["success_rate"] < 75:
            quality = "متوسط"
        elif row["total"]:
            quality = "جيد"
        else:
            quality = "لا عينة"
        lines.append(
            f"{icon} <b>{provider}</b> — {quality}\n"
            f"   ✅ نجاح: <b>{row['success_rate']}%</b> | "
            f"طلبات: {row['total']} | فشل: {row['failed']} | معلّق: {row['pending']}\n"
            f"   ⚡ متوسط الوصول: <b>{avg_text}</b> | وزن التوجيه: <b>{score}</b>"
        )
        if status and status.last_error:
            lines.append(f"   ⚠️ آخر خطأ: {status.last_error[:90]}")
        enriched.append({**row, "online": online})

    lines.append("\n💡 عطّل المزود السيئ مؤقتاً إذا زادت الشكاوى أو الاسترجاعات.")
    await callback.message.edit_text("\n".join(lines), reply_markup=_provider_quality_kb(enriched))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:num_provider_off:"))
async def number_provider_disable(callback: CallbackQuery, session):
    provider_value = callback.data.rsplit(":", 1)[1]
    try:
        provider = ProviderName(provider_value)
    except ValueError:
        await callback.answer("مزود غير معروف.", show_alert=True)
        return
    status = await session.get(ProviderStatus, provider)
    if status is None:
        status = ProviderStatus(provider=provider)
        session.add(status)
    status.is_online = False
    status.last_error = "تعطيل يدوي مؤقت من لوحة جودة المزودين"
    await session.commit()
    await callback.answer("⏸ تم تعطيل المزود مؤقتاً.")
    await number_provider_quality(callback, session)


@router.callback_query(F.data.startswith("admin:num_provider_on:"))
async def number_provider_enable(callback: CallbackQuery, session):
    provider_value = callback.data.rsplit(":", 1)[1]
    try:
        provider = ProviderName(provider_value)
    except ValueError:
        await callback.answer("مزود غير معروف.", show_alert=True)
        return
    status = await session.get(ProviderStatus, provider)
    if status is None:
        status = ProviderStatus(provider=provider)
        session.add(status)
    status.is_online = True
    status.last_error = None
    await session.commit()
    await callback.answer("▶️ تم تفعيل المزود.")
    await number_provider_quality(callback, session)

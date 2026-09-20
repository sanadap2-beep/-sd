"""
عرض معلومات مزودي الأرقام المبرمجين مسبقاً + فحص تشخيصي حي.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from database.models import Country, NumberService, ProviderName, ProviderStatus
from providers.manager import provider_manager
from services.feature_service import FeatureService
from services.number_provider_stats_service import NumberProviderStatsService
from services.settings_service import SettingsService
from keyboards.admin import admin_back_kb
from filters.admin_filter import IsAdmin

router = Router(name="admin_providers")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

# المزود → حقل كود الدولة + حقل كود الخدمة
PROVIDER_CODE_FIELDS: dict[str, tuple[str, str]] = {
    "fivesim": ("fivesim_code", "fivesim_code"),
    "herosms": ("herosms_code", "herosms_code"),
    "sms_activate": ("sms_activate_code", "sms_activate_code"),
    "smshub": ("smshub_code", "smshub_code"),
    "smspool": ("smspool_code", "smspool_code"),
    "grizzly": ("grizzly_code", "grizzly_code"),
}

# عينة فحص ثابتة: (كود الدولة، كود الخدمة) لكل مزود
PROVIDER_SAMPLE: dict[str, tuple[str, str]] = {
    "fivesim": ("egypt", "whatsapp"),
    "herosms": ("21", "wa"),
    "sms_activate": ("21", "wa"),
    "smshub": ("21", "whatsapp"),
    "smspool": ("Egypt", "whatsapp"),
    "grizzly": ("21", "wa"),
}


def _providers_kb(configured: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🩺 فحص {value}", callback_data=f"admin:provider_check:{value}")]
        for value in configured
    ]
    rows.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

    configured = [p.value for p in provider_manager.get_available_providers()]
    await callback.message.edit_text(
        text, reply_markup=_providers_kb(configured) if configured else admin_back_kb()
    )


@router.callback_query(F.data.startswith("admin:provider_check:"))
async def provider_check(callback: CallbackQuery, session):
    """فحص تشخيصي حي لمزود: المفتاح + الرصيد + بوابة الصحة + عينة سعر.

    يكشف سبب السيرفر الفارغ بدقة (مفتاح ميت / رصيد صفر / فحص صحة
    أحمر / أكواد ناقصة / API غيّر شكله) بدل التخمين.
    """
    value = callback.data.rsplit(":", 1)[1]
    try:
        provider = ProviderName(value)
    except ValueError:
        await callback.answer("مزود غير معروف.", show_alert=True)
        return

    await callback.answer("⏳ جاري فحص المزود...")
    lines = [f"🩺 <b>فحص {value}</b>", ""]
    instance = provider_manager.get_instance(provider)

    # 1) المفتاح والمثيل
    if instance is None:
        lines.append("🔑 المفتاح: ⚠️ غير مضبوط — أضفه في <code>.env</code> وأعد التشغيل")
        lines.append("")
        lines.append("الخلاصة: ❌ المزود غير مهيأ إطلاقاً.")
        await callback.message.edit_text("\n".join(lines), reply_markup=admin_back_kb())
        return
    lines.append("🔑 المفتاح: ✅ مضبوط")

    # 2) الرصيد
    try:
        balance = await instance.get_balance()
        lines.append(f"💰 الرصيد: <b>{balance}$</b>" + (" ⚠️ فاضي!" if balance <= 0 else ""))
        balance_ok = balance > 0
    except Exception as e:
        lines.append(f"💰 الرصيد: ❌ فشل — <code>{str(e)[:150]}</code>")
        balance_ok = False

    # 3) بوابة فحص الصحة (تحجب الشراء عند session!)
    status = await session.get(ProviderStatus, provider)
    if status is None or status.last_checked_at is None:
        lines.append("🚦 فحص الصحة: ⚪ لم يُفحص بعد (لا يحجب)")
    elif status.is_online:
        lines.append("🚦 فحص الصحة: 🟢 سليم")
    else:
        lines.append(f"🚦 فحص الصحة: 🔴 يحجب الشراء! — <code>{(status.last_error or '')[:150]}</code>")

    # 4) تغطية الدول والخدمات
    fields = PROVIDER_CODE_FIELDS.get(value, (None, None))
    country_field, service_field = fields
    active_countries = 0
    if country_field:
        res = await session.execute(
            select(Country).where(
                Country.is_active.is_(True),
                getattr(Country, country_field).is_not(None),
            )
        )
        active_countries = len(res.scalars().all())
    lines.append(f"🌍 دول مفعلة بكوده: <b>{active_countries}</b>")

    svc = await session.execute(
        select(NumberService).where(NumberService.code == "whatsapp")
    )
    wa = svc.scalar_one_or_none()
    if wa is None:
        lines.append("💬 خدمة الواتساب: ❌ غير موجودة بقاعدة البيانات!")
        wa_code = None
    else:
        if not wa.is_active:
            lines.append("💬 خدمة الواتساب: ⚠️ معطلة — فعّلها من إدارة خدمات الأرقام")
        wa_code = getattr(wa, service_field, None) if service_field else None
    lines.append(
        f"🔢 كود الواتساب لدى المزود: <code>{wa_code or '— ناقص!'}</code>"
        if wa_code
        else "🔢 كود الواتساب لدى المزود: ⚠️ ناقص — السيرفر سيبقى فارغاً حتى يُضبط"
    )

    # 5) عينة سعر حية بلا كاش
    sample = PROVIDER_SAMPLE.get(value)
    if sample and wa_code:
        try:
            price = await instance.get_price(*sample)
            if price is not None and price > 0:
                lines.append(f"🧪 عينة حية ({sample[0]}/whatsapp): <b>{price}$</b> ✅")
            else:
                lines.append("🧪 عينة حية: ⚪ بلا مخزون (طبيعي إن نفدت الدولة)")
        except Exception as e:
            lines.append(f"🧪 عينة حية: ❌ فشل — <code>{str(e)[:150]}</code>")

    # الخلاصة
    lines.append("")
    if instance is not None and balance_ok and active_countries > 0 and wa_code:
        lines.append("الخلاصة: ✅ المزود سليم — إن بقي السيرفر فارغاً فالسبب مخزون لحظي.")
    else:
        lines.append("الخلاصة: ❌ عالج البنود المعلمة أعلاه ثم أعد السحب.")

    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=admin_back_kb())
    except Exception:
        await callback.message.answer("\n".join(lines), reply_markup=admin_back_kb())


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

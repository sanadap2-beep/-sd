"""
مركز العمليات — لوحة أدمن موحّدة للكائنات التي تنتجها الإضافات الجديدة.

مركز الإضافات يتحكم بالتفعيل والإعدادات، لكن بعض الميزات تنتج كائنات
تحتاج مراجعة بشرية أو إدارة مباشرة: قواعد الامتثال، المستأجرون، نقاط
الويبhook، عروض المزودين، الحجوزات، الغرف، الأسهم، واختبارات A/B.

بدون هذه اللوحة تبقى تلك الكائنات موجودة في القاعدة بلا way لإدارتها.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from database.models import (
    EscrowHold,
    ExperimentResult,
    JurisdictionRule,
    MarketListing,
    MarketListingStatus,
    ProviderBid,
    PurchaseRoom,
    RevenueShareToken,
    Tenant,
    WebhookDelivery,
    WebhookEndpoint,
)
from filters.admin_filter import IsAdmin
from services.audit_service import AuditAction, AuditService
from services.growth_channels_service import GrowthOptimizerService
from states.states import AdminOpsStates

from decimal import Decimal, InvalidOperation

router = Router(name="admin_ops")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ops")]]
    )


# ══════════════ القائمة ══════════════


@router.callback_query(F.data == "admin:ops")
async def ops_home(callback: CallbackQuery, session):
    async def count(model, *where):
        query = select(func.count(model.id))
        for clause in where:
            query = query.where(clause)
        return int((await session.execute(query)).scalar_one())

    rules = await count(JurisdictionRule)
    tenants = await count(Tenant)
    endpoints = await count(WebhookEndpoint)
    bids = await count(ProviderBid)
    holds = await count(EscrowHold, EscrowHold.status == "held")
    rooms = await count(PurchaseRoom, PurchaseRoom.status.in_(["open", "ready"]))
    shares = await count(RevenueShareToken, RevenueShareToken.status == "active")
    pending = await count(MarketListing, MarketListing.status == MarketListingStatus.PENDING_REVIEW)

    rows = [
        [InlineKeyboardButton(text=f"🌍 قواعد الامتثال ({rules})", callback_data="ops:juris")],
        [InlineKeyboardButton(text=f"🏢 المستأجرون ({tenants})", callback_data="ops:tenants")],
        [InlineKeyboardButton(text=f"🔗 نقاط الويبhook ({endpoints})", callback_data="ops:webhooks")],
        [InlineKeyboardButton(text=f"💰 عروض المزودين ({bids})", callback_data="ops:bids")],
        [InlineKeyboardButton(text=f"🏪 إعلانات السوق المعلقة ({pending})", callback_data="admin:marketplace")],
    ]
    rows.append(
        [
            InlineKeyboardButton(text=f"🔒 حجوزات معلّقة ({holds})", callback_data="ops:holds"),
            InlineKeyboardButton(text=f"👥 غرف نشطة ({rooms})", callback_data="ops:rooms"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=f"💹 أسهم نشطة ({shares})", callback_data="ops:shares"),
            InlineKeyboardButton(text="🧪 اختبارات A/B", callback_data="ops:experiments"),
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")])

    await callback.message.edit_text(
        "🛠 <b>مركز العمليات</b>\n\n"
        "إدارة الكائنات التي تنتجها الإضافات:\n"
        "قواعد الامتثال، المستأجرون، الويبhooks، عروض المزودين،\n"
        "الحجوزات، الغرف، الأسهم، واختبارات A/B.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ══════════════ قواعد الامتثال ══════════════


@router.callback_query(F.data == "ops:juris")
async def juris_list(callback: CallbackQuery, session):
    result = await session.execute(select(JurisdictionRule).order_by(JurisdictionRule.country_code))
    rules = list(result.scalars().all())
    if not rules:
        text = "🌍 لا قواعد امتثال بعد.\n\nأضف قاعدة لتحديد ما يُمنع بيعه في دولة."
    else:
        lines = []
        for rule in rules:
            status = "🟢" if rule.is_active else "⚪"
            kyc = f" · KYC≥{rule.requires_kyc_tier}" if rule.requires_kyc_tier else ""
            lines.append(
                f"{status} #{rule.id} <b>{rule.country_code}</b> — "
                f"ممنوع: {rule.blocked_services or '—'}{kyc}"
            )
        text = "🌍 <b>قواعد الامتثال</b>\n\n" + "\n".join(lines)
    rows = [
        [InlineKeyboardButton(text="➕ قاعدة جديدة", callback_data="ops:jurisnew")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ops")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "ops:jurisnew")
async def juris_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminOpsStates.waiting_jurisdiction)
    await callback.message.answer(
        "🌍 <b>قاعدة امتثال جديدة</b>\n\n"
        "أرسل: <code>الدولة الخدمات_الممنوعة مستوى_KYC السبب</code>\n"
        "مثال: <code>DE tg,wa 1 غير متاح في ألمانيا</code>\n\n"
        "استخدم <code>-</code> لأي حقل تريد تركه فارغاً."
    )
    await callback.answer()


@router.message(AdminOpsStates.waiting_jurisdiction)
async def juris_submit(message: Message, state: FSMContext, session, db_user):
    parts = (message.text or "").split(maxsplit=3)
    await state.clear()
    if len(parts) < 4:
        return await message.answer("⚠️ الصيغة: <code>الدولة الخدمات KYC السبب</code>")
    country, blocked, kyc_raw, reason = parts
    try:
        kyc = 0 if kyc_raw == "-" else int(kyc_raw)
    except ValueError:
        return await message.answer("⚠️ مستوى KYC يجب أن يكون رقماً أو <code>-</code>.")

    rule = JurisdictionRule(
        country_code=country.upper()[:8],
        blocked_services=None if blocked == "-" else blocked,
        requires_kyc_tier=max(0, kyc),
        reason=None if reason == "-" else reason[:255],
        is_active=True,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="jurisdiction_rule",
        entity_id=rule.id,
        entity_name=rule.country_code,
        new_value=f"blocked={rule.blocked_services} kyc={rule.requires_kyc_tier}",
        description="إضافة قاعدة امتثال",
        session=session,
    )
    await message.answer(f"✅ أُضيفت القاعدة #{rule.id} لـ {rule.country_code}")


@router.callback_query(F.data.startswith("ops:juristoggle:"))
async def juris_toggle(callback: CallbackQuery, session, db_user):
    rule = await session.get(JurisdictionRule, int(callback.data.split(":")[2]))
    if rule is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    rule.is_active = not rule.is_active
    await session.commit()
    await AuditService.log_toggle(
        admin_id=db_user.id,
        entity_type="jurisdiction_rule",
        entity_id=rule.id,
        entity_name=rule.country_code,
        new_status=rule.is_active,
        session=session,
    )
    await callback.answer("🟢 مفعّلة" if rule.is_active else "⚪ موقوفة")
    await juris_list(callback, session)


# ══════════════ المستأجرون ══════════════


@router.callback_query(F.data == "ops:tenants")
async def tenants_list(callback: CallbackQuery, session):
    result = await session.execute(select(Tenant).order_by(Tenant.id))
    tenants = list(result.scalars().all())
    if not tenants:
        text = "🏢 لا مستأجرين بعد."
    else:
        lines = [
            f"{'🟢' if t.is_active else '⚪'} #{t.id} <b>{t.brand_name}</b> "
            f"(@{t.bot_username or '-'}) — عمولة {t.commission_percent}%"
            for t in tenants
        ]
        text = "🏢 <b>المستأجرون</b>\n\n" + "\n".join(lines)
    rows = [
        [InlineKeyboardButton(text="➕ مستأجر جديد", callback_data="ops:tenantnew")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ops")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "ops:tenantnew")
async def tenant_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminOpsStates.waiting_tenant)
    await callback.message.answer(
        "🏢 <b>مستأجر جديد</b>\n\n"
        "أرسل: <code>المعرف اسم_العلامة يوزر_البوت العمولة</code>\n"
        "مثال: <code>myshop متجري @myshop_bot 7</code>"
    )
    await callback.answer()


@router.message(AdminOpsStates.waiting_tenant)
async def tenant_submit(message: Message, state: FSMContext, session, db_user):
    from services.platform_service import PlatformError, WhiteLabelService

    parts = (message.text or "").split(maxsplit=3)
    await state.clear()
    if len(parts) < 4:
        return await message.answer("⚠️ الصيغة: <code>المعرف الاسم البوت العمولة</code>")
    slug, brand, bot_username, commission_raw = parts
    try:
        commission = Decimal(commission_raw)
    except InvalidOperation:
        return await message.answer("⚠️ العمولة يجب أن تكون رقماً.")
    try:
        result = await WhiteLabelService.create(session, slug, brand, bot_username, commission)
    except PlatformError as exc:
        return await message.answer(f"⚠️ {exc}")
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="tenant",
        entity_id=result["tenant_id"],
        entity_name=brand,
        description="إنشاء مستأجر",
        session=session,
    )
    await message.answer(f"✅ أُنشئ المستأجر #{result['tenant_id']} ({slug})")


# ══════════════ نقاط الويبhook ══════════════


@router.callback_query(F.data == "ops:webhooks")
async def webhooks_list(callback: CallbackQuery, session):
    result = await session.execute(select(WebhookEndpoint).order_by(WebhookEndpoint.id))
    endpoints = list(result.scalars().all())
    if not endpoints:
        text = "🔗 لا نقاط ويبhook مسجلة."
    else:
        lines = []
        for endpoint in endpoints:
            delivered = int(
                (
                    await session.execute(
                        select(func.count(WebhookDelivery.id)).where(
                            WebhookDelivery.endpoint_id == endpoint.id,
                            WebhookDelivery.succeeded.is_(True),
                        )
                    )
                ).scalar_one()
            )
            failed = int(
                (
                    await session.execute(
                        select(func.count(WebhookDelivery.id)).where(
                            WebhookDelivery.endpoint_id == endpoint.id,
                            WebhookDelivery.succeeded.is_(False),
                        )
                    )
                ).scalar_one()
            )
            lines.append(
                f"{'🟢' if endpoint.is_active else '⚪'} #{endpoint.id} "
                f"<code>{endpoint.url[:40]}</code>\n"
                f"    ✅ {delivered} · ❌ {failed} · صلاحيات: {endpoint.scopes or '—'}"
            )
        text = "🔗 <b>نقاط الويبhook</b>\n\n" + "\n".join(lines)
    rows = [
        [InlineKeyboardButton(text="🗑 حذف كل النقاط", callback_data="ops:webhookclear")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ops")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "ops:webhookclear")
async def webhooks_clear(callback: CallbackQuery, session, db_user):
    result = await session.execute(delete(WebhookDelivery))
    await session.execute(delete(WebhookEndpoint))
    await session.commit()
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.DELETE,
        entity_type="webhook_endpoints",
        description=f"حذف كل نقاط الويبhook ({result.rowcount} تسليم)",
        session=session,
    )
    await callback.answer("🗑 حُذفت.")
    await webhooks_list(callback, session)


# ══════════════ عروض المزودين ══════════════


@router.callback_query(F.data == "ops:bids")
async def bids_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(ProviderBid).order_by(ProviderBid.service_code, ProviderBid.price_usd).limit(30)
    )
    bids = list(result.scalars().all())
    if not bids:
        text = "💰 لا عروض مزايدة."
    else:
        lines = []
        for bid in bids:
            expired = bid.expires_at and bid.expires_at < __import__("datetime").datetime.utcnow()
            mark = "⌛" if expired else "🟢"
            lines.append(
                f"{mark} {bid.service_code}/{bid.country_code} — "
                f"<b>{bid.price_usd}$</b> (مزود #{bid.provider_id})"
            )
        text = "💰 <b>عروض المزايدة</b>\n\n" + "\n".join(lines)
    rows = [
        [InlineKeyboardButton(text="🧹 تنظيف المنتهية", callback_data="ops:bidsclean")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ops")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "ops:bidsclean")
async def bids_clean(callback: CallbackQuery, session):
    from services.platform_service import ProviderBiddingService

    removed = await ProviderBiddingService.cleanup_expired(session)
    await callback.answer(f"🧹 نُظفت {removed} عروض منتهية.")
    await bids_list(callback, session)


# ══════════════ الحجوزات ══════════════


@router.callback_query(F.data == "ops:holds")
async def holds_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(EscrowHold).where(EscrowHold.status == "held").order_by(EscrowHold.id.desc()).limit(20)
    )
    holds = list(result.scalars().all())
    if not holds:
        text = "🔒 لا حجوزات معلّقة."
    else:
        total = sum((h.amount_usd for h in holds), Decimal("0"))
        lines = [
            f"• #{h.id} — <b>{h.amount_usd}$</b> من مستخدم {h.payer_id}\n"
            f"   {h.purpose or '—'}"
            for h in holds
        ]
        text = f"🔒 <b>حجوزات معلّقة</b> ({len(holds)})\n\nإجمالي المحجوز: <b>{total}$</b>\n\n" + "\n".join(lines)
    rows = [
        [InlineKeyboardButton(text="⏰ إفراج عن القديمة (72س)", callback_data="ops:holdsexpire")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ops")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "ops:holdsexpire")
async def holds_expire(callback: CallbackQuery, session):
    from services.marketplace_ext_service import EscrowService

    count = await EscrowService.expire_stale(session, 72)
    await callback.answer(f"⏰ أُفرج عن {count} حجوزات قديمة.")
    await holds_list(callback, session)


# ══════════════ الغرف ══════════════


@router.callback_query(F.data == "ops:rooms")
async def rooms_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(PurchaseRoom).where(PurchaseRoom.status.in_(["open", "ready"])).order_by(PurchaseRoom.id.desc()).limit(20)
    )
    rooms = list(result.scalars().all())
    if not rooms:
        text = "👥 لا غرف نشطة."
    else:
        lines = [
            f"• #{r.id} — منتج {r.product_id} · {r.members} عضو · "
            f"هدف {r.target_quantity} · {'✅ جاهزة' if r.status == 'ready' else '⏳ مفتوحة'}"
            for r in rooms
        ]
        text = "👥 <b>الغرف النشطة</b>\n\n" + "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=_back())
    await callback.answer()


# ══════════════ الأسهم ══════════════


@router.callback_query(F.data == "ops:shares")
async def shares_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(RevenueShareToken).where(RevenueShareToken.status == "active").limit(20)
    )
    tokens = list(result.scalars().all())
    if not tokens:
        text = "💹 لا أسهم نشطة."
    else:
        lines = [
            f"• #{t.id} — {t.share_percent}% من مستخدم {t.issuer_id} "
            f"بـ{t.price_usd}$ · حامل: {t.holder_id or 'لم يُشترَ'}"
            for t in tokens
        ]
        text = "💹 <b>الأسهم النشطة</b>\n\n" + "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=_back())
    await callback.answer()


# ══════════════ اختبارات A/B ══════════════


@router.callback_query(F.data == "ops:experiments")
async def experiments_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(ExperimentResult.experiment, func.count(ExperimentResult.id))
        .group_by(ExperimentResult.experiment)
        .order_by(func.count(ExperimentResult.id).desc())
        .limit(20)
    )
    rows_data = list(result.all())
    if not rows_data:
        text = "🧪 لا اختبارات مسجلة."
    else:
        lines = [f"• <b>{name}</b> — {count} عينة" for name, count in rows_data]
        text = "🧪 <b>اختبارات A/B</b>\n\n" + "\n".join(lines)
    rows = [[InlineKeyboardButton(text=f"📊 نتيجة: {name}", callback_data=f"ops:exp:{name}")]
            for name, _ in rows_data[:10]]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:ops")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("ops:exp:"))
async def experiment_detail(callback: CallbackQuery, session):
    name = callback.data.split(":", 2)[2]
    winner = await GrowthOptimizerService.winner(session, name, min_samples=30)
    if winner is None:
        await callback.message.edit_text(
            f"📊 <b>{name}</b>\n\nالعيّنات غير كافية بعد لإعلان فائز (الحد 30 لكل متغير).",
            reply_markup=_back(),
        )
        await callback.answer()
        return
    lines = [
        f"{'🏆' if row['variant'] == winner['winner']['variant'] else '  '} "
        f"<b>{row['variant']}</b> — {row['conversion_rate']}% ({row['samples']} عينة)"
        for row in winner["all"]
    ]
    await callback.message.edit_text(
        f"📊 <b>{name}</b>\n\n" + "\n".join(lines) + "\n\n🏆 الفائز يُعتمد تلقائياً.",
        reply_markup=_back(),
    )
    await callback.answer()

"""Merchant panel in the main bot: own and operate white-label sub-bots.

الدخول: الأمر /mystore (يعمل لكل المستخدمين — أي مستخدم قد يصبح تاجراً).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, func, select

from database.models import (
    Category,
    DepositRequest,
    DepositStatus,
    SupportTicket,
    SupportTicketStatus,
    Tenant,
    TenantCatalogSelection,
    TenantOrderMap,
    TransactionType,
    User,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.html_guard import esc
from services.tenant_service import SUBSCRIPTION_USD, TenantError, TenantService
from states.states import MerchantStates

router = Router(name="merchant")


def _back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 متاجري", callback_data="mc:home")]]
    )


async def _my_tenants(session, owner_id: int) -> list[Tenant]:
    return await TenantService.merchant_tenants(session, owner_id)


@router.message(F.text.startswith("/mystore"))
async def merchant_entry(message: Message, session, db_user: User, state: FSMContext):
    await state.clear()
    await _render_home(message, session, db_user)


async def _render_home(message: Message, session, db_user: User):
    tenants = await _my_tenants(session, db_user.id)
    lines = ["🏪 <b>متاجري الفرعية</b>\n"]
    rows: list[list[InlineKeyboardButton]] = []
    for t in tenants:
        wallet = await TenantService.wallet(session, t.id)
        status = "🟢" if TenantService.is_usable(t) else "🔴"
        lines.append(
            f"{status} <b>{esc(t.brand_name)}</b> (@{esc(t.bot_username or '-')})\n"
            f"   💼 المحفظة: {wallet.balance}$ · 📅 الاشتراك: {t.subscription_status}"
        )
        rows.append(
            [InlineKeyboardButton(text=f"⚙️ {t.brand_name[:20]}", callback_data=f"mc:view:{t.id}")]
        )
    if not tenants:
        lines.append("لا متاجر بعد — أنشئ أول بوت فرعي بضغطة واحدة.")
    rows.append([InlineKeyboardButton(text="➕ إنشاء متجر جديد", callback_data="mc:new")])
    try:
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass


@router.callback_query(F.data == "mc:home")
async def merchant_home_cb(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    await state.clear()
    await callback.answer()
    tenants = await _my_tenants(session, db_user.id)
    lines = ["🏪 <b>متاجري الفرعية</b>\n"]
    rows: list[list[InlineKeyboardButton]] = []
    for t in tenants:
        wallet = await TenantService.wallet(session, t.id)
        status = "🟢" if TenantService.is_usable(t) else "🔴"
        lines.append(
            f"{status} <b>{esc(t.brand_name)}</b>\n"
            f"   💼 المحفظة: {wallet.balance}$ · 📅 الاشتراك: {t.subscription_status}"
        )
        rows.append(
            [InlineKeyboardButton(text=f"⚙️ {t.brand_name[:20]}", callback_data=f"mc:view:{t.id}")]
        )
    if not tenants:
        lines.append("لا متاجر بعد.")
    rows.append([InlineKeyboardButton(text="➕ إنشاء متجر جديد", callback_data="mc:new")])
    try:
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
    except Exception:
        pass


async def _owned(session, owner_id: int, tenant_id: int) -> Tenant | None:
    t = await session.get(Tenant, tenant_id)
    if t is None or t.owner_user_id != owner_id:
        return None
    return t


@router.callback_query(F.data.startswith("mc:view:"))
async def merchant_view(callback: CallbackQuery, session, db_user: User):
    t = await _owned(session, db_user.id, int(callback.data.split(":")[2]))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    wallet = await TenantService.wallet(session, t.id)
    sales = (
        await session.execute(
            select(func.count(TenantOrderMap.id)).where(TenantOrderMap.tenant_id == t.id)
        )
    ).scalar_one()
    pending_tickets = (
        await session.execute(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.tenant_id == t.id,
                SupportTicket.status.in_([SupportTicketStatus.OPEN, SupportTicketStatus.IN_PROGRESS]),
            )
        )
    ).scalar_one()
    pending_deps = (
        await session.execute(
            select(func.count(DepositRequest.id)).where(
                DepositRequest.tenant_id == t.id,
                DepositRequest.status == DepositStatus.PENDING,
            )
        )
    ).scalar_one()
    status = "🟢 يعمل" if TenantService.is_usable(t) else f"🔴 موقوف ({esc(t.suspended_reason or t.subscription_status)})"
    text = (
        f"🏪 <b>{esc(t.brand_name)}</b> (@{esc(t.bot_username or '-')})\n\n"
        f"الحالة: {status}\n"
        f"💼 المحفظة: <b>{wallet.balance}$</b> (رُبح: {wallet.total_earned_usd}$)\n"
        f"📊 الطلبات: {sales} · 🎫 تذاكر مفتوحة: {pending_tickets} · 💳 شحن معلق: {pending_deps}\n"
        f"💹 الهامش: {t.margin_percent}% · الكتالوج: {t.catalog_mode}\n"
        f"📅 الاشتراك حتى: {t.subscription_due_at.strftime('%Y-%m-%d') if t.subscription_due_at else '—'}"
    )
    rows = [
        [
            InlineKeyboardButton(text="💼 تمويل المحفظة", callback_data=f"mc:fund:{t.id}"),
            InlineKeyboardButton(text="💹 الهامش", callback_data=f"mc:margin:{t.id}"),
        ],
        [
            InlineKeyboardButton(text="🗂 الكتالوج", callback_data=f"mc:catalog:{t.id}"),
            InlineKeyboardButton(text="🎫 التذاكر", callback_data=f"mc:tickets:{t.id}:0"),
        ],
        [
            InlineKeyboardButton(text="💳 طلبات الشحن", callback_data=f"mc:deps:{t.id}:0"),
            InlineKeyboardButton(text="📊 تقرير", callback_data=f"mc:report:{t.id}"),
        ],
        [
            InlineKeyboardButton(text="💳 دفع الاشتراك الآن ($8)", callback_data=f"mc:subpay:{t.id}"),
            InlineKeyboardButton(text="⏸ إيقاف/تفعيل", callback_data=f"mc:toggle:{t.id}"),
        ],
        [InlineKeyboardButton(text="🔙 متاجري", callback_data="mc:home")],
    ]
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass


# ─────────── إنشاء متجر ───────────

@router.callback_query(F.data == "mc:new")
async def merchant_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(MerchantStates.waiting_token)
    await callback.message.answer(
        "➕ <b>إنشاء بوت فرعي جديد</b>\n\n"
        "1) أنشئ بوتاً من @BotFather وانسخ التوكن.\n"
        "2) أرسل التوكن هنا (يُخزن مشفراً ولا يظهر لأحد).\n\n"
        "⚠️ ضع صورة البروفايل يدوياً من BotFather (تيليجرام لا يسمح آلياً)."
    )
    await callback.answer()


@router.message(MerchantStates.waiting_token)
async def merchant_token_received(message: Message, state: FSMContext, session):
    token = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass
    # تحقق فوري من التوكن عبر getMe قبل التخزين
    try:
        from aiogram import Bot as _Bot

        probe = _Bot(token=token)
        try:
            me = await probe.get_me()
            bot_username = (me.username or "").lower()
        finally:
            try:
                await probe.session.close()
            except Exception:
                pass
    except Exception:
        await message.answer("⚠️ التوكن غير صالح أو البوت محذوف — تحقق وأعد الإرسال.")
        return
    if not bot_username:
        await message.answer("⚠️ تعذر قراءة معرف البوت — أعد المحاولة.")
        return
    await state.update_data(mc_token=token, mc_username=bot_username)
    await state.set_state(MerchantStates.waiting_brand)
    await message.answer(
        f"✅ البوت صالح: @{esc(bot_username)}\n\nالآن أرسل <b>اسم متجرك التجاري</b>:"
    )


@router.message(MerchantStates.waiting_brand)
async def merchant_brand_received(message: Message, state: FSMContext):
    brand = (message.text or "").strip()
    if len(brand) < 2 or len(brand) > 64:
        await message.answer("⚠️ الاسم بين 2 و 64 حرفاً.")
        return
    await state.update_data(mc_brand=brand)
    await state.set_state(MerchantStates.waiting_margin)
    await message.answer(
        "💹 أرسل <b>هامش الربح %</b> فوق أسعارنا (مثال: 20 تعني أسعارك = أسعارنا + 20%):"
    )


@router.message(MerchantStates.waiting_margin)
async def merchant_margin_received(message: Message, state: FSMContext, session, db_user: User):
    try:
        margin = Decimal((message.text or "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("⚠️ أدخل رقماً (مثال: 20).")
        return
    if margin < 0 or margin > 500:
        await message.answer("⚠️ الهامش بين 0 و 500.")
        return
    data = await state.get_data()
    # وضع تعديل هامش متجر قائم
    if data.get("mc_margin_tenant"):
        t = await _owned(session, db_user.id, int(data["mc_margin_tenant"]))
        await state.clear()
        if t is None:
            await message.answer("⚠️ المتجر غير موجود.")
            return
        t.margin_percent = margin
        await session.commit()
        await message.answer(f"✅ أصبح الهامش <b>{margin}%</b>.")
        return
    await state.clear()
    try:
        tenant = await TenantService.create(
            session,
            owner_user_id=db_user.id,
            token=data["mc_token"],
            bot_username=data["mc_username"],
            brand_name=data["mc_brand"],
            margin_percent=margin,
        )
    except TenantError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    # تسجيل webhook + دفع الهوية
    ok_hook = await TenantService.register_webhook(session, tenant)
    ok_brand = await TenantService.push_branding(
        tenant, name=data["mc_brand"], description=f"{data['mc_brand']} — تسوق بسهولة وأمان."
    )
    try:
        from services.tenant_runtime import drop_tenant_bot

        drop_tenant_bot(tenant.id)
    except Exception:
        pass
    await message.answer(
        "🎉 <b>تم إنشاء متجرك!</b>\n\n"
        f"🏪 {esc(data['mc_brand'])} (@{esc(data['mc_username'])})\n"
        f"💹 الهامش: {margin}%\n"
        f"🔌 الربط: {'✅ يعمل' if ok_hook else '⚠️ تعذر — تحقق من PUBLIC_BASE_URL وأعد التفعيل'}\n"
        f"🎨 الهوية: {'✅ دُفعت' if ok_brand else '⚠️ راجع التوكن'}\n\n"
        "الخطوة التالية: موّل محفظة المتجر من زر التمويل حتى تبدأ طلبات زبائنك."
    )


# ─────────── تمويل المحفظة من رصيد التاجر ───────────

@router.callback_query(F.data.startswith("mc:fund:"))
async def merchant_fund_start(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    t = await _owned(session, db_user.id, int(callback.data.split(":")[2]))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    await state.update_data(mc_fund_tenant=t.id)
    await state.set_state(MerchantStates.waiting_wallet_amount)
    await callback.message.answer(
        f"💼 رصيدك الشخصي: <b>{db_user.balance:.4f}$</b>\n"
        "أرسل مبلغ التمويل لمحفظة المتجر:"
    )
    await callback.answer()


@router.message(MerchantStates.waiting_wallet_amount)
async def merchant_fund_received(message: Message, state: FSMContext, session, db_user: User):
    data = await state.get_data()
    await state.clear()
    try:
        amount = Decimal((message.text or "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("⚠️ مبلغ غير صالح.")
        return
    if amount <= 0 or amount > 100000:
        await message.answer("⚠️ مبلغ غير صالح.")
        return
    t = await _owned(session, db_user.id, int(data.get("mc_fund_tenant") or 0))
    if t is None:
        await message.answer("⚠️ المتجر غير موجود.")
        return
    try:
        await BalanceService.deduct_balance(
            session,
            db_user.id,
            amount,
            TransactionType.PURCHASE,
            description=f"تمويل محفظة متجر {t.brand_name} #{t.id}",
            related_table="tenants",
            related_id=t.id,
        )
    except InsufficientBalanceError:
        await message.answer("⚠️ رصيدك الشخصي غير كافٍ — اشحن أولاً.")
        return
    try:
        wallet = await TenantService.fund(session, t.id, amount, f"تمويل من {db_user.telegram_id}")
    except TenantError as exc:
        # رد المبلغ للرصيد الشخصي — لا ضياع
        try:
            await BalanceService.add_balance(
                session, db_user.id, amount, TransactionType.REFUND,
                description="إرجاع تمويل متجر فاشل",
                payment_reference=f"tenant_fund_fail:{t.id}:{db_user.id}:{amount}",
            )
        except Exception:
            pass
        await message.answer(f"⚠️ {exc}")
        return
    await message.answer(f"✅ مُوّلت المحفظة بـ <b>{amount}$</b> — المتاح الآن: <b>{wallet.balance}$</b>.")


# ─────────── الهامش والكتالوج ───────────

@router.callback_query(F.data.startswith("mc:margin:"))
async def merchant_margin_view(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    t = await _owned(session, db_user.id, int(callback.data.split(":")[2]))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    await state.update_data(mc_margin_tenant=t.id)
    await state.set_state(MerchantStates.waiting_margin)
    # نعيد استخدام نفس الحالة: نميز عبر mc_margin_tenant بدل إنشاء متجر
    await callback.message.answer(
        f"💹 الهامش العام الحالي: <b>{t.margin_percent}%</b>\nأرسل الهامش الجديد أو /mystore للإلغاء:"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mc:catalog:"))
async def merchant_catalog(callback: CallbackQuery, session, db_user: User):
    t = await _owned(session, db_user.id, int(callback.data.split(":")[2]))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    mode = t.catalog_mode or "full"
    rows = [
        [InlineKeyboardButton(
            text=f"{'✅' if mode == 'full' else '⬜'} الكتالوج الكامل",
            callback_data=f"mc:catmode:{t.id}:full",
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if mode == 'selective' else '⬜'} انتقاء يدوي",
            callback_data=f"mc:catmode:{t.id}:selective",
        )],
    ]
    if mode == "selective":
        cats = (await session.execute(select(Category).order_by(Category.id))).scalars().all()
        sel = (
            await session.execute(
                select(TenantCatalogSelection.item_id).where(
                    TenantCatalogSelection.tenant_id == t.id,
                    TenantCatalogSelection.item_type == "category",
                )
            )
        ).scalars().all()
        picked = set(sel)
        for c in cats[:20]:
            mark = "✅" if c.id in picked else "⬜"
            rows.append(
                [InlineKeyboardButton(
                    text=f"{mark} {c.name_ar[:24]}", callback_data=f"mc:catpick:{t.id}:{c.id}"
                )]
            )
    rows.append([InlineKeyboardButton(text="🔙 المتجر", callback_data=f"mc:view:{t.id}")])
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"🗂 <b>كتالوج {esc(t.brand_name)}</b>\n\n"
            "الكامل = مرآة طبق الأصل لمتجرنا. الانتقاء = الأقسام المحددة فقط.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("mc:catmode:"))
async def merchant_catmode(callback: CallbackQuery, session, db_user: User):
    _, _, tid, mode = callback.data.split(":")
    t = await _owned(session, db_user.id, int(tid))
    if t is None or mode not in ("full", "selective"):
        await callback.answer("غير صالح.", show_alert=True)
        return
    t.catalog_mode = mode
    await session.commit()
    await callback.answer("✅ حُفظ.")
    # إعادة العرض
    callback.data = f"mc:catalog:{t.id}"
    await merchant_catalog(callback, session, db_user)


@router.callback_query(F.data.startswith("mc:catpick:"))
async def merchant_catpick(callback: CallbackQuery, session, db_user: User):
    _, _, tid, cid = callback.data.split(":")
    t = await _owned(session, db_user.id, int(tid))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    if (t.catalog_mode or "full") != "selective":
        await callback.answer("فعّل الانتقاء اليدوي أولاً.", show_alert=True)
        return
    existing = (
        await session.execute(
            select(TenantCatalogSelection).where(
                TenantCatalogSelection.tenant_id == t.id,
                TenantCatalogSelection.item_type == "category",
                TenantCatalogSelection.item_id == int(cid),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            TenantCatalogSelection(tenant_id=t.id, item_type="category", item_id=int(cid))
        )
    else:
        await session.delete(existing)
    await session.commit()
    await callback.answer("✅ حُفظ.")
    callback.data = f"mc:catalog:{t.id}"
    await merchant_catalog(callback, session, db_user)


# ─────────── التذاكر (وارد التاجر) ───────────

@router.callback_query(F.data.startswith("mc:tickets:"))
async def merchant_tickets(callback: CallbackQuery, session, db_user: User):
    _, _, tid, page = callback.data.split(":")
    t = await _owned(session, db_user.id, int(tid))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    page = int(page)
    result = await session.execute(
        select(SupportTicket)
        .where(SupportTicket.tenant_id == t.id)
        .order_by(desc(SupportTicket.id))
        .limit(8)
        .offset(page * 8)
    )
    tickets = list(result.scalars().all())
    if not tickets:
        await callback.answer("لا تذاكر.", show_alert=True)
        return
    rows = []
    for tk in tickets:
        st = "🟢" if tk.status == SupportTicketStatus.OPEN else "🔄" if tk.status == SupportTicketStatus.IN_PROGRESS else "✅"
        rows.append(
            [InlineKeyboardButton(text=f"{st} #{tk.id} {(tk.subject or '')[:24]}", callback_data=f"mc:ticket:{tk.id}")]
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"mc:tickets:{t.id}:{page - 1}"))
    nav.append(InlineKeyboardButton(text="▶️", callback_data=f"mc:tickets:{t.id}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 المتجر", callback_data=f"mc:view:{t.id}")])
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"🎫 <b>تذاكر {esc(t.brand_name)}</b> (الأحدث أولاً):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("mc:ticket:"))
async def merchant_ticket_view(callback: CallbackQuery, session, db_user: User):
    tk_id = int(callback.data.split(":")[2])
    tk = await session.get(SupportTicket, tk_id)
    if tk is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    t = await _owned(session, db_user.id, tk.tenant_id or 0)
    if t is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    user = await session.get(User, tk.user_id)
    text = (
        f"🎫 <b>تذكرة #{tk.id}</b> — {esc(t.brand_name)}\n\n"
        f"👤 الزبون: <code>{user.telegram_id if user else '—'}</code>\n"
        f"📝 {esc(tk.subject or '')}\n\n{esc(tk.message or '')}"
    )
    if tk.admin_reply:
        text += f"\n\n<b>ردك:</b>\n{esc(tk.admin_reply)}"
    rows = [
        [InlineKeyboardButton(text="✉️ رد", callback_data=f"mc:reply:{tk.id}")],
        [InlineKeyboardButton(text="✅ حل", callback_data=f"mc:resolve:{tk.id}")],
        [InlineKeyboardButton(text="🔙 التذاكر", callback_data=f"mc:tickets:{t.id}:0")],
    ]
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass


@router.callback_query(F.data.startswith("mc:reply:"))
async def merchant_reply_start(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    tk = await session.get(SupportTicket, int(callback.data.split(":")[2]))
    if tk is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    t = await _owned(session, db_user.id, tk.tenant_id or 0)
    if t is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    await state.update_data(mc_reply_ticket=tk.id)
    await state.set_state(MerchantStates.waiting_ticket_reply)
    await callback.message.answer(f"✉️ أرسل ردك على التذكرة <b>#{tk.id}</b>:")
    await callback.answer()


@router.message(MerchantStates.waiting_ticket_reply)
async def merchant_reply_received(message: Message, state: FSMContext, session, db_user: User):
    data = await state.get_data()
    await state.clear()
    reply = (message.text or "").strip()
    if len(reply) < 2:
        await message.answer("⚠️ الرد قصير جداً.")
        return
    tk = await session.get(SupportTicket, int(data.get("mc_reply_ticket") or 0))
    if tk is None:
        await message.answer("⚠️ التذكرة غير موجودة.")
        return
    t = await _owned(session, db_user.id, tk.tenant_id or 0)
    if t is None:
        await message.answer("⚠️ غير مصرح.")
        return
    tk.admin_reply = reply
    tk.admin_id = db_user.id
    tk.status = SupportTicketStatus.IN_PROGRESS
    await session.commit()
    # إيصال الرد لزبون الفرعي عبر بوته (بهوية المتجر)
    try:
        from services.tenant_runtime import get_tenant_bot

        sub_bot = get_tenant_bot(t)
        user = await session.get(User, tk.user_id)
        if user:
            await sub_bot.send_message(
                user.telegram_id,
                f"✉️ <b>رد جديد من {esc(t.brand_name)}</b>\n\n{esc(reply)}",
            )
        ok = True
    except Exception:
        ok = False
    await message.answer(
        f"✅ تم إرسال الرد لزبونك.{'' if ok else ' (تعذر الإشعار الفوري — سيقرأه من التذكرة)'}"
    )


@router.callback_query(F.data.startswith("mc:resolve:"))
async def merchant_resolve(callback: CallbackQuery, session, db_user: User):
    tk = await session.get(SupportTicket, int(callback.data.split(":")[2]))
    if tk is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    t = await _owned(session, db_user.id, tk.tenant_id or 0)
    if t is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    tk.status = SupportTicketStatus.RESOLVED
    tk.resolved_at = datetime.utcnow()
    await session.commit()
    await callback.answer("✅ أُغلقت.")
    callback.data = f"mc:ticket:{tk.id}"
    await merchant_ticket_view(callback, session, db_user)


# ─────────── طلبات الشحن (اعتماد التاجر) ───────────

@router.callback_query(F.data.startswith("mc:deps:"))
async def merchant_deps(callback: CallbackQuery, session, db_user: User):
    _, _, tid, page = callback.data.split(":")
    t = await _owned(session, db_user.id, int(tid))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    page = int(page)
    result = await session.execute(
        select(DepositRequest)
        .where(
            DepositRequest.tenant_id == t.id,
            DepositRequest.status == DepositStatus.PENDING,
        )
        .order_by(desc(DepositRequest.id))
        .limit(8)
        .offset(page * 8)
    )
    reqs = list(result.scalars().all())
    if not reqs:
        await callback.answer("لا طلبات معلقة.", show_alert=True)
        return
    rows = []
    for r in reqs:
        user = await session.get(User, r.user_id)
        rows.append(
            [InlineKeyboardButton(
                text=f"💳 #{r.id} {r.amount_usd}$ (@{user.telegram_id if user else '؟'})",
                callback_data=f"mc:dep:{r.id}",
            )]
        )
    rows.append([InlineKeyboardButton(text="🔙 المتجر", callback_data=f"mc:view:{t.id}")])
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"💳 <b>طلبات الشحن المعلقة — {esc(t.brand_name)}:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("mc:dep:"))
async def merchant_dep_view(callback: CallbackQuery, session, db_user: User):
    req = await session.get(DepositRequest, int(callback.data.split(":")[2]))
    if req is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    t = await _owned(session, db_user.id, req.tenant_id or 0)
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    user = await session.get(User, req.user_id)
    text = (
        f"💳 <b>طلب شحن #{req.id}</b>\n\n"
        f"👤 الزبون: <code>{user.telegram_id if user else '—'}</code>\n"
        f"💰 المبلغ: <b>{req.amount_usd}$</b>\n"
        f"🧾 الإثبات: <code>{esc(req.proof_tx_number or '—')}</code>\n"
        f"📌 الحالة: {req.status.value if hasattr(req.status, 'value') else req.status}"
    )
    rows = []
    if req.status == DepositStatus.PENDING:
        rows.append([
            InlineKeyboardButton(text="✅ اعتماد", callback_data=f"mc:dep_ok:{req.id}"),
            InlineKeyboardButton(text="❌ رفض", callback_data=f"mc:dep_no:{req.id}"),
        ])
    rows.append([InlineKeyboardButton(text="🔙 الشحن", callback_data=f"mc:deps:{t.id}:0")])
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass


@router.callback_query(F.data.startswith("mc:dep_ok:"))
async def merchant_dep_approve(callback: CallbackQuery, session, db_user: User):
    req = await session.get(DepositRequest, int(callback.data.split(":")[2]))
    if req is None or req.status != DepositStatus.PENDING:
        await callback.answer("غير صالح.", show_alert=True)
        return
    t = await _owned(session, db_user.id, req.tenant_id or 0)
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    try:
        await BalanceService.add_balance(
            session,
            req.user_id,
            req.amount_usd,
            TransactionType.DEPOSIT,
            description=f"شحن معتمد من {t.brand_name} #{req.id}",
            related_table="deposit_requests",
            related_id=req.id,
            tenant_id=t.id,
        )
    except Exception:
        await callback.answer("تعذر الإضافة (ربما اعتُمد مسبقاً).", show_alert=True)
        return
    req.status = DepositStatus.APPROVED
    req.admin_id = db_user.id
    req.processed_at = datetime.utcnow()
    await session.commit()
    # إشعار الزبون عبر بوته
    try:
        from services.tenant_runtime import get_tenant_bot

        sub_bot = get_tenant_bot(t)
        user = await session.get(User, req.user_id)
        if user:
            await sub_bot.send_message(
                user.telegram_id,
                f"✅ <b>تم شحن رصيدك!</b>\n\n💰 المبلغ: <b>{req.amount_usd}$</b>",
            )
    except Exception:
        pass
    await callback.answer("✅ اعتُمد وشُحن.")
    callback.data = f"mc:dep:{req.id}"
    await merchant_dep_view(callback, session, db_user)


@router.callback_query(F.data.startswith("mc:dep_no:"))
async def merchant_dep_reject_start(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    req = await session.get(DepositRequest, int(callback.data.split(":")[2]))
    if req is None or req.status != DepositStatus.PENDING:
        await callback.answer("غير صالح.", show_alert=True)
        return
    t = await _owned(session, db_user.id, req.tenant_id or 0)
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    await state.update_data(mc_reject_dep=req.id)
    await state.set_state(MerchantStates.waiting_reject_reason)
    await callback.message.answer("❌ أرسل سبب الرفض:")
    await callback.answer()


@router.message(MerchantStates.waiting_reject_reason)
async def merchant_dep_reject_received(message: Message, state: FSMContext, session, db_user: User):
    data = await state.get_data()
    await state.clear()
    req = await session.get(DepositRequest, int(data.get("mc_reject_dep") or 0))
    if req is None:
        await message.answer("⚠️ غير موجود.")
        return
    t = await _owned(session, db_user.id, req.tenant_id or 0)
    if t is None:
        await message.answer("⚠️ غير مصرح.")
        return
    req.status = DepositStatus.REJECTED
    req.admin_id = db_user.id
    req.reject_reason = (message.text or "")[:255]
    req.processed_at = datetime.utcnow()
    await session.commit()
    try:
        from services.tenant_runtime import get_tenant_bot

        sub_bot = get_tenant_bot(t)
        user = await session.get(User, req.user_id)
        if user:
            await sub_bot.send_message(
                user.telegram_id,
                f"❌ <b>رُفض طلب الشحن #{req.id}</b>\n\nالسبب: {esc(req.reject_reason or '')}",
            )
    except Exception:
        pass
    await message.answer("✅ رُفض وأُبلغ الزبون.")


# ─────────── تقرير + اشتراك + إيقاف ───────────

@router.callback_query(F.data.startswith("mc:report:"))
async def merchant_report(callback: CallbackQuery, session, db_user: User):
    t = await _owned(session, db_user.id, int(callback.data.split(":")[2]))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    wallet = await TenantService.wallet(session, t.id)
    maps = (
        await session.execute(
            select(TenantOrderMap).where(TenantOrderMap.tenant_id == t.id)
        )
    ).scalars().all()
    gross_base = sum((m.base_price_usd for m in maps), Decimal("0"))
    fees = sum((m.fee_usd for m in maps), Decimal("0"))
    users_count = (
        await session.execute(
            select(func.count(User.id)).where(User.tenant_id == t.id)
        )
    ).scalar_one()
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"📊 <b>تقرير {esc(t.brand_name)}</b>\n\n"
            f"👥 الزبائن: {users_count}\n"
            f"🧾 الطلبات: {len(maps)}\n"
            f"💵 مبيع بسعرنا: {gross_base}$\n"
            f"🏦 عمولات المنصة: {fees}$\n"
            f"💰 ربحك المتراكم: {wallet.total_earned_usd}$\n"
            f"💼 المحفظة الآن: {wallet.balance}$",
            reply_markup=_back_home(),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("mc:subpay:"))
async def merchant_subpay(callback: CallbackQuery, session, db_user: User):
    t = await _owned(session, db_user.id, int(callback.data.split(":")[2]))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    from datetime import timedelta

    try:
        await TenantService.charge(session, t.id, SUBSCRIPTION_USD)
    except TenantError:
        await callback.answer("رصيد المحفظة لا يكفي $8 — موّلها أولاً.", show_alert=True)
        return
    t.subscription_status = "active"
    t.subscription_due_at = datetime.utcnow() + timedelta(days=30)
    t.suspended_reason = None
    await session.commit()
    # إعادة تفعيل webhook إن لزم
    if not t.webhook_set:
        ok = await TenantService.register_webhook(session, t)
        if not ok:
            await callback.answer("دُفع الاشتراك لكن تعذر تفعيل الربط — راجع الدعم.", show_alert=True)
            return
    await callback.answer("✅ تم تجديد الاشتراك.")
    callback.data = f"mc:view:{t.id}"
    await merchant_view(callback, session, db_user)


@router.callback_query(F.data.startswith("mc:toggle:"))
async def merchant_toggle(callback: CallbackQuery, session, db_user: User):
    t = await _owned(session, db_user.id, int(callback.data.split(":")[2]))
    if t is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    t.is_active = not t.is_active
    await session.commit()
    try:
        from services.tenant_runtime import drop_tenant_bot

        if t.is_active:
            await TenantService.register_webhook(session, t)
        else:
            await TenantService.unregister_webhook(session, t)
        drop_tenant_bot(t.id)
    except Exception:
        pass
    await callback.answer("✅ حُفظ.")
    callback.data = f"mc:view:{t.id}"
    await merchant_view(callback, session, db_user)

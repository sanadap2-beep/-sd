"""
واجهات تليجرام للميزات الجديدة.

بدون هذا الملف تبقى الخدمات معزولة: الكود يعمل لكن المستخدم لا يصل
إليه. كل زر هنا يفحص علم الميزة أولاً، فإن كانت موقوفة من لوحة
الأدمن لا يظهر ولا يستجيب.

الميزات المربوطة:
- بورصة الأرقام (أوامر حدّ)
- قابلية نقل الرقم
- شهادات VIP
- غرف الشراء الجماعي
- أسهم حصة الإحالة
- مهام مقابل رصيد
- تتبع أسعار الألعاب
- الطلب الصوتي
- المساعد الذكي
- ذكاء السوق (للأدمن)
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import User
from services.collectibles_service import (
    ExchangeError,
    NumberExchangeService,
    NumberPortabilityService,
    VipCertificateService,
)
from services.feature_service import FeatureService
from services.growth_channels_service import (
    GrowthError,
    MarketIntelligenceService,
    PooledRoomService,
    RevenueShareService,
    TaskToCreditService,
    VoiceOrderingService,
)
from services.platform_service import GamePriceTrackerService
from services.ai_layer_service import AIAgentService
from services.i18n_service import I18nService
from services.main_button_service import MainButtonService
from states.states import ExtrasStates

from decimal import Decimal, InvalidOperation

router = Router(name="extras")


def _lang(db_user) -> str:
    return getattr(db_user, "language_code", "ar") or "ar"


async def _enabled(key: str) -> bool:
    return await FeatureService.enabled(key)


# ══════════════ القائمة الجامعة ══════════════


def _extras_labels(language: str) -> dict[str, str]:
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    return {
        "withdraw": t("menu_withdraw"),
        "search": t("menu_search"),
        "favorites": t("menu_favorites"),
        "cart": t("menu_cart"),
        "loyalty": t("menu_loyalty"),
        "promotions": t("menu_promotions"),
        "request": t("menu_product_request"),
        "gift": t("menu_gift"),
        "assistant": t("menu_assistant"),
        "offers": t("menu_special_offers"),
        "ads": t("menu_my_ads"),
        "notif": t("menu_notifications"),
        "status": t("menu_status"),
        "challenges": t("menu_challenges"),
        "num_packages": f"📦 {t('menu_numbers')}",
        "number_exchange": t("extras_number_exchange"),
        "number_portability": t("extras_number_portability"),
        "vip_number_certificates": t("extras_vip_certificates"),
        "pooled_rooms": t("extras_pooled_rooms"),
        "revenue_sharing_tokens": t("extras_revenue_share"),
        "task_to_credit": t("extras_task_to_credit"),
        "game_price_tracker": t("extras_game_prices"),
        "ai_agent_layer": t("extras_ai_agent"),
        "peer_marketplace": t("menu_marketplace"),
        "tasks_system": t("menu_tasks"),
        "points_currency": t("menu_points"),
    }


def _button_row(label: str, action: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=label,
            url=action if action.startswith(("http://", "https://")) else None,
            callback_data=None if action.startswith(("http://", "https://")) else action,
        )
    ]


@router.callback_query(F.data == "extras:home")
async def extras_home(callback: CallbackQuery, db_user: User | None = None):
    """Show the compact Extras hub as three high-level sections."""
    language = _lang(db_user)
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    from services.extras_section_service import EXTRAS_SECTIONS, ExtrasSectionService

    visible_by_section: dict[str, int] = {}
    for section_key in EXTRAS_SECTIONS:
        visible_by_section[section_key] = len(
            await ExtrasSectionService.visible_entries(section=section_key)
        )

    rows = []
    for section_key, titles in EXTRAS_SECTIONS.items():
        title = titles[0] if language == "ar" else titles[1]
        count = visible_by_section.get(section_key, 0)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} ({count})",
                    callback_data=f"extras:section:{section_key}", style="success",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text=t("back_to_main"), callback_data="menu:main")])

    await callback.message.edit_text(
        f"{t('menu_extras')}\n\n"
        f"{('اختر قسماً رئيسياً ثم ستظهر لك خدماته المختصرة:' if language == 'ar' else 'Choose a main section, then pick the service you need:')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("extras:section:"))
async def extras_section(callback: CallbackQuery, db_user: User | None = None):
    """Open one Extras section and render only its child actions."""
    language = _lang(db_user)
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    from services.extras_section_service import EXTRAS_SECTIONS, TOOLS, ExtrasSectionService

    section_key = (callback.data or "").rsplit(":", 1)[-1]
    if section_key not in EXTRAS_SECTIONS:
        await callback.answer(I18nService.t("not_available", language), show_alert=True)
        return

    labels = _extras_labels(language)
    entries: list[tuple[str, str]] = []
    for entry in await ExtrasSectionService.visible_entries(section=section_key):
        entries.append((labels.get(entry.key, entry.label), entry.action))

    # Admin-created shortcuts live under Advanced tools to keep the first
    # Extras screen fixed at three sections.
    if section_key == TOOLS:
        covered_actions = {action for _, action in entries} | {"store:home"}
        for button in await MainButtonService.list_buttons(include_inactive=False):
            if button.action in covered_actions:
                continue
            if button.action.startswith("store:section:") or button.action.startswith("cat:"):
                continue
            entries.append((button.label, button.action))
            covered_actions.add(button.action)

    rows = [_button_row(label, action) for label, action in entries]
    if not rows:
        rows.append([InlineKeyboardButton(text=("لا توجد عناصر مفعلة" if language == "ar" else "No enabled items"), callback_data="noop")])
    rows.append([InlineKeyboardButton(text=("⬅️ رجوع للأقسام" if language == "ar" else "⬅️ Back to sections"), callback_data="extras:home", style="success")])
    rows.append([InlineKeyboardButton(text=t("back_to_main"), callback_data="menu:main")])

    title = EXTRAS_SECTIONS[section_key][0] if language == "ar" else EXTRAS_SECTIONS[section_key][1]
    await callback.message.edit_text(
        f"{title}\n\n{('اختر الخدمة المطلوبة:' if language == 'ar' else 'Choose a service:')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ══════════════ بورصة الأرقام ══════════════


@router.callback_query(F.data == "extras:exchange")
async def exchange_home(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("number_exchange"):
        await callback.answer("بورصة الأرقام موقوفة.", show_alert=True)
        return
    orders = await NumberExchangeService.open_orders(session, db_user.id)
    lines = []
    for order in orders:
        lines.append(
            f"• {order.service_code}/{order.country_code} — "
            f"{order.quantity} رقم عند {order.target_price_usd}$"
        )
    text = "📈 <b>بورصة الأرقام</b>\n\n"
    text += (
        "أوامرك المفتوحة:\n" + "\n".join(lines)
        if lines
        else "لا أوامر مفتوحة."
    )
    text += "\n\nضع أمراً: «اشترِ N رقم تلقائياً حين ينزل السعر تحت X»."
    rows = [
        [InlineKeyboardButton(text="➕ أمر حدّ جديد", callback_data="extras:exnew", style="success")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "extras:exnew")
async def exchange_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ExtrasStates.waiting_limit_order)
    await callback.message.answer(
        "📈 <b>أمر حدّ جديد</b>\n\n"
        "أرسل بالصيغة:\n<code>الخدمة الدولة الكمية السعر</code>\n"
        "مثال: <code>tg tr 50 0.28</code>\n\n"
        "يعني: اشترِ 50 رقم تليجرام تركيا حين ينزل السعر تحت 0.28$"
    )
    await callback.answer()


@router.message(ExtrasStates.waiting_limit_order)
async def exchange_submit(message: Message, state: FSMContext, session, db_user: User):
    parts = (message.text or "").split()
    if len(parts) != 4:
        return await message.answer("⚠️ الصيغة: <code>الخدمة الدولة الكمية السعر</code>")
    service_code, country_code, quantity_raw, price_raw = parts
    try:
        quantity = int(quantity_raw)
        price = Decimal(price_raw)
    except (ValueError, InvalidOperation):
        return await message.answer("⚠️ الكمية والسعر يجب أن يكونا رقمين.")
    await state.clear()
    try:
        order = await NumberExchangeService.place_limit_order(
            session, db_user.id, service_code, country_code, quantity, price
        )
    except ExchangeError as exc:
        return await message.answer(f"⚠️ {exc}")
    await message.answer(
        f"✅ <b>أُضيف الأمر #{order['order_id']}</b>\n\n"
        f"سيُنفَّذ تلقائياً حين ينزل سعر {service_code}/{country_code} "
        f"تحت {price}$."
    )


# ══════════════ أرقامي المحفوظة ══════════════


@router.callback_query(F.data == "extras:portability")
async def portability_home(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("number_portability"):
        await callback.answer("هذه الميزة موقوفة.", show_alert=True)
        return
    orders = await NumberPortabilityService.reclaimable(session, db_user.id)
    if not orders:
        text = "🔁 لا أرقام محفوظة لك حالياً.\n\nبعد شراء رقم يمكنك حفظه ليعود إليك لاحقاً."
    else:
        lines = [
            f"• <code>{o.phone_number}</code> — محفوظ حتى {o.portable_until:%Y-%m-%d %H:%M}"
            for o in orders
        ]
        text = "🔁 <b>أرقامك المحفوظة</b>\n\n" + "\n".join(lines)
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")]]
        ),
    )
    await callback.answer()


# ══════════════ شهادات VIP ══════════════


@router.callback_query(F.data == "extras:vip")
async def vip_home(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("vip_number_certificates"):
        await callback.answer("شهادات VIP موقوفة.", show_alert=True)
        return
    owned = await VipCertificateService.owned_by(session, db_user.id)
    fee = await VipCertificateService.fee()
    if owned:
        lines = [f"• <code>{c.serial}</code> — {c.phone_number}" for c in owned]
        body = "شهاداتك:\n" + "\n".join(lines)
    else:
        body = "لا تملك شهادات بعد."
    await callback.message.edit_text(
        f"👑 <b>شهادات ملكية VIP</b>\n\n{body}\n\n"
        f"رسوم الإصدار: <b>{fee}$</b>\n"
        "الشهادة تُثبت ملكية رقم نادر ويمكن تحويلها لمستخدم آخر.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ إصدار شهادة", callback_data="extras:vipnew", style="success")],
                [InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "extras:vipnew")
async def vip_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ExtrasStates.waiting_vip_number)
    await callback.message.answer("👑 أرسل الرقم النادر الذي تريد شهادة ملكية له:")
    await callback.answer()


@router.message(ExtrasStates.waiting_vip_number)
async def vip_submit(message: Message, state: FSMContext, session, db_user: User):
    number = (message.text or "").strip()
    await state.clear()
    try:
        cert = await VipCertificateService.issue(session, db_user.id, number)
    except ExchangeError as exc:
        return await message.answer(f"⚠️ {exc}")
    await message.answer(
        f"✅ <b>صدرت الشهادة</b>\n\n"
        f"التسلسلي: <code>{cert['serial']}</code>\n"
        f"الرقم: <code>{cert['phone_number']}</code>\n"
        f"الرسوم: {cert['fee_usd']}$\n\n"
        "يمكنك تحويلها لمستخدم آخر متى شئت."
    )


# ══════════════ غرف الشراء الجماعي ══════════════


@router.callback_query(F.data == "extras:rooms")
async def rooms_home(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("pooled_rooms"):
        await callback.answer("غرف الشراء الجماعي موقوفة.", show_alert=True)
        return
    from database.models import PurchaseRoom
    from sqlalchemy import select

    result = await session.execute(
        select(PurchaseRoom)
        .where(PurchaseRoom.status.in_(["open", "ready"]))
        .order_by(PurchaseRoom.id.desc())
        .limit(10)
    )
    rooms = list(result.scalars().all())
    needed = await PooledRoomService.min_members()
    if not rooms:
        text = "👥 لا غرف مفتوحة حالياً.\n\nأنشئ غرفة وادعُ أصدقاءك ليفتح خصم الجملة."
    else:
        lines = [
            f"• غرفة #{r.id} — {r.members}/{needed} عضو"
            + (" ✅ جاهزة" if r.status == "ready" else "")
            for r in rooms
        ]
        text = "👥 <b>الغرف المفتوحة</b>\n\n" + "\n".join(lines)
    rows = [[InlineKeyboardButton(text="➕ إنشاء غرفة", callback_data="extras:roomnew", style="success")]]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "extras:roomnew")
async def room_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ExtrasStates.waiting_room)
    await callback.message.answer(
        "👥 <b>غرفة شراء جماعي</b>\n\n"
        "أرسل: <code>آيدي_المنتج الكمية</code>\n"
        "مثال: <code>1 5000</code>"
    )
    await callback.answer()


@router.message(ExtrasStates.waiting_room)
async def room_submit(message: Message, state: FSMContext, session, db_user: User):
    parts = (message.text or "").split()
    await state.clear()
    if len(parts) != 2:
        return await message.answer("⚠️ الصيغة: <code>آيدي_المنتج الكمية</code>")
    try:
        product_id, quantity = int(parts[0]), int(parts[1])
    except ValueError:
        return await message.answer("⚠️ أرسل رقمين صحيحين.")
    try:
        room = await PooledRoomService.create(session, db_user.id, product_id, quantity)
    except GrowthError as exc:
        return await message.answer(f"⚠️ {exc}")
    needed = await PooledRoomService.min_members()
    await message.answer(
        f"✅ <b>أُنشئت الغرفة #{room['room_id']}</b>\n\n"
        f"الهدف: {quantity} وحدة\n"
        f"النصاب: {needed} أعضاء\n\n"
        "شارك رقم الغرفة مع أصدقائك ليفتح خصم الجملة."
    )


@router.callback_query(F.data.startswith("extras:roomjoin:"))
async def room_join(callback: CallbackQuery, session, db_user: User):
    room_id = int(callback.data.split(":")[2])
    try:
        result = await PooledRoomService.join(session, room_id, db_user.id)
    except GrowthError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if result["ready"]:
        await callback.answer("✅ اكتمل النصاب! فُتح خصم الجملة.", show_alert=True)
    else:
        await callback.answer(
            f"انضممت. الأعضاء {result['members']}/{result['needed']}", show_alert=True
        )


# ══════════════ أسهم حصة الإحالة ══════════════


@router.callback_query(F.data == "extras:revshare")
async def revshare_home(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("revenue_sharing_tokens"):
        await callback.answer("أسهم حصة الإحالة موقوفة.", show_alert=True)
        return
    projected = await RevenueShareService.projected_commission(session, db_user.id, 30)
    max_share = await RevenueShareService.max_share_percent()
    await callback.message.edit_text(
        "💹 <b>أسهم حصة الإحالة</b>\n\n"
        f"عمولتك المتوقعة (30 يوم): <b>{projected}$</b>\n\n"
        "يمكنك بيع جزء من هذه العمولة المستقبلية مقابل كاش فوري.\n"
        f"أقصى نسبة قابلة للبيع: <b>{max_share}%</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ إصدار سهم", callback_data="extras:rsnew", style="success")],
                [InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "extras:rsnew")
async def revshare_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ExtrasStates.waiting_revshare)
    await callback.message.answer(
        "💹 <b>إصدار سهم</b>\n\n"
        "أرسل: <code>النسبة_المئوية السعر</code>\n"
        "مثال: <code>20 15</code>\n\n"
        "يعني: تبيع 20% من عمولتك المستقبلية بـ15$ تقبضها الآن."
    )
    await callback.answer()


@router.message(ExtrasStates.waiting_revshare)
async def revshare_submit(message: Message, state: FSMContext, session, db_user: User):
    parts = (message.text or "").split()
    await state.clear()
    if len(parts) != 2:
        return await message.answer("⚠️ الصيغة: <code>النسبة_المئوية السعر</code>")
    try:
        share_percent, price = int(parts[0]), Decimal(parts[1])
    except (ValueError, InvalidOperation):
        return await message.answer("⚠️ أرسل نسبة رقماً وسعراً رقمياً.")
    try:
        token = await RevenueShareService.issue(session, db_user.id, share_percent, price)
    except GrowthError as exc:
        return await message.answer(f"⚠️ {exc}")
    await message.answer(
        f"✅ <b>صدر السهم #{token['token_id']}</b>\n\n"
        f"النسبة: {share_percent}%\n"
        f"أُضيف لرصيدك: <b>{price}$</b>\n\n"
        "من يشتريه سيأخذ هذه النسبة من عمولاتك المستقبلية."
    )


# ══════════════ مهام مقابل رصيد ══════════════


@router.callback_query(F.data == "extras:task2credit")
async def task2credit_home(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("task_to_credit"):
        await callback.answer("هذه الميزة موقوفة.", show_alert=True)
        return
    rows = []
    for task_type, (label, reward) in TaskToCreditService.TASK_TYPES.items():
        actual = await TaskToCreditService.reward_for(task_type)
        rows.append(
            [InlineKeyboardButton(
                text=f"🧾 {label} — {actual}$", callback_data=f"extras:t2c:{task_type}", style="success"
            )]
        )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")])
    await callback.message.edit_text(
        "🧾 <b>مهام مقابل رصيد</b>\n\n"
        "أنجز مهمة مفيدة للبوت واكسب رصيداً فورياً.\n"
        "كل مهمة تُكافأ مرة واحدة لكل دليل.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("extras:t2c:"))
async def task2credit_do(callback: CallbackQuery, session, db_user: User):
    task_type = callback.data.split(":")[2]
    # الدليل هو المعرف الفريد للسياق (هنا: نوع المهمة + المستخدم + اليوم)
    from datetime import datetime

    proof = f"{task_type}:{datetime.utcnow():%Y%m%d}"
    try:
        result = await TaskToCreditService.complete(session, db_user.id, task_type, proof)
    except GrowthError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(f"✅ كسبت {result['reward_usd']}$", show_alert=True)
    await task2credit_home(callback)


# ══════════════ أسعار الألعاب ══════════════


@router.callback_query(F.data == "extras:gameprices")
async def gameprices_home(callback: CallbackQuery, state: FSMContext, session):
    if not await _enabled("game_price_tracker"):
        await callback.answer("هذه الميزة موقوفة.", show_alert=True)
        return
    rows = await GamePriceTrackerService.compare(session, "")
    if not rows:
        await callback.message.edit_text(
            "🎮 لا خدمات مسعّرة حالياً.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")]]
            ),
        )
        await callback.answer()
        return
    lines = [
        f"• {r['name']} — <b>{r['rate_usd']}$/1000</b>" for r in rows[:15]
    ]
    await callback.message.edit_text(
        "🎮 <b>أسعار مرتبة من الأرخص</b>\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")]]
        ),
    )
    await callback.answer()


# ══════════════ المساعد الذكي ══════════════


@router.callback_query(F.data == "extras:ai")
async def ai_home(callback: CallbackQuery, state: FSMContext):
    if not await _enabled("ai_agent_layer"):
        await callback.answer("المساعد الذكي موقوف.", show_alert=True)
        return
    await state.set_state(ExtrasStates.waiting_ai_query)
    await callback.message.answer(
        "🤖 <b>المساعد الذكي</b>\n\n"
        "اكتب ما تريده بلغتك، مثل:\n"
        "«أبغى أرخص رقم تليجرام»\n"
        "«بدي 1000 متابع تيك توك»"
    )
    await callback.answer()


@router.message(ExtrasStates.waiting_ai_query)
async def ai_answer(message: Message, state: FSMContext, session, db_user: User):
    await state.clear()
    result = await AIAgentService.respond(session, db_user.id, message.text or "")
    products = result.get("products") or []
    rows = []
    for product in products[:6]:
        rows.append(
            [InlineKeyboardButton(
                text=f"{product.name_ar} — {product.price_usd}$",
                callback_data=f"prod:{product.id}", style="success",
            )]
        )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")])
    await message.answer(
        result["text"], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


# ══════════════ الطلب الصوتي ══════════════


@router.message(F.voice)
async def voice_message(message: Message, session, db_user: User, bot):
    """يستقبل تسجيلاً صوتياً ويحوّله طلباً — إن كانت الميزة مفعّلة."""
    if not await _enabled("voice_ordering"):
        return
    voice = message.voice
    if voice is None:
        return
    if voice.duration and voice.duration > 60:
        await message.answer("⚠️ التسجيل أطول من 60 ثانية.")
        return

    import tempfile
    from pathlib import Path

    temp_path = Path(tempfile.gettempdir()) / f"voice_{db_user.id}_{voice.file_unique_id}.ogg"
    try:
        file = await bot.get_file(voice.file_id)
        await bot.download(file, destination=str(temp_path))
    except Exception:
        await message.answer("⚠️ تعذّر تحميل التسجيل.")
        return

    language = "ar" if _lang(db_user).startswith("ar") else "en"
    text, ok = await VoiceOrderingService.transcribe(str(temp_path), language)
    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass

    if not ok or not text:
        await message.answer(
            "🎙 تعذّر تحويل الصوت إلى نص.\nاكتب طلبك نصاً وسأنفذه فوراً."
        )
        return

    intent = VoiceOrderingService.parse_intent(text)
    labels = {
        "deposit": "💰 شحن رصيد",
        "buy_number": "📱 شراء رقم",
        "buy_smm": "📈 خدمات رشق",
        "check_balance": "👤 حسابي",
        "unknown": None,
    }
    label = labels.get(intent["intent"])
    if label is None:
        await message.answer(f"🎙 سمعت: «{text}»\n\nلم أفهم المطلوب، أعد صياغته.")
        return
    routes = {
        "deposit": "menu:deposit",
        "buy_number": "menu:main",
        "buy_smm": "menu:main",
        "check_balance": "menu:account",
    }
    await message.answer(
        f"🎙 سمعت: «{text}»\n➡️ {label}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label, callback_data=routes[intent["intent"]])]
            ]
        ),
    )


# ══════════════ ذكاء السوق (للأدمن) ══════════════


@router.callback_query(F.data == "admin:market_intel")
async def market_intel(callback: CallbackQuery, session):
    if not await _enabled("market_intelligence"):
        await callback.answer("ذكاء السوق موقوف.", show_alert=True)
        return
    report = await MarketIntelligenceService.report(session, 30)
    if not report.get("available"):
        await callback.answer("التقرير غير متاح.", show_alert=True)
        return
    countries = "\n".join(
        f"• {c['country']}: {c['orders']} طلب" for c in report["by_country"][:10]
    ) or "—"
    services = "\n".join(
        f"• {s['service']}: {s['orders']} طلب" for s in report["by_service"][:10]
    ) or "—"
    await callback.message.edit_text(
        "📊 <b>ذكاء السوق (30 يوم)</b>\n\n"
        f"الحجم: <b>{report['total_volume_usd']}$</b>\n"
        f"مجهول الهوية: {'✅' if report['anonymized'] else '❌'}\n\n"
        f"<b>حسب الدولة:</b>\n{countries}\n\n"
        f"<b>حسب الخدمة:</b>\n{services}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")]]
        ),
    )
    await callback.answer()
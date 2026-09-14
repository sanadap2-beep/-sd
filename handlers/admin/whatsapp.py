"""
إدارة قسم واتساب من لوحة الأدمن.

- 📝 الوصف والسعر: وصف القسم (يظهر للمستخدم) + سعر اليوم + الباقات.
- 🔌 الجسر: عنوان الجسر + سره + اختبار اتصال + القدرات (capabilities).
- 👥 المشتركون: حالة كل مستخدم (رقم/ربط/انتهاء/تجديد تلقائي) + عمليات
  (تمديد مجاني، فصل ربط، استرداد آخر دفعة).
- 📊 إحصاءات: نشطين/مربوطين/إيراد 30 يوم.

شاشة الجسر تعرض حالة الاتصال دائماً (🟢/🟠/🔴) حتى لا يُكتشف العطب من شكاوى
المستخدمين، لأن القسم كاملاً يعتمد على خدمة تعمل **بجانب البوت الثاني**.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import desc, func, select

from database.models import User, WaSubscription
from filters.admin_filter import IsAdmin
from services import wa_bridge_client
from services.feature_service import FeatureService
from services.settings_service import SettingsService
from services.whatsapp_section_service import WhatsAppSectionService
from states.states import AdminWaStates

router = Router(name="admin_whatsapp")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_PAGE_RE = re.compile(r"^admin:wa_users:(\d+)$")
_DETAIL_RE = re.compile(r"^admin:wa_detail:(\d+)$")
_OPS_RE = re.compile(r"^admin:wa_ops:(\d+):(extend|unlink|refund)(?::(\d+))?$")

_LINK_MARKS = {"none": "—", "pending": "⏳", "linked": "🔗", "expired": "💀"}
_PER_PAGE = 12


def _mask(value: str) -> str:
    return f"{'*' * 6}{value[-4:]}" if value and len(value) > 8 else ("**" if value else "—")


def _fmt(until) -> str:
    return until.strftime("%Y-%m-%d %H:%M") if until else "—"


async def _wa_configured() -> bool:
    """هل عنوان الجسر وسرّه مضبوطان؟ (قيم لوحة الأدمن تسبق ملف البيئة)."""
    from config import settings as cfg

    url = (await SettingsService.get("wa_bridge_url") or "").strip() or cfg.WA_BRIDGE_URL
    secret = (
        await SettingsService.get("wa_bridge_secret") or ""
    ).strip() or cfg.WA_BRIDGE_SECRET
    return bool(url.strip() and secret.strip())


async def _bridge_status_line() -> str:
    """سطر حالة واحد أعلى شاشة الإدارة."""
    if not await _wa_configured():
        return "🔴 الجسر غير مضبوط (لا عنوان ولا سر) — القسم لا يعمل عند أي مستخدم"
    failures = wa_bridge_client.consecutive_failures()
    if failures:
        return (
            f"🟠 الجسر مضبوط لكنه فشل {failures} مرة متتالية — آخر خطأ: "
            f"{wa_bridge_client.last_bridge_error()[:120]}"
        )
    return "🟢 الجسر مضبوط (آخر محاولة نجحت)"


# ══════════════ القائمة ══════════════


@router.callback_query(F.data == "admin:wa_sections")
async def wa_menu(callback: CallbackQuery):
    status_line = await _bridge_status_line()
    b = InlineKeyboardBuilder()
    b.button(text="📝 الوصف والسعر", callback_data="admin:wa_desc", style="primary")
    b.button(text="🔌 الجسر (URL + سر)", callback_data="admin:wa_bridge")
    b.button(text="👥 المشتركون", callback_data="admin:wa_users:0")
    b.button(text="📊 الإحصاءات", callback_data="admin:wa_stats")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(2, 2, 1)
    await callback.message.edit_text(
        "📱 <b>إدارة قسم واتساب</b>\n\n"
        "هذا القسم مربوط ببوتك الثاني عبر <b>الجسر</b> "
        "(<code>wa_bridge/bridge.py</code> + <code>wa_bridge/adapter_example.py</code> "
        "اللانذان يعملان بجانبه). من هنا تكتب وصف القسم وسعر الباقات، وتضبط عنوان "
        f"الجسر وسرّه، وتراجع المشتركين.\n\nحالة الاتصال: {status_line}\n\n"
        "حجم صفحة الأزرار ومدة الكاش من: ⚙️ الميزات ← <code>whatsapp_section</code> "
        "← الخيارات (<code>menu_page_size</code> / <code>menu_cache_ttl_minutes</code>).",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


# ══════════════ الوصف والسعر ══════════════


@router.callback_query(F.data == "admin:wa_desc")
async def wa_desc_start(callback: CallbackQuery, state: FSMContext):
    current = (await SettingsService.get("wa_section_description", "")) or "(لا يوجد)"
    await callback.message.edit_text(
        "📝 <b>وصف قسم واتساب</b>\n\n"
        "هذا النص يظهر للمستخدم داخل القسم — اشرح ما يقدّمه القسم و"
        "كيف يربط رقمه وماذا يفعل بعد الربط.\n"
        f"الحالي: {current}\n\n"
        "أرسل الوصف الجديد (أو <code>-</code> للإبقاء):"
    )
    await state.set_state(AdminWaStates.waiting_description)
    await callback.answer()


@router.message(AdminWaStates.waiting_description)
async def wa_desc_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        if len(raw) > 2000:
            await message.answer("⚠️ الوصف أطول من 2000 حرف — خلاصه وأعد الإرسال.")
            return
        await SettingsService.set(session, "wa_section_description", raw or "")
    await state.set_state(AdminWaStates.waiting_price)
    daily = await WhatsAppSectionService.daily_price()
    await message.answer(
        f"✓ الوصف حُفظ.\n\n"
        f"أرسل <b>سعر اليوم</b> بالدولار (الحالي: {daily:g}$ — يُستخدم "
        "للتجديد التلقائي وباقة اليوم):"
    )


@router.message(AdminWaStates.waiting_price)
async def wa_price_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw == "-":
        raw = "1"
    try:
        price = Decimal(raw)
        if price <= 0 or price > Decimal("100"):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ أرسل رقماً موجباً بالدولار (مثال: 1).")
        return
    await FeatureService.set_option(session, "whatsapp_section", "price_per_day_usd", float(price))
    await state.set_state(AdminWaStates.waiting_packages)
    await message.answer(
        "✓ السعر حُفظ.\n\n"
        "أرسل <b>الباقات</b> بصيغة JSON (قائمة أيام + سعر)، أو <code>-</code> "
        "للإبقاء على الحالية:\n\n"
        "<code>"
        + json.dumps(
            [
                {"days": 1, "price_usd": 1.0},
                {"days": 3, "price_usd": 2.85},
                {"days": 7, "price_usd": 6.30},
                {"days": 30, "price_usd": 25.50},
            ],
            ensure_ascii=False,
        )
        + "</code>"
    )


@router.message(AdminWaStates.waiting_packages)
async def wa_packages_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        try:
            data = json.loads(raw)
            if not isinstance(data, list) or not data:
                raise ValueError("قائمة فارغة")
            for item in data:
                int(item["days"])
                Decimal(str(item["price_usd"]))
        except (ValueError, KeyError, TypeError) as exc:
            await message.answer(f"⚠️ JSON غير صالح: {exc}\nأعد الإرسال أو أرسل - للإبقاء.")
            return
        await FeatureService.set_option(session, "whatsapp_section", "packages_json", data)
    await state.clear()
    packages = await WhatsAppSectionService.packages()
    lines = ["📦 <b>الباقات الحالية</b>:\n"]
    for pkg in packages:
        lines.append(f"• {pkg['days']} يوم = {pkg['price_usd']:g}$")
    lines.append("\n✓ تم حفظ كل شيء.")
    b = InlineKeyboardBuilder()
    b.button(text="🔙", callback_data="admin:wa_sections")
    await message.answer("\n".join(lines), reply_markup=b.as_markup())


# ══════════════ الجسر ══════════════


@router.callback_query(F.data == "admin:wa_bridge")
async def wa_bridge_screen(callback: CallbackQuery):
    conf = await wa_bridge_client.get_config()
    url = conf["url"]
    secret_set = conf["secret_set"]
    b = InlineKeyboardBuilder()
    b.button(text="✏️ عنوان الجسر (URL)", callback_data="admin:wa_bridge_url")
    b.button(text="🔑 سر الجسر (SECRET)", callback_data="admin:wa_bridge_secret")
    b.button(
        text="🧪 اختبار الاتصال",
        callback_data="admin:wa_bridge_test",
        style="success" if url and secret_set else "danger",
    )
    b.button(text="🧰 القدرات (capabilities)", callback_data="admin:wa_bridge_caps")
    b.button(text="🔙", callback_data="admin:wa_sections")
    b.adjust(1)
    warning = ""
    if url.startswith("http://") and not any(
        host in url for host in ("localhost", "127.0.0.1", "10.", "192.168.", "172.")
    ):
        warning = (
            "\n\n⚠️ العنوان http على شبكة عامة: من يصل المنفذ ويعرف السر يستطيع "
            "تنفيذ أوامر باسم <b>أي مستخدم</b> (رأس <code>X-TG-User-Id</code> غير "
            "موقّع). شغّل الجسر على 127.0.0.1/شبكة داخلية، أو خلف TLS + IP allowlist."
        )
    await callback.message.edit_text(
        "🔌 <b>جسر واتساب</b>\n\n"
        f"العنوان: <code>{url or '—'}</code>\n"
        f"السّر: {'🔒 مضبوط' if secret_set else '— (اضبطه قبل التشغيل)'}\n\n"
        "الجسر هو الخدمة التي تعمل <b>بجانب البوت الثاني</b>: "
        "<code>wa_bridge/bridge.py</code> في هذا المستودع (يُنسخ لمشروع البوت "
        "الثاني أو يعمل من هنا مباشرة)، وتُربط به قائمة الأزرار عبر "
        "<code>wa_bridge/adapter.py</code>. القيم هنا (لوحة الأدمن) تسبق قيم "
        "ملف البيئة <code>WA_BRIDGE_URL</code> / <code>WA_BRIDGE_SECRET</code>.\n"
        "التوثيق: <code>wa_bridge/README_AR.md</code> و"
        "<code>docs/AI-AND-WHATSAPP-SECTIONS_AR.md</code>." + warning,
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:wa_bridge_url")
async def wa_bridge_url_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "✏️ أرسل عنوان الجسر (مثال: <code>http://wa-bridge:8090</code> داخل شبكة "
        "Docker، أو <code>http://10.0.0.5:8090</code>)\n"
        "أرسل <code>-</code> للإبقاء على الحالي:"
    )
    await state.set_state(AdminWaStates.waiting_bridge_url)
    await callback.answer()


@router.message(AdminWaStates.waiting_bridge_url)
async def wa_bridge_url_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        if not re.match(r"^https?://[\w.-]+(:\d+)?(/[\w./-]*)?$", raw):
            await message.answer("⚠️ أرسل رابطاً صحيحاً (http/https + منفذ اختياري).")
            return
        await SettingsService.set(session, "wa_bridge_url", raw.rstrip("/"))
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="🔙", callback_data="admin:wa_bridge")
    await message.answer("✓ تم.", reply_markup=b.as_markup())


@router.callback_query(F.data == "admin:wa_bridge_secret")
async def wa_bridge_secret_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔑 أرسل سر الجسر (يستخدم في Authorization: Bearer ...).\n"
        "يجب أن يطابق <code>BRIDGE_SECRET</code> في خدمة الجسر عند البوت الثاني.\n"
        "أنشئ واحداً: <code>openssl rand -hex 32</code>\n"
        "أرسل <code>-</code> للإبقاء على الحالي:"
    )
    await state.set_state(AdminWaStates.waiting_bridge_secret)
    await callback.answer()


@router.message(AdminWaStates.waiting_bridge_secret)
async def wa_bridge_secret_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        if len(raw) < 16:
            await message.answer(
                "⚠️ السّر قصير — استخدم 16 حرفاً على الأقل (openssl rand -hex 32)."
            )
            return
        await SettingsService.set(session, "wa_bridge_secret", raw)
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="🔙", callback_data="admin:wa_bridge")
    await message.answer("✓ تم حفظ السّر.", reply_markup=b.as_markup())


@router.callback_query(F.data == "admin:wa_bridge_test")
async def wa_bridge_test(callback: CallbackQuery):
    if not await _wa_configured():
        await callback.answer("ضع عنوان الجسر وسرّه أولاً (🔌 الجسر).", show_alert=True)
        return
    await callback.answer("جارٍ الاختبار…")
    info = await wa_bridge_client.probe()
    if not info.get("ok"):
        await callback.message.answer(
            "❌ <b>فشل الاختبار</b>\n\n"
            f"{info.get('error') or 'الجسر لا يرد'}\n\n"
            "راجع: أن الجسر يعمل • العنوان والمنفذ • تطابق السر مع "
            "<code>BRIDGE_SECRET</code> • أن حاوية بوت SD تصل لشبكة الجسر."
        )
        return
    await callback.message.answer(
        "✅ <b>الجسر يرد</b> — الربط جاهز.\n\n"
        f"زمن الرد: {info.get('latency_ms')}ms · إصدار "
        f"<code>{info.get('version') or '1'}</code> · adapter "
        f"<code>{info.get('adapter') or '—'}</code>\n"
        f"القدرات: {', '.join(info.get('capabilities') or []) or 'غير معلنة (جسر بإصدار قديم)'}"
    )


@router.callback_query(F.data == "admin:wa_bridge_caps")
async def wa_bridge_caps(callback: CallbackQuery):
    await callback.answer("جارٍ الفحص…")
    info = await wa_bridge_client.probe()
    if not info.get("ok"):
        await callback.message.answer(f"❌ الجسر لا يرد: {info.get('error') or ''}")
        return

    def flag(name: str) -> str:
        return "✅" if name in (info.get("capabilities") or []) else "⚪"

    await callback.message.answer(
        "🧰 <b>قدرات الجسر</b>\n\n"
        f"الإصدار: <code>{info.get('version') or '1 (قديم)'}</code> · "
        f"adapter: <code>{info.get('adapter') or '—'}</code> · "
        f"زمن الرد: {info.get('latency_ms')}ms\n\n"
        f"{flag('input')} أزرار البوت الثاني التي تحتاج كتابة\n"
        f"{flag('files')} ملفات/صور تصل للمستخدم عبر بوت SD\n"
        f"{flag('links')} أزرار تفتح رابطاً\n"
        f"{flag('paging')} ترقيم صفحات القائمة\n"
        f"{flag('unlink')} إنهاء الجلسة من هنا\n\n"
        "ما ليس معلَّمًا يعمل بشكله الأساسي فقط — التفاصيل في "
        "<code>wa_bridge/README_AR.md</code>."
    )


# ══════════════ المشتركون ══════════════


@router.callback_query(_PAGE_RE)
async def wa_users(callback: CallbackQuery, session):
    page = int(callback.data.split(":")[2])
    result = await session.execute(
        select(WaSubscription)
        .order_by(desc(WaSubscription.active_until), desc(WaSubscription.updated_at))
        .limit(_PER_PAGE)
        .offset(page * _PER_PAGE)
    )
    subs = list(result.scalars().all())
    total = await session.scalar(select(func.count(WaSubscription.id))) or 0
    lines = [
        f"👥 <b>مشتركو واتساب</b> (صفحة {page + 1}/{max(1, -(-total // _PER_PAGE))})\n"
    ]
    if not subs:
        lines.append("لا يوجد مشتركون بعد.")
    now = datetime.utcnow()
    b = InlineKeyboardBuilder()
    for sub in subs:
        user = await session.get(User, sub.user_id)
        name = (
            (user.full_name or user.username or str(user.telegram_id))
            if user
            else f"#{sub.user_id}"
        )
        mark = "🟢" if (sub.active_until and sub.active_until > now) else "🔴"
        linked = _LINK_MARKS.get(sub.link_state, "?")
        warn = " ⚠️" if sub.link_error else ""
        lines.append(f"{mark} {name[:26]} · حتى {_fmt(sub.active_until)} · {linked}{warn}")
        b.button(text=f"⚙️ {name[:16]}", callback_data=f"admin:wa_detail:{sub.id}")
    b.adjust(2)
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="◀️ السابق", callback_data=f"admin:wa_users:{page - 1}")
    if (page + 1) * _PER_PAGE < total:
        nav.button(text="التالي ▶️", callback_data=f"admin:wa_users:{page + 1}")
    nav.button(text="🔙", callback_data="admin:wa_sections")
    nav.adjust(3)
    markup = b.as_markup()
    markup.inline_keyboard.extend(nav.as_markup().inline_keyboard)
    await callback.message.edit_text("\n".join(lines)[:4000], reply_markup=markup)
    await callback.answer()


async def _render_detail(message, session, sub_id: int) -> None:
    """شاشة مشترك واحد + عملياته (تُستعمل بعد كل عملية للتحديث الفوري)."""
    sub = await session.get(WaSubscription, sub_id)
    if sub is None:
        await message.answer("لم يعد هذا الاشتراك موجوداً.")
        return
    user = await session.get(User, sub.user_id)
    name = (
        (user.full_name or user.username or str(user.telegram_id)) if user else f"#{sub.user_id}"
    )
    last_tx = await WhatsAppSectionService.last_purchase_transaction(session, sub.user_id)
    b = InlineKeyboardBuilder()
    for days in (1, 3, 7, 30):
        b.button(text=f"⏱ +{days} يوم", callback_data=f"admin:wa_ops:{sub.id}:extend:{days}")
    b.button(text="🚫 فصل الربط", callback_data="admin:wa_ops:{sub.id}:unlink")
    b.button(
        text="💵 استرداد آخر دفعة" if last_tx else "💵 لا دفعة للاسترداد",
        callback_data=f"admin:wa_ops:{sub.id}:refund",
        disabled=last_tx is None,
    )
    b.button(text="🔙 القائمة", callback_data="admin:wa_users:0")
    b.adjust(4, 2, 1)

    lines = [
        f"👤 <b>{name}</b> <code>{user.telegram_id if user else ''}</code>",
        f"• رقم الواتساب: <code>{sub.phone or '—'}</code>",
        f"• الربط: {_LINK_MARKS.get(sub.link_state, '?')} ({sub.link_state})",
        f"• مربوط منذ: {sub.connected_since or '—'}",
        f"• ساري حتى: {_fmt(sub.active_until)}",
        f"• 🔄 التجديد التلقائي: {'مفعّل' if sub.auto_renew else 'متوقف'}",
        f"• آخر باقة: {sub.last_package_days} يوم · آخر تجديد: {_fmt(sub.last_renewed_at)}",
    ]
    if sub.link_error:
        lines.append(f"• ⚠️ آخر خطأ من الجسر: <code>{sub.link_error[:200]}</code>")
    if last_tx is not None:
        lines.append(
            f"• آخر عملية شراء: {abs(Decimal(str(last_tx.amount))):g}$ "
            f"بتاريخ {last_tx.created_at:%Y-%m-%d %H:%M}"
        )
    try:
        await message.edit_text("\n".join(lines), reply_markup=b.as_markup())
    except TelegramBadRequest:
        await message.answer("\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(_DETAIL_RE)
async def wa_user_detail(callback: CallbackQuery, session):
    await _render_detail(callback.message, session, int(callback.data.split(":")[2]))
    await callback.answer()


@router.callback_query(_OPS_RE)
async def wa_user_op(callback: CallbackQuery, session):
    match = _OPS_RE.match(callback.data)
    sub_id, op, arg = int(match.group(1)), match.group(2), match.group(3)
    sub = await session.get(WaSubscription, sub_id)
    if sub is None:
        await callback.answer("الاشتراك محذوف.", show_alert=True)
        return
    user = await session.get(User, sub.user_id)

    if op == "extend":
        days = max(1, min(int(arg or 1), 365))
        await WhatsAppSectionService.extend(session, sub, days)
        await callback.answer(f"✓ مُدّد {days} يوم بلا خصم.")
        await callback.message.answer(
            f"⏱ مُدّد اشتراك <b>{user.full_name if user else sub.user_id}</b> "
            f"{days} يوم (هدية من الإدارة) — حتى {_fmt(sub.active_until)}."
        )
        await _render_detail(callback.message, session, sub_id)
        return

    if op == "unlink":
        pushed = await WhatsAppSectionService.unlink(session, sub, user)
        await callback.answer("✓")
        await callback.message.answer(
            "🚫 "
            + (
                "أُنهيت الجلسة عند البوت الثاني وأُلغي الربط."
                if pushed
                else "أُلغي الربط في بوت SD. لم يؤكد الجسر إنهاء الجلسة "
                     "(نقطة /link/unlink غير منفّذة عنده) — أوقفها عند مزودك إن كانت تعمل."
            )
        )
        await _render_detail(callback.message, session, sub_id)
        return

    if op == "refund":
        if user is None:
            await callback.answer("المستخدم غير موجود.", show_alert=True)
            return
        amount = await WhatsAppSectionService.refund_purchase(session, user, sub)
        if amount is None:
            await callback.answer(
                "لا توجد دفعة غير مستردة (أو استُردت سابقاً).", show_alert=True
            )
            return
        await callback.message.answer(
            f"💵 استُرد <b>{amount:g}$</b> وأُسقطت مدة الباقة — "
            f"رصيد المستخدم الآن {user.balance:g}$."
        )
        await _render_detail(callback.message, session, sub_id)
        return

    await callback.answer()


# ══════════════ الإحصاءات ══════════════


@router.callback_query(F.data == "admin:wa_stats")
async def wa_stats(callback: CallbackQuery, session):
    stats = await WhatsAppSectionService.stats(session)
    status_line = await _bridge_status_line()
    await callback.message.edit_text(
        "📊 <b>إحصاءات قسم واتساب</b>\n\n"
        f"• اشتراكات نشطة: {stats['active']}\n"
        f"• جلسات مربوطة: {stats['linked']}\n"
        f"• إيراد آخر 30 يوم: {stats['revenue_30d']:g}$\n\n"
        f"حالة الجسر: {status_line}",
        reply_markup=_stats_kb(),
    )
    await callback.answer()


def _stats_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙", callback_data="admin:wa_sections")
    b.adjust(1)
    return b.as_markup()

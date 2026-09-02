"""جرد حسابات المستخدمين فقط — بدون إيداعات أو طلبات الأدمن."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from filters.admin_filter import IsAdmin
from services.ledger_service import (
    DEPOSITS_PER_PAGE,
    PERIOD_LABELS,
    SERVICES_PER_PAGE,
    LedgerService,
    normalize_period,
)

router = Router(name="admin_ledger")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _period_kb(current: str, extra_rows: list | None = None):
    b = InlineKeyboardBuilder()
    labels = [
        ("d", "اليوم"),
        ("w", "7 أيام"),
        ("m", "30 يوم"),
        ("all", "الكل"),
    ]
    for key, label in labels:
        mark = "• " if key == current else ""
        b.button(text=f"{mark}{label}", callback_data=f"ld:h:{key}")
    if extra_rows:
        for text, data in extra_rows:
            b.button(text=text, callback_data=data)
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    rows = [4]
    if extra_rows:
        rows.append(len(extra_rows))
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def _list_kb(kind: str, period: str, page: int, total: int, per_page: int):
    b = InlineKeyboardBuilder()
    last = max(0, (total - 1) // per_page) if total else 0
    nav = 0
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"ld:{kind}:{period}:{page - 1}")
        nav += 1
    if page < last:
        b.button(text="التالي ▶️", callback_data=f"ld:{kind}:{period}:{page + 1}")
        nav += 1
    b.button(text="📒 ملخص الجرد", callback_data=f"ld:h:{period}")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    rows = []
    if nav:
        rows.append(nav)
    rows.extend([1, 1])
    b.adjust(*rows)
    return b.as_markup()


def _fmt(amount) -> str:
    return f"{amount:.2f}$"


def _parse(data: str, expected: int) -> list[str]:
    parts = data.split(":")
    if len(parts) < expected:
        return []
    return parts


@router.callback_query(F.data == "admin:ledger")
async def ledger_home(callback: CallbackQuery, session):
    await callback.answer()
    await _render_home(callback, session, "m")


@router.callback_query(F.data.startswith("ld:h:"))
async def ledger_home_period(callback: CallbackQuery, session):
    period = normalize_period(callback.data.split(":")[2] if ":" in callback.data else "m")
    await callback.answer()
    await _render_home(callback, session, period)


async def _render_home(callback: CallbackQuery, session, period: str) -> None:
    summary = await LedgerService.summary(session, period)
    label = PERIOD_LABELS[summary.period]
    text = (
        "📒 <b>جرد الحسابات — المستخدمون فقط</b>\n"
        f"📅 الفترة: <b>{label}</b>\n\n"
        "لا تُحتسب إيداعات الأدمن ولا طلباته إذا اشترى من البوت.\n\n"
        "━━━ 💳 شحن المستخدمين ━━━\n"
        f"الإجمالي: <b>{_fmt(summary.deposit_total)}</b>\n"
        f"عدد العمليات: {summary.deposit_count}\n\n"
        "━━━ 🛒 مبيعات المستخدمين ━━━\n"
        f"المبيعات: <b>{_fmt(summary.sales_total)}</b>\n"
        f"تكلفة المزودين: {_fmt(summary.cost_total)}\n"
        f"صافي الربح: <b>{_fmt(summary.profit_total)}</b>\n"
        f"عدد الطلبات المكتملة: {summary.order_count}\n\n"
        "━━━ 👛 أرصدة العملاء الحالية ━━━\n"
        f"عدد المستخدمين (غير أدمن): {summary.customer_count}\n"
        f"مجموع أرصدتهم: {_fmt(summary.customer_balances)}"
    )
    extra = [
        ("💳 شحنات المستخدمين", f"ld:dep:{summary.period}:0"),
        ("📦 أرباح كل خدمة", f"ld:svc:{summary.period}:0"),
    ]
    await callback.message.edit_text(
        text,
        reply_markup=_period_kb(summary.period, extra),
    )


@router.callback_query(F.data.startswith("ld:dep:"))
async def ledger_deposits(callback: CallbackQuery, session):
    parts = _parse(callback.data, 4)
    if not parts:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    period = normalize_period(parts[2])
    try:
        page = int(parts[3])
    except ValueError:
        page = 0
    rows, total = await LedgerService.list_deposits(session, period, page)
    last = max(1, (total + DEPOSITS_PER_PAGE - 1) // DEPOSITS_PER_PAGE)
    label = PERIOD_LABELS[period]
    lines = [
        "💳 <b>شحنات المستخدمين</b>",
        "بدون إيداعات الأدمن وإضافات الرصيد اليدوية.",
        f"📅 {label} · {total} عملية · صفحة {page + 1}/{last}",
        "",
    ]
    if not rows:
        lines.append("لا توجد شحنات مستخدمين في هذه الفترة.")
    else:
        for row in rows:
            uname = f"@{escape(row.username)}" if row.username else "—"
            when = row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "—"
            desc = escape(row.description or "شحن رصيد")
            lines.append(
                f"👤 {escape(row.user_name)} · {uname}\n"
                f"🆔 <code>{row.telegram_id}</code>\n"
                f"💰 <b>{_fmt(row.amount)}</b> · {when}\n"
                f"📝 {desc}\n"
            )
    await callback.answer()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_list_kb("dep", period, page, total, DEPOSITS_PER_PAGE),
    )


@router.callback_query(F.data.startswith("ld:svc:"))
async def ledger_services(callback: CallbackQuery, session):
    parts = _parse(callback.data, 4)
    if not parts:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    period = normalize_period(parts[2])
    try:
        page = int(parts[3])
    except ValueError:
        page = 0
    rows, total, sales, cost, profit = await LedgerService.service_profits(
        session, period, page
    )
    last = max(1, (total + SERVICES_PER_PAGE - 1) // SERVICES_PER_PAGE)
    label = PERIOD_LABELS[period]
    lines = [
        "📦 <b>أرباح كل خدمة</b>",
        "طلبات المستخدمين فقط — مشتريات الأدمن مستثناة.",
        f"📅 {label} · {total} خدمة · صفحة {page + 1}/{last}",
        f"Σ مبيعات {_fmt(sales)} · تكلفة {_fmt(cost)} · ربح <b>{_fmt(profit)}</b>",
        "",
    ]
    if not rows:
        lines.append("لا توجد مبيعات مستخدمين في هذه الفترة.")
    else:
        for row in rows:
            lines.append(
                f"🛒 {escape(row.name)}\n"
                f"مبيعات {_fmt(row.sales)} · مصروف {_fmt(row.cost)} · "
                f"ربح <b>{_fmt(row.profit)}</b> · {row.orders} طلب\n"
            )
    await callback.answer()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_list_kb("svc", period, page, total, SERVICES_PER_PAGE),
    )

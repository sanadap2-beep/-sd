"""لوحة صحة النظام وإعداداته بدون كشف الأسرار."""

from datetime import datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from config import settings
from database.models import ApiProvider, ProviderStatus
from filters.admin_filter import IsAdmin
from keyboards.admin import admin_health_kb
from providers.manager import provider_manager
from services.payment_method_service import payment_method_diagnostics

router = Router(name="admin_health")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _mark(value: bool) -> str:
    return "✅" if value else "⚠️"


async def _render_health(callback: CallbackQuery, session):
    sms_providers = provider_manager.get_available_providers()
    provider_rows = []

    result = await session.execute(select(ProviderStatus))
    statuses = {row.provider: row for row in result.scalars().all()}

    for provider in sms_providers:
        status = statuses.get(provider)

        if status is None:
            provider_rows.append(
                f"⚠️ {provider.value}: لا يوجد سجل حالة"
            )
            continue

        state = "متصل" if status.is_online else "غير متصل"
        checked = (
            status.last_checked_at.strftime("%Y-%m-%d %H:%M")
            if status.last_checked_at
            else "لم يُفحص بعد"
        )

        provider_rows.append(
            f"{_mark(status.is_online)} {provider.value}: "
            f"{state} ({checked})"
        )

    api_count = (
        await session.execute(select(ApiProvider.id))
    ).scalars().all()

    active_api_count = (
        await session.execute(
            select(ApiProvider.id).where(ApiProvider.is_active.is_(True))
        )
    ).scalars().all()

    diagnostics = await payment_method_diagnostics(
        (
            "shamcash_manual",
            "usdt_manual",
            "shamcash_auto",
            "usdt_auto",
        )
    )

    payment_labels = {
        "shamcash_manual": "شام كاش يدوي",
        "usdt_manual": "USDT يدوي",
        "shamcash_auto": "شام كاش تلقائي",
        "usdt_auto": "USDT تلقائي",
    }

    payment_state = [
        f"{_mark(diagnostics[method].enabled)} "
        f"{label}: {diagnostics[method].reason}"
        for method, label in payment_labels.items()
    ]

    redis_state = bool(settings.REDIS_URL)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    text = (
        "🩺 <b>صحة النظام</b>\n\n"
        f"🕐 آخر فحص: {now}\n"
        "🗄 قاعدة البيانات: ✅ متاحة\n"
        f"🧠 Redis FSM: {_mark(redis_state)} "
        f"{'مفعل' if redis_state else 'ذاكرة مؤقتة'}\n\n"
        "━━━ 📞 مزودو الأرقام ━━━\n"
    )

    text += (
        "\n".join(provider_rows)
        if provider_rows
        else "⚠️ لا يوجد مزود SMS مضبوط"
    )

    text += (
        "\n\n━━━ 🔌 مزودو المتجر ━━━\n"
        f"الإجمالي: {len(api_count)} | المفعّل: {len(active_api_count)}\n\n"
        "━━━ 💳 طرق الدفع ━━━\n"
        + "\n".join(payment_state)
        + "\n\n⚠️ الحالة تعرض وجود الإعداد فقط، ولا تعرض أي مفتاح سري."
    )

    await callback.message.edit_text(
        text,
        reply_markup=admin_health_kb(),
    )


@router.callback_query(F.data == "admin:health")
async def health_page(callback: CallbackQuery, session):
    await callback.answer()
    await _render_health(callback, session)


@router.callback_query(F.data == "admin:health:refresh")
async def health_refresh(callback: CallbackQuery, session):
    await callback.answer("🔄 تم تحديث الفحص.")
    await _render_health(callback, session)
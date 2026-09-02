"""صفحة حالة شفافة للمزودين والخدمات المتاحة."""

from datetime import datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from database.models import (
    ApiProvider,
    Category,
    NumberService,
    Product,
    ProductStatus,
    ProviderStatus,
)
from keyboards.status import status_kb
from services.i18n_service import I18nService

router = Router(name="status")


async def _render_status(target, session, db_user=None):
    language = getattr(db_user, "language_code", "ar") or "ar"
    sms_result = await session.execute(select(ProviderStatus))
    sms_statuses = list(sms_result.scalars().all())
    api_result = await session.execute(select(ApiProvider))
    api_providers = list(api_result.scalars().all())
    active_categories = (
        await session.execute(select(func.count(Category.id)).where(Category.is_active.is_(True)))
    ).scalar_one()
    active_services = (
        await session.execute(
            select(func.count(NumberService.id)).where(NumberService.is_active.is_(True))
        )
    ).scalar_one()
    active_products = (
        await session.execute(
            select(func.count(Product.id)).where(Product.status == ProductStatus.ACTIVE)
        )
    ).scalar_one()

    lines = [I18nService.t("status_title", language), ""]
    if sms_statuses:
        lines.append("━━━ 📞 مزودو SMS ━━━")
        for status in sms_statuses:
            checked = status.last_checked_at.strftime("%H:%M") if status.last_checked_at else "—"
            lines.append(
                f"{'🟢' if status.is_online else '🔴'} {status.provider.value} · آخر فحص {checked}"
            )
    if api_providers:
        lines.append("\n━━━ 🔌 مزودو API ━━━")
        for provider in api_providers:
            lines.append(
                f"{'🟢' if provider.is_active else '⚪'} "
                f"{provider.name} · {provider.total_services or 0} خدمة"
            )
    lines.extend(
        [
            "\n━━━ 📊 الكتالوج ━━━",
            f"📂 الأقسام الفعالة: {active_categories}",
            f"📞 خدمات الأرقام الفعالة: {active_services}",
            f"📦 المنتجات الفعالة: {active_products}",
            "",
            f"🕐 آخر تحديث للصفحة: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
            "يتم إيقاف الخدمة تلقائياً إذا اكتشف النظام أنها غير متاحة.",
        ]
    )
    text = "\n".join(lines)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=status_kb())
    else:
        await target.answer(text, reply_markup=status_kb())


@router.callback_query(F.data == "menu:status")
async def status_page(callback: CallbackQuery, session, db_user=None):
    await callback.answer()
    await _render_status(callback, session, db_user)


@router.message(F.text == "📡 حالة الخدمات")
async def status_message(message: Message, session, db_user=None):
    await _render_status(message, session, db_user)

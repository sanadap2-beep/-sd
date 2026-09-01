"""العروض الحية للمستخدمين."""

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from keyboards.promotions import promotions_kb
from services.promotion_service import PromotionService
from services.i18n_service import I18nService

router = Router(name="promotions")


def _discount_text(promotion) -> str:
    if promotion.discount_type.value == "percent":
        return f"{promotion.discount_value:g}%"
    return f"{promotion.discount_value:g}$"


@router.callback_query(F.data == "menu:promotions")
async def promotions_page(callback: CallbackQuery, session, db_user=None):
    language = getattr(db_user, "language_code", "ar") or "ar"
    promotions = await PromotionService.get_active_promotions(session)
    await callback.answer()
    if not promotions:
        await callback.message.edit_text(
            I18nService.t("promotions_empty", language),
            reply_markup=promotions_kb([]),
        )
        return

    lines = [I18nService.t("promotions_title", language) + "\n"]
    for promotion in promotions:
        name = promotion.product.name_ar if promotion.product else promotion.name
        lines.append(
            I18nService.t(
                "promotion_line",
                language,
                promo=escape(promotion.name),
                product=escape(name),
                discount=_discount_text(promotion),
            )
        )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=promotions_kb(promotions),
    )

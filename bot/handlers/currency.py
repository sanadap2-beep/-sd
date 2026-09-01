"""تبديل عملة العرض للمستخدم (USD/EUR/EGP/SYP).

الحسابات والخصم تبقى بالدولار دائماً؛ العملة المختارة تُستخدم فقط
لعرض الأسعار والأرصدة محوّلة حسب سعر الصرف اليومي الذي يضبطه الأدمن.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import User
from services.currency_service import CurrencyService, DISPLAY_CURRENCIES
from services.i18n_service import I18nService

router = Router(name="currency")


def _currency_name(code: str, language: str) -> str:
    meta = DISPLAY_CURRENCIES[code]
    return meta["name_en"] if language == "en" else meta["name_ar"]


def currency_kb(current: str, language: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code in DISPLAY_CURRENCIES:
        marker = "✅ " if code == current else ""
        b.button(
            text=f"{marker}{_currency_name(code, language)}",
            callback_data=f"currency:set:{code}",
        )
    b.button(
        text=I18nService.t("back_to_main", language),
        callback_data="back_to_main",
    )
    b.adjust(2, 2, 1)
    return b.as_markup()


@router.callback_query(F.data == "menu:currency")
async def currency_menu(callback: CallbackQuery, session, db_user: User):
    language = db_user.language_code
    current = CurrencyService.normalize_display_currency(db_user.display_currency)
    await callback.answer()
    await callback.message.edit_text(
        I18nService.t("currency_choose", language),
        reply_markup=currency_kb(current, language),
    )


@router.callback_query(F.data.startswith("currency:set:"))
async def currency_set(callback: CallbackQuery, session, db_user: User):
    code = callback.data.split(":", 2)[2].upper()
    if not CurrencyService.is_display_currency(code):
        await callback.answer(
            I18nService.t("currency_invalid", db_user.language_code), show_alert=True
        )
        return

    db_user.display_currency = code
    await session.commit()

    language = db_user.language_code
    await callback.answer(
        I18nService.t("currency_updated", language, currency=_currency_name(code, language))
    )
    balance_text = await CurrencyService.format_user_amount(
        db_user.balance, db_user, session
    )
    await callback.message.edit_text(
        I18nService.t(
            "currency_current",
            language,
            currency=_currency_name(code, language),
            balance=balance_text,
        ),
        reply_markup=currency_kb(code, language),
    )
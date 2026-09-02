"""
نظام الإحالة.
"""

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy import select, func

from database.models import User
from services.settings_service import SettingsService
from services.i18n_service import I18nService
from services.bot_identity import (
    referral_share_url,
    referral_start_link,
    resolve_bot_username,
)

router = Router(name="referral")


@router.message(F.text == "💎 دعوة أصدقاء")
async def referral_handler(message: Message, session, db_user: User):
    await _send_referral_info(message, session, db_user)


@router.callback_query(F.data == "menu:referral")
async def referral_handler_cb(callback: CallbackQuery, session, db_user: User):
    await callback.answer()
    await _send_referral_info(callback.message, session, db_user, bot=callback.bot)


async def _send_referral_info(message: Message, session, db_user: User, bot=None):
    bot = bot or getattr(message, "bot", None)
    username = await resolve_bot_username(bot)
    link = referral_start_link(username, db_user.telegram_id)

    result = await session.execute(
        select(func.count(User.id)).where(User.referrer_id == db_user.id)
    )
    referrals_count = result.scalar_one()

    bonus_usd = await SettingsService.get_decimal("referral_bonus_usd")
    referral_percent = await SettingsService.get_decimal("referral_percent")
    language = db_user.language_code

    if not link:
        await message.answer(
            I18nService.t("referral_link_unavailable", language),
            reply_markup=_referral_kb(language, share_url=""),
        )
        return

    share_text = I18nService.t("referral_share_text", language)
    share_url = referral_share_url(link, share_text)

    await message.answer(
        I18nService.t(
            "referral_card",
            language,
            link=link,
            bonus=f"{bonus_usd}",
            percent=f"{referral_percent}",
            count=referrals_count,
        ),
        reply_markup=_referral_kb(language, share_url),
        disable_web_page_preview=True,
    )


def _referral_kb(language: str, share_url: str) -> InlineKeyboardMarkup:
    rows = []
    if share_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=I18nService.t("referral_share_button", language),
                    url=share_url,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=I18nService.t("back_to_main", language),
                callback_data="back_to_main",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

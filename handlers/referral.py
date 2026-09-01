"""
نظام الإحالة.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select, func

from config import settings
from database.models import User
from services.settings_service import SettingsService
from services.i18n_service import I18nService
from keyboards.main_menu import back_to_main_kb

router = Router(name="referral")


@router.message(F.text == "💎 دعوة أصدقاء")
async def referral_handler(message: Message, session, db_user: User):
    await _send_referral_info(message, session, db_user)


@router.callback_query(F.data == "menu:referral")
async def referral_handler_cb(callback: CallbackQuery, session, db_user: User):
    await callback.answer()
    await _send_referral_info(callback.message, session, db_user)


async def _send_referral_info(message: Message, session, db_user: User):
    link = f"https://t.me/{settings.BOT_USERNAME}?start=ref_{db_user.telegram_id}"

    result = await session.execute(
        select(func.count(User.id)).where(User.referrer_id == db_user.id)
    )
    referrals_count = result.scalar_one()

    bonus_usd = await SettingsService.get_decimal("referral_bonus_usd")
    referral_percent = await SettingsService.get_decimal("referral_percent")

    await message.answer(
        I18nService.t(
            "referral_card",
            db_user.language_code,
            link=link,
            bonus=f"{bonus_usd}",
            percent=f"{referral_percent}",
            count=referrals_count,
        ),
        reply_markup=back_to_main_kb(db_user.language_code),
    )

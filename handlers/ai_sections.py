"""
القسم الرئيسي للذكاء الاصطناعي — واجهة المستخدم.

- ai:home → قائمة الأقسام الفعالة (برمجة/دردشة/...)
- كل قسم: وصفه + سعر الرسالة → جلسة جديدة أو متابعة → يرسل طلبه
  فيُخصم الرصيد ويُرد عليه (رد قسم البرمجة يُرسل كملف/ملفات).
- الجلسات محفوظة ويعود لها المستخدم من «جلستي».

التكلفة: سعر المزود + ربح (مضاعف مضبوط من اللوحة، الافتراضي 3×).
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from database.models import AiSession, User
from keyboards.ai_sections import (
    ai_error_kb,
    ai_hist_view_kb,
    ai_history_kb,
    ai_home_kb,
    ai_insufficient_kb,
    ai_prompt_kb,
    ai_section_kb,
)
from services.ai_section_service import (
    AiConversationService,
    AiSectionError,
    AiSectionService,
    extract_code_artifacts,
    files_to_zip,
)
from services.balance_service import InsufficientBalanceError
from services.feature_service import FeatureService
from services.i18n_service import I18nService
from states.states import AiSectionStates

logger = logging.getLogger(__name__)
router = Router(name="ai_sections")

_OPEN_RE = re.compile(r"^ai:open:(\d+)$")
_ACTION_RE = re.compile(r"^ai:(new|stay):(\d+)$")
_CONTINUE_RE = re.compile(r"^ai:continue:(\d+)$")
_HISTORY_RE = re.compile(r"^ai:history:(\d+)$")
_HIST_VIEW_RE = re.compile(r"^ai:hist_view:(\d+)$")


def _lang(db_user) -> str:
    return getattr(db_user, "language_code", "ar") or "ar"


async def _feature_on() -> bool:
    return await FeatureService.enabled("ai_sections")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _section_name(section, language: str) -> str:
    if language == "en" and section.name_en:
        return section.name_en
    return section.name_ar


def _section_desc(section, language: str) -> str:
    if language == "en" and section.description_en:
        return section.description_en
    return section.description_ar or ""


async def _show_home(callback: CallbackQuery, session):
    language = _lang(callback.from_user)
    sections = await AiSectionService.list_enabled(session)
    if not sections:
        await callback.message.edit_text(I18nService.t("ai_no_sections", language))
        await callback.answer()
        return
    await callback.message.edit_text(
        I18nService.t("ai_home_title", language),
        reply_markup=ai_home_kb(sections, language),
    )
    await callback.answer()


async def _show_section(callback: CallbackQuery, session, db_user: User, section):
    language = _lang(db_user)
    price = AiSectionService.sell_price(section)
    cost = section.cost_per_message_usd
    has_session = (
        await AiConversationService.get_session(session, db_user, section) is not None
    )
    name = _section_name(section, language)
    description = _section_desc(section, language)
    kind_note = (
        I18nService.t("ai_kind_coding_note", language) if section.kind == "coding" else ""
    )
    text = (
        f"🤖 <b>{_escape(name)}</b>\n\n"
        + (f"{_escape(description)}\n\n" if description else "")
        + kind_note
        + f"\n💰 {I18nService.t('ai_cost_line', language, price=f'{price:g}$', cost=f'{cost:g}$')}\n"
        + f"👛 {I18nService.t('ai_balance_now', language, balance=f'{db_user.balance:g}$')}"
    )
    await callback.message.edit_text(
        text, reply_markup=ai_section_kb(section.id, has_session, language)
    )
    await callback.answer()


@router.callback_query(F.data == "ai:home")
async def ai_home(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("ai_disabled", _lang(db_user)), show_alert=True)
        return
    await _show_home(callback, session)


@router.callback_query(F.data.regexp(_OPEN_RE))
async def ai_open(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("ai_disabled", _lang(db_user)), show_alert=True)
        return
    section_id = int(callback.data.split(":")[2])
    section = await AiSectionService.get(session, section_id)
    if section is None or not section.enabled:
        await _show_home(callback, session)
        return
    await _show_section(callback, session, db_user, section)


@router.callback_query(F.data.regexp(_ACTION_RE))
async def ai_new_or_stay(
    callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None
):
    """بدء جلسة جديدة (ai:new) أو إكمال الجلسة الحالية (ai:stay)."""
    if not await _feature_on():
        await callback.answer(I18nService.t("ai_disabled", _lang(db_user)), show_alert=True)
        return
    _action, section_id = callback.data.split(":")[1], int(callback.data.split(":")[2])
    section = await AiSectionService.get(session, section_id)
    if section is None or not section.enabled:
        await _show_home(callback, session)
        return

    language = _lang(db_user)
    if _action == "new":
        ai_session = AiSession(user_id=db_user.id, section_id=section.id)
        session.add(ai_session)
        await session.flush()
    else:
        ai_session = await AiConversationService.get_session(session, db_user, section)
        if ai_session is None:
            ai_session = await AiConversationService.ensure_session(session, db_user, section)

    price = AiSectionService.sell_price(section)
    await state.set_state(AiSectionStates.waiting_prompt)
    await state.update_data(section_id=section.id, session_id=ai_session.id)
    await callback.message.edit_text(
        I18nService.t(
            "ai_write_prompt",
            language,
            name=_section_name(section, language),
            price=f"{price:g}$",
        )
    )
    await callback.answer()


@router.callback_query(F.data.regexp(_CONTINUE_RE))
async def ai_continue(
    callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None
):
    """متابعة آخر جلسة: يعرض آخر رددين ثم ينتظر رسالة جديدة."""
    if not await _feature_on():
        await callback.answer(I18nService.t("ai_disabled", _lang(db_user)), show_alert=True)
        return
    section_id = int(callback.data.split(":")[2])
    section = await AiSectionService.get(session, section_id)
    if section is None or not section.enabled:
        await _show_home(callback, session)
        return
    ai_session = await AiConversationService.get_session(session, db_user, section)
    if ai_session is None:
        await callback.answer(I18nService.t("ai_no_sessions", _lang(db_user)), show_alert=True)
        return

    language = _lang(db_user)
    price = AiSectionService.sell_price(section)
    await state.set_state(AiSectionStates.waiting_prompt)
    await state.update_data(section_id=section.id, session_id=ai_session.id)

    last = await AiConversationService.latest_messages(session, ai_session, limit=4)
    preview = ""
    for row in last[-2:]:
        marker = "👤" if row.role == "user" else "🤖"
        preview += f"{marker} {_escape(row.content[:200])}\n\n"
    await callback.message.edit_text(
        I18nService.t("ai_continue_header", language, name=_section_name(section, language))
        + (f"\n{preview}" if preview else "")
        + f"\n{I18nService.t('ai_write_prompt_short', language, price=f'{price:g}$')}",
    )
    await callback.answer()


@router.callback_query(F.data == "ai:cancel")
async def ai_cancel(callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None):
    await state.clear()
    if not await _feature_on():
        await callback.answer()
        return
    await _show_home(callback, session)


@router.callback_query(F.data.regexp(_HISTORY_RE))
async def ai_history(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("ai_disabled", _lang(db_user)), show_alert=True)
        return
    section_id = int(callback.data.split(":")[2])
    section = await AiSectionService.get(session, section_id)
    if section is None:
        await callback.answer("؟", show_alert=True)
        return
    sessions = await AiConversationService.sessions_for(session, db_user.id, section.id, limit=10)
    if not sessions:
        await callback.message.edit_text(
            I18nService.t("ai_no_sessions", _lang(db_user)),
            reply_markup=ai_section_kb(section.id, False, _lang(db_user)),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        I18nService.t(
            "ai_history_title", _lang(db_user), name=_section_name(section, _lang(db_user))
        ),
        reply_markup=ai_history_kb(section.id, sessions, _lang(db_user)),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(_HIST_VIEW_RE))
async def ai_hist_view(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("ai_disabled", _lang(db_user)), show_alert=True)
        return
    ai_session_id = int(callback.data.split(":")[2])
    ai_session = await session.get(AiSession, ai_session_id)
    if ai_session is None or ai_session.user_id != db_user.id:
        await callback.answer("؟", show_alert=True)
        return
    messages = await AiConversationService.latest_messages(session, ai_session, limit=12)
    lines = [f"📜 {_escape(ai_session.title or '…')}\n"]
    for row in messages:
        marker = "👤 أنت:" if row.role == "user" else "🤖 البوت:"
        lines.append(f"{marker}\n{_escape(row.content[:700])}\n")
    await callback.message.edit_text(
        "\n".join(lines)[:4000],
        reply_markup=ai_hist_view_kb(ai_session.section_id, ai_session.id, _lang(db_user)),
    )
    await callback.answer()


# ══════════════ رسالة المستخدم داخل القسم ══════════════


@router.message(AiSectionStates.waiting_prompt, F.text)
async def ai_prompt_handler(
    message: Message, state: FSMContext, session, db_user: User | None = None
):
    """المستخدم أرسل طلبه داخل قسم ذكاء اصطناعي."""
    data = await state.get_data()
    section_id = data.get("section_id")
    if not section_id or db_user is None:
        await state.clear()
        return
    section = await AiSectionService.get(session, section_id)
    if section is None or not section.enabled:
        await state.clear()
        await message.answer(I18nService.t("ai_disabled", _lang(db_user)))
        return

    language = _lang(db_user)
    text = message.text.strip()
    if not text or len(text) > 8000:
        await message.answer(I18nService.t("ai_prompt_invalid", language))
        return

    try:
        result = await AiConversationService.process_message(session, db_user, section, text)
    except InsufficientBalanceError:
        price = AiSectionService.sell_price(section)
        await state.clear()
        await message.answer(
            I18nService.t("ai_insufficient", language, price=f"{price:g}$"),
            reply_markup=ai_insufficient_kb(language),
        )
        return
    except AiSectionError as exc:
        await state.clear()
        await message.answer(str(exc))
        return

    if not result.get("ok"):
        # المبلغ رُجع للمستخدم داخل الخدمة — نعرض الخطأ مع إمكانية الإعادة.
        await message.answer(
            I18nService.t("ai_error_provider", language),
            reply_markup=ai_error_kb(section.id, language),
        )
        return

    response_text: str = result["text"]
    files: list[tuple[str, bytes]] = result.get("files") or []

    if section.kind == "coding":
        explanation, artifacts = extract_code_artifacts(response_text)
        files = [(name, content.encode("utf-8")) for name, content in artifacts]
        visible = explanation if explanation else response_text
    else:
        visible = response_text

    kb = ai_prompt_kb(section.id, language)
    if not visible:
        visible = (
            I18nService.t("ai_files_ready", language, name=_section_name(section, language))
            if files
            else I18nService.t("ai_empty_reply", language)
        )
    await message.answer(
        (visible[:4000] + "\n…") if len(visible) > 4000 else visible,
        reply_markup=kb,
    )
    if files:
        for name, content in files[:10]:
            await message.bot.send_document(
                message.chat.id,
                BufferedInputFile(content, filename=name),
                caption=f"📎 {_section_name(section, language)}"[:100],
            )
        if len(files) > 1:
            archive = files_to_zip(files)
            await message.bot.send_document(
                message.chat.id,
                BufferedInputFile(archive, filename="project.zip"),
                caption="📦"[:100],
            )

    # البقاء في حالة الانتظار لإرسال رسالة جديدة بنفس الجلسة.
    await state.update_data(section_id=section.id)


@router.message(AiSectionStates.waiting_prompt)
async def ai_prompt_non_text(message: Message, state: FSMContext, db_user: User | None = None):
    """داخل القسم نعالج النص فقط."""
    await message.answer(I18nService.t("ai_prompt_text_only", _lang(db_user)))

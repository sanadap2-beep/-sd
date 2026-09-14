"""
واجهة المستخدم لأقسام الذكاء الاصطناعي.

المسار: زر «🤖 الذكاء الاصطناعي» في القائمة الرئيسية ← قائمة الأقسام
(برمجة بدون قيود، دردشة بدون قيود، وأي قسم يضيفه الأدمن لاحقاً).

- قسم دردشة: رسالة ↔ رسالة مع سياق الجلسة، والخصم لكل رسالة.
- قسم برمجة: المستخدم يطلب كوداً/أداة/ملفاً كاملاً ← يصل الرد **كملف**
  (لأن تيليجرام يقصّ الرسائل الطويلة) مع الاسم والامتداد المناسبين.
- كل شيء يُحفظ في جلسات يمكن الرجوع إليها لاحقاً من «🗂 جلساتي».
"""

from __future__ import annotations

import html as html_module
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from database.models import AISectionMode, User
from services.ai_sections_service import (
    AISectionError,
    AISectionService,
    AISessionService,
    guess_extension,
    run_turn,
)
from services.balance_service import InsufficientBalanceError
from keyboards.ai_sections import (
    chat_active_kb,
    ai_home_kb,
    section_view_kb,
    session_view_kb,
    sessions_list_kb,
)
from states.states import AISectionStates

router = Router(name="ai_sections")

MAX_TEXT_CHUNK = 3800
MAX_SESSION_PREVIEW = 12


# ══════════════ أدوات عرض ══════════════


def md_to_telegram_html(text: str) -> str:
    """
    تحويل ماركداون خفيف من الموديل إلى HTML تيليجرام:
    ```code fences``` → <pre>، `inline` → <code>، **bold** → <b>.
    كل شيء يُهرَّب أولاً — الحماية من كسر التنسيق.
    """
    escaped = html_module.escape(text or "")

    def _fence(match: re.Match) -> str:
        return "<pre>" + match.group(1) + "</pre>"

    escaped = re.sub(r"```[A-Za-z0-9_+#.\-]*\n?(.*?)```", _fence, escaped, flags=re.S)
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def split_for_telegram(text: str, limit: int = MAX_TEXT_CHUNK) -> list[str]:
    chunks: list[str] = []
    current = text
    while len(current) > limit:
        cut = current.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(current[:cut])
        current = current[cut:].lstrip("\n")
    if current or not chunks:
        chunks.append(current)
    return chunks


def _footer(paid, balance) -> str:
    return f"\n\n💸 خصم هذه الرسالة: <b>${paid}</b> | رصيدك: <b>${balance}</b>"


async def _get_section_or_alert(callback: CallbackQuery, section_id: int):
    section = None
    # الجلسة تمرر من الـ middleware لكن نحتاج استعلاماً هنا
    from database.engine import async_session_maker

    async with async_session_maker() as db:
        section = await AISectionService.get(db, section_id)
    if section is None or not section.is_enabled:
        await callback.answer("هذا القسم غير متاح حالياً.", show_alert=True)
        return None
    return section


# ══════════════ القائمة الرئيسية للأقسام ══════════════


@router.callback_query(F.data == "ai:home")
async def ai_home(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    await state.clear()
    sections = await AISectionService.available_for_users(session)
    if not sections:
        configured = (
            await AISectionService.list_enabled(session)
        )
        if configured:
            text = (
                "🤖 <b>الذكاء الاصطناعي</b>\n\n"
                "الأقسام موجودة لكن المزود غير مهيأ بعد. راجع الإدارة."
            )
        else:
            text = (
                "🤖 <b>الذكاء الاصطناعي</b>\n\n"
                "لا توجد أقسام مفعّلة حالياً. عد لاحقاً 👋"
            )
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="back_to_main")]
                ]
            ),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "🤖 <b>الذكاء الاصطناعي</b>\n\n"
        "اختر القسم الذي تريده. كل قسم له موديل وسعر خاص به،\n"
        "وكل رسالة تُخصم من رصيدك عند إرسالها.",
        reply_markup=ai_home_kb(sections),
    )
    await callback.answer()


# ══════════════ شاشة القسم ══════════════


@router.callback_query(F.data.startswith("ai:sec:"))
async def ai_section_view(callback: CallbackQuery, state: FSMContext, session):
    await state.clear()
    section_id = int(callback.data.split(":")[2])
    section = await AISectionService.get(session, section_id)
    if section is None or not section.is_enabled:
        await callback.answer("هذا القسم غير متاح حالياً.", show_alert=True)
        return

    mode_line = (
        "👨‍💻 <b>قسم برمجة:</b> اطلب أي كود أو أداة أو ملف كامل، وسيتوصلك النتيجة "
        "<b>كملف جاهز للتحميل</b> (لأن تيليجرام يقصّ النصوص الطويلة)."
        if section.mode == AISectionMode.CODE
        else "💬 <b>دردشة حرة بدون قيود:</b> اسأل عن أي شيء وسيجيبك الموديل "
        "مع تذكّر سياق الجلسة الحالية."
    )
    description = f"\n📝 {section.description}\n" if section.description else ""
    await callback.message.edit_text(
        f"{section.emoji} <b>{section.title}</b>\n"
        f"{description}\n"
        f"{mode_line}\n\n"
        f"🧠 الموديل: <code>{section.model}</code>\n"
        f"💸 التسعير: {AISectionService.price_label(section)}",
        reply_markup=section_view_kb(section),
    )
    await callback.answer()


# ══════════════ بدء محادثة/طلب ══════════════


async def _enter_mode(
    target: Message | CallbackQuery,
    state: FSMContext,
    session,
    db_user: User,
    section,
    ai_session=None,
):
    mode = AISectionStates.coding if section.mode == AISectionMode.CODE else AISectionStates.chatting
    ai_session = ai_session or await AISessionService.get_or_create(session, db_user.id, section)
    await state.set_state(mode)
    await state.update_data(ai_section_id=section.id, ai_session_id=ai_session.id)

    if section.mode == AISectionMode.CODE:
        text = (
            f"👨‍💻 <b>{section.title}</b>\n\n"
            "اكتب طلبك الآن — كود، سكربت، أداة، أو ملف كامل بأي لغة.\n"
            "مثال:\n"
            "<code>اكتبلي بوت تيليجرام كامل يبيع أرقام مع لوحة أدمن</code>\n\n"
            f"💸 التسعير: {AISectionService.price_label(section)}\n"
            "النتيجة توصلك كملف بعد كل طلب. أرسل طلباً جديداً في أي وقت."
        )
    else:
        text = (
            f"💬 <b>{section.title}</b>\n\n"
            "أنت الآن داخل محادثة. اسأل أي شيء — كل رسالة تُخصم تلقائياً.\n"
            f"💸 التسعير: {AISectionService.price_label(section)}"
        )

    kb = chat_active_kb(section)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ai:start:"))
async def ai_start(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    section_id = int(callback.data.split(":")[2])
    section = await AISectionService.get(session, section_id)
    if section is None or not section.is_enabled:
        await callback.answer("هذا القسم غير متاح حالياً.", show_alert=True)
        return
    await _enter_mode(callback, state, session, db_user, section)


@router.callback_query(F.data.startswith("ai:new:"))
async def ai_new_session(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    section_id = int(callback.data.split(":")[2])
    section = await AISectionService.get(session, section_id)
    if section is None or not section.is_enabled:
        await callback.answer("هذا القسم غير متاح حالياً.", show_alert=True)
        return
    fresh = await AISessionService.new_session(session, db_user.id, section)
    await _enter_mode(callback, state, session, db_user, section, ai_session=fresh)
    await callback.answer("🆕 جلسة جديدة جاهزة")


@router.callback_query(F.data.startswith("ai:resume:"))
async def ai_resume_session(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    session_id = int(callback.data.split(":")[2])
    ai_session = await AISessionService.get_user_session(session, session_id, db_user.id)
    if ai_session is None:
        await callback.answer("الجلسة غير موجودة.", show_alert=True)
        return
    section = await AISectionService.get(session, ai_session.section_id)
    if section is None or not section.is_enabled:
        await callback.answer("هذا القسم غير متاح حالياً.", show_alert=True)
        return
    await _enter_mode(callback, state, session, db_user, section, ai_session=ai_session)


# ══════════════ معالجة رسائل المستخدم ══════════════


async def _process_turn(message: Message, state: FSMContext, session, db_user: User, as_file: bool):
    data = await state.get_data()
    section = await AISectionService.get(session, data.get("ai_section_id", 0))
    if section is None or not section.is_enabled:
        await state.clear()
        await message.answer("هذا القسم غير متاح حالياً.")
        return
    ai_session = await AISessionService.get_or_create(
        session, db_user.id, section, data.get("ai_session_id")
    )

    user_text = (message.text or message.caption or "").strip()
    if not user_text:
        await message.answer("⚠️ أرسل طلبك كتابة.")
        return

    thinking = await message.answer("⏳ جاري المعالجة...")
    try:
        result = await run_turn(session, db_user, section, user_text, ai_session=ai_session)
    except InsufficientBalanceError as exc:
        await thinking.edit_text(f"❌ {exc}\n\n💰 اشحن رصيدك من زر «شحن رصيد» بالقائمة.")
        return
    except AISectionError as exc:
        await thinking.edit_text(f"❌ {exc}")
        return

    kb = chat_active_kb(section)
    # رصيد المستخدم بعد الخصم
    await session.refresh(db_user)
    balance = db_user.balance

    if as_file:
        ext = guess_extension(result["text"])
        filename = f"ai_code_{result['ai_session'].id}_{result['model'].split('/')[-1][:20]}.{ext}"
        safe_name = re.sub(r"[^A-Za-z0-9_.\-]", "_", filename)
        caption = (
            f"✅ <b>جاهز!</b> طلبك: <i>{html_module.escape(user_text[:80])}</i>\n"
            f"🧠 الموديل: <code>{result['model']}</code>"
            f"{_footer(result['paid'], balance)}"
        )
        document = BufferedInputFile(result["text"].encode("utf-8"), filename=safe_name)
        await thinking.delete()
        await message.answer_document(document, caption=caption, reply_markup=kb)
    else:
        body = md_to_telegram_html(result["text"])
        parts = split_for_telegram(body)
        await thinking.edit_text(parts[0] + _footer(result["paid"], balance), reply_markup=kb)
        for extra in parts[1:]:
            await message.answer(extra)


@router.message(AISectionStates.chatting, F.text)
async def ai_chat_message(message: Message, state: FSMContext, session, db_user: User):
    await _process_turn(message, state, session, db_user, as_file=False)


@router.message(AISectionStates.coding, F.text)
async def ai_code_message(message: Message, state: FSMContext, session, db_user: User):
    await _process_turn(message, state, session, db_user, as_file=True)


@router.message(AISectionStates.chatting)
@router.message(AISectionStates.coding)
async def ai_non_text_message(message: Message):
    await message.answer("⚠️ أرسل طلبك كتابة (نص فقط).")


# ══════════════ الجلسات السابقة ══════════════


@router.callback_query(F.data.startswith("ai:sessions:"))
async def ai_sessions_list(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    await state.clear()
    section_id = int(callback.data.split(":")[2])
    section = await AISectionService.get(session, section_id)
    if section is None:
        await callback.answer("القسم غير موجود.", show_alert=True)
        return
    sessions = await AISessionService.list_for_user(session, db_user.id, section_id)
    if not sessions:
        await callback.message.edit_text(
            f"🗂 لا توجد جلسات سابقة في «{section.title}» بعد.\n"
            "ابدأ أول جلسة الآن 👇",
            reply_markup=section_view_kb(section),
        )
        await callback.answer()
        return

    lines = [f"🗂 <b>جلساتك في «{section.title}»</b>\n"]
    for s in sessions:
        lines.append(
            f"• <b>{html_module.escape((s.title or 'جلسة')[:48])}</b> — "
            f"{s.messages_count} رسالة | دُفع ${s.charged_total}"
        )
    await callback.message.edit_text(
        "\n".join(lines) + "\n\nاضغط على جلسة لعرض رسائلها أو متابعتها:",
        reply_markup=sessions_list_kb(section, sessions),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai:view:"))
async def ai_session_view(callback: CallbackQuery, session, db_user: User):
    session_id = int(callback.data.split(":")[2])
    ai_session = await AISessionService.get_user_session(session, session_id, db_user.id)
    if ai_session is None:
        await callback.answer("الجلسة غير موجودة.", show_alert=True)
        return
    section = await AISectionService.get(session, ai_session.section_id)
    messages = await AISessionService.get_messages(session, ai_session)

    lines = [
        f"📄 <b>{html_module.escape((ai_session.title or 'جلسة')[:60])}</b>",
        f"🧠 {section.model if section else ''} | "
        f"{ai_session.messages_count} رسالة | تكلفة المزود ${ai_session.provider_cost} "
        f"| دُفع ${ai_session.charged_total}",
        "",
    ]
    shown = messages[-MAX_SESSION_PREVIEW:]
    for m in shown:
        icon = "🧑" if m.role.value == "user" else "🤖"
        preview = (m.content or "").strip().replace("\n", " ")[:220]
        lines.append(f"{icon} {html_module.escape(preview)}")
    if len(messages) > MAX_SESSION_PREVIEW:
        lines.append(f"\n… و{len(messages) - MAX_SESSION_PREVIEW} رسالة أقدم.")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=session_view_kb(ai_session),
    )
    await callback.answer()

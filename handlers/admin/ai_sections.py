"""
لوحة الأدمن: أقسام الذكاء الاصطناعي.

من هنا يضيف الأدمن أقساماً جديدة (برمجة بدون قيود، دردشة بدون قيود،
وأي قسم مستقبلي)، يربط كل قسم بموديل NanoGPT، يكتب شرحه وسعره التقريبي
يدوياً، يضبط مضاعف الربح، يدير مفتاح المزود، ويستعرض جلسات المستخدمين
ورسائلهم للمراجعة.
"""

from __future__ import annotations

import html as html_module
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from sqlalchemy import desc, select

from database.models import (
    AISection,
    AISectionMode,
    AIPricingMode,
    AISession,
    User,
)
from filters.admin_filter import IsAdmin
from keyboards.ai_sections_admin import (
    ai_admin_home_kb,
    ai_admin_section_kb,
    ai_admin_wizard_mode_kb,
    ai_admin_wizard_pricing_kb,
    ai_admin_wizard_start_kb,
)
from services.ai_sections_service import (
    AISectionService,
    AISessionService,
    global_ai_stats,
)
from services.audit_service import AuditAction, AuditService
from services.nanogpt_service import NanoGPTService
from services.settings_service import SettingsService
from states.states import AdminAISectionStates

router = Router(name="admin_ai_sections")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MODE_LABELS = {
    AISectionMode.CODE: "👨‍💻 برمجة (الرد كملف)",
    AISectionMode.CHAT: "💬 دردشة حرة",
    AISectionMode.CUSTOM: "🧩 مخصص",
}
PRICING_LABELS = {
    AIPricingMode.USAGE: "حسب استهلاك المزود × مضاعف",
    AIPricingMode.FIXED: "سعر ثابت للرسالة",
}


def _fmt_decimal(value) -> str:
    try:
        return f"{Decimal(str(value)):g}"
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


async def _render_home(callback: CallbackQuery, session) -> None:
    configured = await NanoGPTService.configured()
    sections = await AISectionService.list_all(session)
    stats = await global_ai_stats(session)

    key_line = "🔑 مفتاح NanoGPT: <b>مضبوط ✅</b>" if configured else "🔑 مفتاح NanoGPT: <b>غير مضبوط ❌</b> (الأقسام مخفية عن المستخدمين)"
    lines = [
        "🤖 <b>إدارة أقسام الذكاء الاصطناعي</b>",
        "",
        key_line,
        f"📂 الأقسام: <b>{len(sections)}</b> | المفعّل: <b>{sum(1 for s in sections if s.is_enabled)}</b>",
        "",
        f"📊 الجلسات: <b>{stats['sessions']}</b> | الرسائل: <b>{stats['messages']}</b>",
        f"💵 تكلفة المزود: <b>${stats['provider_cost']}</b> | المُحصّل: <b>${stats['charged']}</b> | الربح: <b>${stats['profit']}</b>",
        "",
        "💡 كل قسم: موديل خاص + شرح تكتبه يدوياً + تسعير "
        "(تكلفة المزود × مضاعف الربح، أو سعر ثابت).",
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=ai_admin_home_kb(sections))
    await callback.answer()


@router.callback_query(F.data == "admin:ai")
async def ai_admin_home(callback: CallbackQuery, session):
    await _render_home(callback, session)


# ══════════════ معالج إضافة قسم جديد (Wizard) ══════════════


@router.callback_query(F.data == "admin:ai:new")
async def ai_wizard_start(callback: CallbackQuery, state: FSMContext, session):
    await state.set_state(AdminAISectionStates.wizard_title)
    await state.update_data(wiz={})
    await callback.message.edit_text(
        "➕ <b>إضافة قسم ذكاء اصطناعي جديد</b>\n\n"
        "1/9 — أرسل <b>عنوان القسم</b> كما سيظهر للمستخدم:\n"
        "<i>مثال: برمجة بدون قيود</i>",
        reply_markup=ai_admin_wizard_start_kb(),
    )
    await callback.answer()


async def _cancel_wizard(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ أُلغي إنشاء القسم.")


@router.message(AdminAISectionStates.wizard_title, F.text)
async def wiz_title(message: Message, state: FSMContext):
    if message.text.strip() == "إلغاء":
        await _cancel_wizard(message, state)
        return
    data = await state.get_data()
    wiz = data.get("wiz", {})
    wiz["title"] = message.text.strip()[:64]
    await state.update_data(wiz=wiz)
    await state.set_state(AdminAISectionStates.wizard_emoji)
    await message.answer(
        "2/9 — أرسل <b>إيموجي القسم</b>:\n<i>مثال: 👨‍💻</i>"
    )


@router.message(AdminAISectionStates.wizard_emoji, F.text)
async def wiz_emoji(message: Message, state: FSMContext):
    data = await state.get_data()
    wiz = data.get("wiz", {})
    wiz["emoji"] = message.text.strip()[:8] or "🤖"
    await state.update_data(wiz=wiz)
    await state.set_state(AdminAISectionStates.wizard_description)
    await message.answer(
        "3/9 — اكتب <b>شرح القسم</b> (مهمته وماذا يفعل) — يظهر للمستخدم في شاشة القسم:\n"
        "<i>مثال: اطلب أي كود أو أداة وستوصلك النتيجة كملف جاهز.</i>"
    )


@router.message(AdminAISectionStates.wizard_description, F.text)
async def wiz_description(message: Message, state: FSMContext):
    data = await state.get_data()
    wiz = data.get("wiz", {})
    wiz["description"] = message.text.strip()[:1500]
    await state.update_data(wiz=wiz)
    await state.set_state(AdminAISectionStates.wizard_mode)
    await message.answer(
        "4/9 — اختر <b>نوع القسم</b>:\n"
        "• برمجة: الرد ينرسل للمستخدم <b>كملف</b>\n"
        "• دردشة: الرد نصي حواري\n"
        "• مخصص: نصي حواري (لأي فكرة مستقبلية)",
        reply_markup=ai_admin_wizard_mode_kb(),
    )


@router.callback_query(AdminAISectionStates.wizard_mode, F.data.startswith("aiw:mode:"))
async def wiz_mode(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(":")[2]
    data = await state.get_data()
    wiz = data.get("wiz", {})
    wiz["mode"] = mode
    await state.update_data(wiz=wiz)
    await state.set_state(AdminAISectionStates.wizard_model)
    await callback.message.edit_text(
        "5/9 — أرسل <b>اسم الموديل في NanoGPT</b> لهذا القسم:\n"
        "<i>أمثلة:</i>\n<code>openai/gpt-5.2</code>\n<code>anthropic/claude-sonnet-4.6</code>\n<code>z-ai/glm-4.6</code>\n\n"
        "شوف الأسعار والأسماء من: nano-gpt.com/models",
    )
    await callback.answer()


@router.message(AdminAISectionStates.wizard_model, F.text)
async def wiz_model(message: Message, state: FSMContext):
    data = await state.get_data()
    wiz = data.get("wiz", {})
    wiz["model"] = message.text.strip()[:128]
    await state.update_data(wiz=wiz)
    await state.set_state(AdminAISectionStates.wizard_system_prompt)
    await message.answer(
        "6/9 — أرسل <b>الـ System Prompt</b> لهذا القسم (شخصية الموديل وتعليماته).\n"
        "أرسل <code>-</code> إذا أردت الافتراضي بدون تعليمات خاصة."
    )


@router.message(AdminAISectionStates.wizard_system_prompt, F.text)
async def wiz_system_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    wiz = data.get("wiz", {})
    text = message.text.strip()
    wiz["system_prompt"] = None if text == "-" else text[:8000]
    await state.update_data(wiz=wiz)
    await state.set_state(AdminAISectionStates.wizard_pricing_mode)
    await message.answer(
        "7/9 — اختر <b>طريقة التسعير</b>:\n"
        "• حسب الاستهلاك: يخصم (تكلفة المزود الفعلية × المضاعف) — الأدق.\n"
        "• سعر ثابت: مبلغ محدد لكل رسالة.",
        reply_markup=ai_admin_wizard_pricing_kb(),
    )


@router.callback_query(AdminAISectionStates.wizard_pricing_mode, F.data.startswith("aiw:pricing:"))
async def wiz_pricing_mode(callback: CallbackQuery, state: FSMContext):
    pricing = callback.data.split(":")[2]
    data = await state.get_data()
    wiz = data.get("wiz", {})
    wiz["pricing_mode"] = pricing
    await state.update_data(wiz=wiz)
    if pricing == "fixed":
        await state.set_state(AdminAISectionStates.wizard_fixed_price)
        await callback.message.edit_text(
            "أرسل <b>سعر الرسالة الثابت بالدولار</b>:\n<i>مثال: 0.02</i>"
        )
    else:
        await state.set_state(AdminAISectionStates.wizard_est_cost)
        await callback.message.edit_text(
            "أرسل <b>التكلفة التقريبية للرسالة الواحدة عند المزود بالدولار</b>.\n"
            "تُستخدم للخصم المسبق، وبعد كل رد تُحسب التكلفة الفعلية التي يرجعها "
            "NanoGPT وتُسوّى تلقائياً (زيادة تُخصم، نقص يُرجع).\n<i>مثال: 0.003</i>"
        )
    await callback.answer()


async def _wiz_read_decimal(message: Message, state: FSMContext, field: str, prompt: str, next_state, extra: dict | None = None):
    raw = (message.text or "").strip().replace("$", "")
    try:
        value = Decimal(raw)
        if value < 0 or not value.is_finite():
            raise InvalidOperation
    except (InvalidOperation, TypeError):
        await message.answer("⚠️ أرسل رقماً صحيحاً موجباً. " + prompt)
        return False
    data = await state.get_data()
    wiz = data.get("wiz", {})
    wiz[field] = str(value)
    if extra:
        wiz.update(extra)
    await state.update_data(wiz=wiz)
    await state.set_state(next_state)
    return True


@router.message(AdminAISectionStates.wizard_fixed_price, F.text)
async def wiz_fixed_price(message: Message, state: FSMContext):
    ok = await _wiz_read_decimal(
        message, state, "fixed_price", "مثال: 0.02",
        AdminAISectionStates.wizard_multiplier,
    )
    if ok:
        await message.answer(
            "9/9 — أرسل <b>مضاعف الربح</b> (يُطبّق على التكلفة الفعلية للتسوية أيضاً).\n"
            "3 = المستخدم يدفع 3 أضعاف تكلفة المزود. أرسل رقماً مثل <code>3</code>"
        )


@router.message(AdminAISectionStates.wizard_est_cost, F.text)
async def wiz_est_cost(message: Message, state: FSMContext):
    ok = await _wiz_read_decimal(
        message, state, "est_cost_per_message", "مثال: 0.003",
        AdminAISectionStates.wizard_multiplier,
    )
    if ok:
        await message.answer(
            "9/9 — أرسل <b>مضاعف الربح</b>.\n"
            "الخصم = (تكلفة المزود) × المضاعف → مضاعف 3 يعني ربح 3 أضعاف. أرسل مثل <code>3</code>"
        )


@router.message(AdminAISectionStates.wizard_multiplier, F.text)
async def wiz_multiplier(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    try:
        multiplier = Decimal(raw)
        if multiplier < 1 or not multiplier.is_finite():
            raise InvalidOperation
    except (InvalidOperation, TypeError):
        await message.answer("⚠️ أرسل رقماً ≥ 1. مثال: 3")
        return

    data = await state.get_data()
    wiz = data.get("wiz", {})
    section = await AISectionService.create(
        session,
        title=wiz.get("title", "قسم جديد"),
        model=wiz.get("model", "openai/gpt-4o-mini"),
        mode=AISectionMode(wiz.get("mode", "chat")),
        emoji=wiz.get("emoji", "🤖"),
        description=wiz.get("description"),
        system_prompt=wiz.get("system_prompt"),
        pricing_mode=AIPricingMode(wiz.get("pricing_mode", "usage")),
        est_cost_per_message=Decimal(wiz.get("est_cost_per_message", "0.003")),
        fixed_price=Decimal(wiz.get("fixed_price", "0.01")),
        profit_multiplier=multiplier,
    )
    await state.clear()
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="ai_section",
        entity_name=section.title,
        new_value={"model": section.model, "mode": section.mode.value, "pricing": section.pricing_mode.value, "multiplier": str(multiplier)},
        description="إنشاء قسم ذكاء اصطناعي",
        session=session,
    )
    await message.answer(
        f"✅ تم إنشاء القسم <b>{section.emoji} {section.title}</b>\n"
        f"🧠 الموديل: <code>{section.model}</code>\n"
        f"💸 {AISectionService.price_label(section)}\n\n"
        "القسم مفعّل ويظهر للمستخدمين فوراً (بعد ضبط مفتاح NanoGPT إن لم يكن مضبوطاً).",
        reply_markup=ai_admin_section_kb(section),
    )


# ══════════════ تفاصيل قسم وتعديله ══════════════


EDITABLE_FIELDS = {
    "title": "العنوان",
    "emoji": "الإيموجي",
    "description": "الشرح",
    "model": "الموديل",
    "system_prompt": "System Prompt (أرسل - للإلغاء)",
    "est_cost_per_message": "التكلفة التقريبية للرسالة ($)",
    "fixed_price": "السعر الثابت ($)",
    "profit_multiplier": "مضاعف الربح (≥1)",
}


@router.callback_query(F.data.startswith("admin:ai:sec:"))
async def ai_admin_section_view(callback: CallbackQuery, session):
    section_id = int(callback.data.split(":")[3])
    section = await AISectionService.get(session, section_id)
    if section is None:
        await callback.answer("القسم غير موجود.", show_alert=True)
        return
    status = "🟢 مفعّل" if section.is_enabled else "🔴 موقوف"
    prompt_line = "موجود" if section.system_prompt else "بدون"
    await callback.message.edit_text(
        f"{section.emoji} <b>{section.title}</b> — {status}\n\n"
        f"📝 الشرح: {html_module.escape((section.description or '—')[:300])}\n"
        f"🧠 الموديل: <code>{section.model}</code>\n"
        f"🧩 النوع: {MODE_LABELS.get(section.mode, section.mode.value)}\n"
        f"📜 System Prompt: {prompt_line}\n"
        f"💳 التسعير: {PRICING_LABELS.get(section.pricing_mode, '')}\n"
        f"• تكلفة تقريبية/رسالة: <b>${_fmt_decimal(section.est_cost_per_message)}</b>\n"
        f"• سعر ثابت: <b>${_fmt_decimal(section.fixed_price)}</b>\n"
        f"• مضاعف الربح: <b>×{_fmt_decimal(section.profit_multiplier)}</b>\n"
        f"🧾 {AISectionService.price_label(section)}",
        reply_markup=ai_admin_section_kb(section),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:ai:toggle:"))
async def ai_admin_section_toggle(callback: CallbackQuery, session, db_user: User):
    section_id = int(callback.data.split(":")[3])
    section = await AISectionService.toggle(session, section_id)
    if section is None:
        await callback.answer("القسم غير موجود.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="ai_section",
        entity_name=section.title,
        new_value={"is_enabled": section.is_enabled},
        description="تفعيل/تعطيل قسم ذكاء اصطناعي",
        session=session,
    )
    await callback.answer("تم الحفظ ✅")
    await ai_admin_section_view(callback, session)


@router.callback_query(F.data.startswith("admin:ai:del:"))
async def ai_admin_section_delete(callback: CallbackQuery, session, db_user: User):
    section_id = int(callback.data.split(":")[3])
    deleted = await AISectionService.delete(session, section_id)
    if not deleted:
        await callback.answer("القسم غير موجود.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.DELETE,
        entity_type="ai_section",
        entity_name=str(section_id),
        description="حذف قسم ذكاء اصطناعي",
        session=session,
    )
    await callback.answer("🗑 تم حذف القسم.", show_alert=True)
    await _render_home(callback, session)


@router.callback_query(F.data.startswith("admin:ai:edit:"))
async def ai_admin_section_edit(callback: CallbackQuery, state: FSMContext):
    section_id = int(callback.data.split(":")[3])
    await state.set_state(AdminAISectionStates.edit_field_value)
    await state.update_data(edit_section_id=section_id)
    buttons = "\n".join(f"• <code>{key}</code> — {label}" for key, label in EDITABLE_FIELDS.items())
    await callback.message.edit_text(
        f"✏️ <b>تعديل القسم</b>\n\nأرسل: <code>الحقل: القيمة الجديدة</code>\n\nالحقول:\n{buttons}\n\n"
        "مثال:\n<code>model: openai/gpt-5.2</code>\n<code>profit_multiplier: 3.5</code>\n\n"
        "أرسل <code>تم</code> للانتهاء.",
    )
    await callback.answer()


@router.message(AdminAISectionStates.edit_field_value, F.text)
async def ai_admin_section_edit_value(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    if raw.lower() in ("تم", "done"):
        await state.clear()
        await message.answer("✅ انتهى التعديل.")
        return
    if ":" not in raw:
        await message.answer("⚠️ الصيغة: <code>الحقل: القيمة</code> — أرسل <code>تم</code> للانتهاء.")
        return
    field, _, value = raw.partition(":")
    field = field.strip()
    value = value.strip()
    data = await state.get_data()
    section_id = data.get("edit_section_id")
    if section_id is None:
        await state.clear()
        await message.answer("❌ انتهت جلسة التعديل، افتح القسم من جديد.")
        return
    section = await AISectionService.get(session, int(section_id))
    if section is None:
        await state.clear()
        await message.answer("❌ القسم غير موجود.")
        return

    if field not in EDITABLE_FIELDS:
        await message.answer(f"⚠️ حقل غير معروف: {field}")
        return
    if field in ("est_cost_per_message", "fixed_price", "profit_multiplier"):
        try:
            number = Decimal(value)
            if number < 0 or not number.is_finite():
                raise InvalidOperation
            if field == "profit_multiplier" and number < 1:
                raise InvalidOperation
        except (InvalidOperation, TypeError):
            await message.answer("⚠️ أرسل رقماً صحيحاً.")
            return
        setattr(section, field, number)
    elif field == "system_prompt":
        setattr(section, field, None if value == "-" else value[:8000])
    else:
        setattr(section, field, value[:1500])

    await session.commit()
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="ai_section",
        entity_name=section.title,
        new_value={field: value[:200]},
        description="تعديل حقل في قسم ذكاء اصطناعي",
        session=session,
    )
    await message.answer(f"✅ تم تحديث «{EDITABLE_FIELDS[field]}».")


# ══════════════ مفتاح NanoGPT ══════════════


@router.callback_query(F.data == "admin:ai:key")
async def ai_admin_key_start(callback: CallbackQuery, state: FSMContext):
    current = await SettingsService.get("nanogpt_api_key", "")
    masked = f"{current[:6]}…{current[-4:]}" if len(current or "") > 12 else ("مضبوط" if current else "غير مضبوط")
    await state.set_state(AdminAISectionStates.waiting_api_key)
    await callback.message.edit_text(
        f"🔑 <b>مفتاح NanoGPT الحالي:</b> {masked}\n\n"
        "أرسل المفتاح الجديد (من nano-gpt.com/api).\n"
        "أرسل <code>-</code> لحذف المفتاح، أو <code>إلغاء</code> للتراجع.",
    )
    await callback.answer()


@router.message(AdminAISectionStates.waiting_api_key, F.text)
async def ai_admin_key_save(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    if raw == "إلغاء":
        await state.clear()
        await message.answer("❌ تم التراجع.")
        return
    await state.clear()
    if raw == "-":
        await SettingsService.set(session, "nanogpt_api_key", "")
        await message.answer("🗑 تم حذف المفتاح.")
        return
    await SettingsService.set(session, "nanogpt_api_key", raw)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="setting",
        entity_name="nanogpt_api_key",
        description="تحديث مفتاح NanoGPT",
        session=session,
    )
    status = await message.answer("⏳ جاري اختبار المفتاح...")
    ok, report = await NanoGPTService.test_key()
    await status.edit_text(("✅ " if ok else "❌ ") + report)


# ══════════════ استعراض جلسات المستخدمين ══════════════


@router.callback_query(F.data == "admin:ai:users")
async def ai_admin_users_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminAISectionStates.waiting_user_search)
    await callback.message.edit_text(
        "🧾 <b>جلسات المستخدمين</b>\n\nأرسل <b>آيدي تيليجرام</b> للمستخدم لعرض جلساته ورسائله.\nأرسل <code>إلغاء</code> للتراجع."
    )
    await callback.answer()


@router.message(AdminAISectionStates.waiting_user_search, F.text)
async def ai_admin_users_search(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw == "إلغاء":
        await state.clear()
        await message.answer("❌ تم التراجع.")
        return
    if not raw.isdigit():
        await message.answer("⚠️ أرسل آيدي رقمي مثل <code>123456789</code>.")
        return
    await state.clear()

    from sqlalchemy import select

    user = (
        await session.execute(select(User).where(User.telegram_id == int(raw)))
    ).scalar_one_or_none()
    if user is None:
        await message.answer("❌ لا يوجد مستخدم بهذا الآيدي.")
        return

    from sqlalchemy import desc as _desc
    from database.models import AISession as AISessionModel, AISection as AISectionModel

    sessions_result = await session.execute(
        select(AISessionModel, AISectionModel)
        .join(AISectionModel, AISectionModel.id == AISessionModel.section_id)
        .where(AISessionModel.user_id == user.id)
        .order_by(_desc(AISessionModel.updated_at), _desc(AISessionModel.id))
        .limit(10)
    )
    rows = sessions_result.all()
    if not rows:
        await message.answer(f"📭 المستخدم <b>{user.full_name or raw}</b> ليس لديه جلسات ذكاء اصطناعي.")
        return

    lines = [f"🧾 <b>آخر جلسات</b> {html_module.escape(user.full_name or raw)}:\n"]
    for ai_session, section in rows:
        lines.append(
            f"• [{ai_session.id}] {section.emoji} {html_module.escape(section.title)} — "
            f"«{html_module.escape((ai_session.title or 'جلسة')[:36])}» "
            f"({ai_session.messages_count} رسالة | دُفع ${ai_session.charged_total})"
        )
    lines.append("\nلعرض رسائل جلسة أرسل: <code>جلسة: الآيدي</code>")
    await state.set_state(AdminAISectionStates.waiting_user_search)
    await state.update_data(viewing_user=user.id)
    await message.answer("\n".join(lines))


# (يُستدعى مباشرة من ai_admin_users_search عند "جلسة: <id>")
async def ai_admin_session_messages(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    session_id = int(raw.split(":")[1])
    ai_session = await session.get(AISessionModel := __import__(
        "database.models", fromlist=["AISession"]
    ).AISession, session_id)
    if ai_session is None:
        await message.answer("❌ جلسة غير موجودة.")
        return
    messages = await AISessionService.get_messages(session, ai_session)
    lines = [
        f"📄 جلسة #{ai_session.id} — {ai_session.messages_count} رسالة | "
        f"تكلفة المزود ${ai_session.provider_cost} | دُفع ${ai_session.charged_total}\n"
    ]
    for m in messages[-14:]:
        icon = "🧑" if m.role.value == "user" else "🤖"
        lines.append(f"{icon} {(m.content or '')[:280]}".replace("\n", " "))
        lines.append("")
    text = "\n".join(lines) or "لا رسائل."
    for start in range(0, len(text), 3800):
        await message.answer(html_module.escape(text[start:start + 3800]))

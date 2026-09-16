"""
إدارة القسم الرئيسي للذكاء الاصطناعي من لوحة الأدمن.

- 🧩 الأقسام: إنشاء/تعديل/تفعيل/تعطيل.
  كل قسم: معرّف + اسم + وصف يدوي + نوع (برمجة/دردشة) + موديل NanoGPT
  + تكلفة الرسالة التقريبية + مضاعف الربح (الافتراضي 3×).
- 🔌 المزود: عنوان الـ API + المفتاح + اختبار اتصال.
- 📊 الإحصاءات: رسائل/تكلفة/إيراد/ربح لكل قسم.
"""

from __future__ import annotations

import re
import time
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from filters.admin_filter import IsAdmin
from keyboards.ai_sections import (
    admin_ai_kind_kb,
    admin_ai_list_kb,
    admin_ai_menu_kb,
    admin_ai_provider_kb,
    admin_ai_section_kb,
)
from services.ai_provider_client import (
    AiProviderError,
    chat_completion,
    configured,
    get_provider_config,
)
from services.ai_section_service import (
    AiConversationService,
    AiSectionError,
    AiSectionService,
)
from services.settings_service import SettingsService
from states.states import AdminAiProviderStates, AdminAiSectionStates

router = Router(name="admin_ai_sections")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_EDIT_RE = re.compile(r"^admin:ai_edit:(\d+)$")
_TOGGLE_RE = re.compile(r"^admin:ai_toggle:(\d+)$")
_KIND_RE = re.compile(r"^admin:ai_kind(?::edit:\d+)?:(\w+)$")


# ══════════════ القائمة الرئيسية للقسم ══════════════


@router.callback_query(F.data == "admin:ai_sections")
async def ai_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🤖 <b>إدارة قسم الذكاء الاصطناعي</b>\n\n"
        "أنشئ الأقسام (برمجة/دردشة/مستقبلية) واضبط موديل كل قسم وتكلفة "
        "رسالته. سعر الرسالة للمستخدم = التكلفة + ربح (3× افتراضياً).\n"
        "المستخدمون يشاهدون القسم في القائمة الرئيسية عند توفر قسم مفعّل.",
        reply_markup=admin_ai_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:ai_list")
async def ai_list(callback: CallbackQuery, session):
    sections = await AiSectionService.list_all(session)
    await callback.message.edit_text(
        "🧩 <b>أقسام الذكاء الاصطناعي</b>\n\n"
        "🟢 مفعّل | 🔴 معطّل\n"
        "اضغط القسم لتعديله أو الزر المجاور للتفعيل/التعطيل.",
        reply_markup=admin_ai_list_kb(sections),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(_TOGGLE_RE))
async def ai_toggle(callback: CallbackQuery, session):
    section_id = int(callback.data.split(":")[2])
    section = await AiSectionService.get(session, section_id)
    if section is None:
        await callback.answer("القسم غير موجود.", show_alert=True)
        return
    await AiSectionService.toggle(session, section)
    await callback.message.edit_text(
        "🧩 <b>أقسام الذكاء الاصطناعي</b>",
        reply_markup=admin_ai_list_kb(await AiSectionService.list_all(session)),
    )
    await callback.answer("تم التفعيل." if section.enabled else "تم التعطيل.")


# ══════════════ إنشاء/تعديل قسم ══════════════


@router.callback_query(F.data == "admin:ai_new")
async def ai_new_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ <b>إضافة قسم ذكاء اصطناعي</b>\n\n"
        "1/8) أرسل معرّف القسم (إنجليزي بدون مسافات، مثال: <code>ai_coding</code>):"
    )
    await state.set_state(AdminAiSectionStates.waiting_key)
    await callback.answer()


@router.callback_query(F.data.regexp(_EDIT_RE))
async def ai_edit_start(callback: CallbackQuery, session, state: FSMContext):
    section_id = int(callback.data.split(":")[2])
    section = await AiSectionService.get(session, section_id)
    if section is None:
        await callback.answer("القسم غير موجود.", show_alert=True)
        return
    await state.update_data(section_id=section_id)
    await callback.message.edit_text(
        f"✏️ <b>تعديل قسم: {section.name_ar}</b>\n\n"
        f"المعرّف الحالي: <code>{section.key}</code>\n"
        f"الموديل الحالي: <code>{section.model}</code>\n"
        f"التكلفة: {section.cost_per_message_usd}$ · الربح ×{section.profit_multiplier}\n\n"
        "1/8) أرسل المعرّف الجديد (أو أرسل <code>-</code> للإبقاء على الحالي):"
    )
    await state.set_state(AdminAiSectionStates.waiting_key)
    await callback.answer()


@router.message(AdminAiSectionStates.waiting_key)
async def ai_key_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    data = await state.get_data()
    section_id = data.get("section_id")
    keep_key = bool(section_id) and raw == "-"

    key = raw
    if not keep_key:
        if not re.fullmatch(r"[a-z0-9_]{3,40}", raw):
            await message.answer(
                "⚠️ المعرّف: 3-40 رمزاً، أحرف إنجليزية صغيرة وأرقام و _ فقط "
                "(مثال: ai_chat)."
            )
            return
        if not section_id:
            existing = await AiSectionService.get_by_key(session, raw)
            if existing:
                await message.answer(f"⚠️ يوجد قسم بهذا المعرّف مسبقاً: {raw}")
                return
    else:
        section = await AiSectionService.get(session, int(section_id))
        key = section.key if section else raw

    await state.update_data(key=key)
    await message.answer("2/8) أرسل <b>الاسم بالعربية</b> (هذا ما يظهر للمستخدم):")
    await state.set_state(AdminAiSectionStates.waiting_name_ar)


@router.message(AdminAiSectionStates.waiting_name_ar)
async def ai_name_ar_received(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if len(raw) < 2 or len(raw) > 120:
        await message.answer("⚠️ الاسم: بين 2 و 120 حرفاً.")
        return
    await state.update_data(name_ar=raw)
    await message.answer(
        "3/8) أرسل <b>الاسم بالإنجليزية</b> (اختياري — أرسل <code>-</code> للتخطي):"
    )
    await state.set_state(AdminAiSectionStates.waiting_name_en)


@router.message(AdminAiSectionStates.waiting_name_en)
async def ai_name_en_received(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw == "-":
        raw = ""
    await state.update_data(name_en=raw or None)
    await message.answer(
        "4/8) اختر <b>نوع القسم</b>:\n"
        "💻 برمجة = يولد كوداً/أداة كاملة ويرسلها <b>كملف</b> "
        "(تليجرام فيه حد رسائل فالكود الطويل يتلخبط).\n"
        "💬 دردشة = محادثة عادية بدون قيود.",
        reply_markup=admin_ai_kind_kb(None),
    )
    await state.set_state(AdminAiSectionStates.waiting_kind)


@router.message(AdminAiSectionStates.waiting_kind)
async def ai_kind_text_reject(message: Message):
    """نوع القسم يُختار بالزر أعلاه فقط."""
    await message.answer("⚠️ اختر النوع من الأزرار أعلاه (💻 برمجة أو 💬 دردشة).")


@router.callback_query(F.data.regexp(_KIND_RE))
async def ai_kind_selected(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.split(":")[-1]
    if kind not in ("coding", "chat"):
        await callback.answer("؟", show_alert=True)
        return
    await state.update_data(kind=kind)
    await callback.message.edit_text(
        "5/8) أرسل <b>اسم الموديل</b> عند NanoGPT لهذا القسم "
        "(مثال: <code>openai/gpt-5.6-sol</code> — اختره من كتالوج nano-gpt.com):"
    )
    await state.set_state(AdminAiSectionStates.waiting_model)
    await callback.answer()


@router.message(AdminAiSectionStates.waiting_model)
async def ai_model_received(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if len(raw) < 2 or len(raw) > 120:
        await message.answer("⚠️ أرسل اسم موديل صحيح (2-120 رمزاً).")
        return
    await state.update_data(model=raw)
    await message.answer(
        "6/8) أرسل <b>تكلفة الرسالة التقريبية عند المزود</b> بالدولار "
        "(مثال: <code>0.01</code> — تظهر للأدمن في الإحصاءات فقط):"
    )
    await state.set_state(AdminAiSectionStates.waiting_cost)


@router.message(AdminAiSectionStates.waiting_cost)
async def ai_cost_received(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    try:
        cost = Decimal(raw)
        if cost <= 0 or cost > Decimal("10"):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ أرسل رقماً موجباً بالدولار (مثال: 0.01).")
        return
    await state.update_data(cost=cost)
    await message.answer(
        "7/8) أرسل <b>مضاعف الربح</b> (الافتراضي <code>3</code> = ربح 3 أضعاف التكلفة).\n"
        "سعر الرسالة للمستخدم = التكلفة × (1 + المضاعف). "
        "مثال: تكلفة 0.01$ × 4 = 0.04$: "
    )
    await state.set_state(AdminAiSectionStates.waiting_multiplier)


@router.message(AdminAiSectionStates.waiting_multiplier)
async def ai_multiplier_received(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw in ("", "-"):
        raw = "3"
    try:
        mult = float(raw)
        if mult < 0 or mult > 100:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل رقماً بين 0 و 100 (مثال: 3).")
        return
    await state.update_data(multiplier=mult)
    await message.answer(
        "8/8 (أ) أرسل <b>الوصف/الشرح بالعربية</b>:\n"
        "اشرح يدوياً ماذا يسوي هذا القسم وما مهمته (يظهر للمستخدم داخله).\n"
        "أرسل <code>-</code> إذا ما بدك وصف:"
    )
    await state.set_state(AdminAiSectionStates.waiting_desc_ar)


@router.message(AdminAiSectionStates.waiting_desc_ar)
async def ai_desc_ar_received(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw == "-":
        raw = ""
    await state.update_data(desc_ar=raw or None)
    await message.answer(
        "8/8 (ب) أرسل <b>الوصف بالإنجليزية</b> (اختياري — أرسل <code>-</code> للتخطي):"
    )
    await state.set_state(AdminAiSectionStates.waiting_desc_en)


@router.message(AdminAiSectionStates.waiting_desc_en)
async def ai_desc_en_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw == "-":
        raw = ""
    data = await state.get_data()
    await state.clear()

    try:
        if data.get("section_id"):
            section = await AiSectionService.get(session, int(data["section_id"]))
            if section is None:
                await message.answer("❌ القسم لم يعد موجوداً.")
                return
            section = await AiSectionService.update(
                session,
                section,
                key=data.get("key") or section.key,
                name_ar=data["name_ar"],
                name_en=data.get("name_en") or None,
                kind=data.get("kind") or section.kind,
                model=data["model"],
                cost_per_message_usd=Decimal(str(data["cost"])),
                profit_multiplier=data["multiplier"],
                description_ar=data.get("desc_ar"),
                description_en=raw or None,
            )
            verb = "✓ عُدّل"
        else:
            section = await AiSectionService.create(
                session,
                key=data["key"],
                name_ar=data["name_ar"],
                name_en=data.get("name_en") or None,
                kind=data.get("kind") or "chat",
                model=data["model"],
                cost_per_message_usd=Decimal(str(data["cost"])),
                profit_multiplier=data["multiplier"],
                description_ar=data.get("desc_ar"),
                description_en=raw or None,
                enabled=False,
            )
            verb = "✓ أُنشئ"
    except (AiSectionError, InvalidOperation, ValueError, KeyError) as exc:
        await message.answer(f"❌ {exc}")
        return

    price = AiSectionService.sell_price(section)
    await message.answer(
        f"{verb} القسم <b>{section.name_ar}</b>\n\n"
        f"• المعرّف: <code>{section.key}</code>\n"
        f"• النوع: {'💻 برمجة (ملفات)' if section.kind == 'coding' else '💬 دردشة'}\n"
        f"• الموديل: <code>{section.model}</code>\n"
        f"• تكلفة الرسالة عند المزود: {section.cost_per_message_usd}$\n"
        f"• ربح ×{section.profit_multiplier} → سعر المستخدم: <b>{price}$</b>\n\n"
        "القسم الآن <b>معطّل</b>. فعّله من قائمة الأقسام بعد التأكد من المفتاح "
        "والموديل (🔌 المزود ← 🧪 اختبار الاتصال).",
        reply_markup=admin_ai_section_kb(section.id),
    )


# ══════════════ المزود (NanoGPT) ══════════════


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"{'*' * 8}{key[-4:]}"


@router.callback_query(F.data == "admin:ai_provider")
async def ai_provider_screen(callback: CallbackQuery):
    base_url, api_key = await get_provider_config()
    state = "🟢 مضبوط" if api_key else "🔴 لا يوجد مفتاح (ضع NANOGPT_API_KEY أو أرسله هنا)"
    await callback.message.edit_text(
        "🔌 <b>مزود NanoGPT</b>\n\n"
        f"عنوان الـ API:\n<code>{base_url}</code>\n\n"
        f"المفتاح: <code>{_mask_key(api_key) if api_key else '—'}</code>\n"
        f"الحالة: {state}\n\n"
        "المفتاح هنا (لوحة الأدمن) يسري على كل الأقسام. الأولوية له على "
        "قيمة ملف البيئة NANOGPT_API_KEY.",
        reply_markup=admin_ai_provider_kb(bool(api_key)),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:ai_prov_url")
async def ai_prov_url_start(callback: CallbackQuery, state: FSMContext):
    base_url, _key = await get_provider_config()
    await callback.message.edit_text(
        "✏️ أرسل عنوان الـ API الجديد (ينتهي بـ /v1 عادةً):\n"
        f"الحالي: <code>{base_url}</code>\n"
        "أرسل <code>-</code> للإبقاء على الحالي:"
    )
    await state.set_state(AdminAiProviderStates.waiting_base_url)
    await callback.answer()


@router.message(AdminAiProviderStates.waiting_base_url)
async def ai_prov_url_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        if not re.match(r"^https?://[\w.-]+(/[\w./-]*)?$", raw):
            await message.answer("⚠️ أرسل رابطاً صحيحاً (http/https).")
            return
        await SettingsService.set(session, "ai_provider_base_url", raw.rstrip("/"))
    await state.clear()
    await message.answer("✓ تم.", reply_markup=admin_ai_provider_kb(True))


@router.callback_query(F.data == "admin:ai_prov_key")
async def ai_prov_key_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔑 أرسل مفتاح NanoGPT الجديد:\n"
        "من لوحة nano-gpt.com ← API Keys. أرسل <code>-</code> للإبقاء على الحالي:"
    )
    await state.set_state(AdminAiProviderStates.waiting_api_key)
    await callback.answer()


@router.message(AdminAiProviderStates.waiting_api_key)
async def ai_prov_key_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw != "-":
        if len(raw) < 8:
            await message.answer("⚠️ المفتاح يبدو قصيراً — تأكد منه وأرسله مرة أخرى.")
            return
        await SettingsService.set(session, "ai_provider_api_key", raw)
    await state.clear()
    await message.answer("✓ تم حفظ المفتاح.", reply_markup=admin_ai_provider_kb(True))


@router.callback_query(F.data == "admin:ai_prov_test")
async def ai_prov_test(callback: CallbackQuery, session):
    if not await configured():
        await callback.answer("لا يوجد مفتاح بعد.", show_alert=True)
        return
    sections = await AiSectionService.list_all(session)
    enabled = [s for s in sections if s.enabled]
    model = (enabled or sections or [None])[0].model if (enabled or sections) else None
    if not model:
        await callback.answer("أنشئ قسماً أولاً حتى يختبر الاتصال بموديله.", show_alert=True)
        return
    await callback.answer("جارٍ الاختبار...")
    started = time.monotonic()
    try:
        result = await chat_completion(
            [{"role": "user", "content": "ping"}],
            model,
            max_tokens=8,
            temperature=0,
            timeout_seconds=60,
        )
    except AiProviderError as exc:
        await callback.message.answer(f"❌ <b>فشل الاختبار</b>\n\n{exc}")
        return
    elapsed = time.monotonic() - started
    await callback.message.answer(
        f"✅ <b>نجح الاتصال</b>\n\n"
        f"الموديل: <code>{model}</code>\n"
        f"الزمن: {elapsed:.1f} ثانية\n"
        f"الرد: <code>{result.text[:120]}</code>"
    )


# ══════════════ الإحصاءات ══════════════


@router.callback_query(F.data == "admin:ai_stats")
async def ai_stats(callback: CallbackQuery, session):
    stats = await AiConversationService.stats(session)
    lines = ["📊 <b>إحصاءات الذكاء الاصطناعي</b>\n"]
    for row in stats["sections"]:
        lines.append(
            f"🧩 {row['name_ar']}\n"
            f"   رسائل: {row['messages']} · تكلفة المزود: {row['cost']:g}$ · "
            f"إيراد: {row['revenue']:g}$ · ربح: {row['profit']:g}$\n"
        )
    if not stats["sections"]:
        lines.append("لا توجد رسائل بعد.")
    lines.append(
        f"\n<b>الإجمالي:</b> {stats['total_messages']} رسالة · "
        f"تكلفة {stats['total_cost']:g}$ · إيراد {stats['total_revenue']:g}$ · "
        f"ربح <b>{stats['total_profit']:g}$</b>"
    )
    await callback.message.edit_text("\n".join(lines))
    await callback.answer()

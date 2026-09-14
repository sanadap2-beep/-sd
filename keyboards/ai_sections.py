"""
كيبوردات أقسام الذكاء الاصطناعي وقسم واتساب (جهة المستخدم).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import AISection, AISession, WALinkStatus, WhatsAppLink


def ai_home_kb(sections: list[AISection]) -> InlineKeyboardMarkup:
    """قائمة الأقسام المفعّلة — كل قسم زر."""
    b = InlineKeyboardBuilder()
    for section in sections:
        b.button(
            text=f"{section.emoji} {section.title}",
            callback_data=f"ai:sec:{section.id}",
            style="primary",
        )
    b.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def section_view_kb(section: AISection) -> InlineKeyboardMarkup:
    """شاشة القسم: زر البدء + الجلسات + رجوع."""
    b = InlineKeyboardBuilder()
    if section.mode.value == "code":
        b.button(text="👨‍💻 اطلب كود / ملف", callback_data=f"ai:start:{section.id}", style="success")
        b.button(text="🗂 طلباتي السابقة", callback_data=f"ai:sessions:{section.id}")
    else:
        b.button(text="💬 ابدأ المحادثة", callback_data=f"ai:start:{section.id}", style="success")
        b.button(text="🗂 جلساتي السابقة", callback_data=f"ai:sessions:{section.id}")
    b.button(text="🔙 أقسام الذكاء الاصطناعي", callback_data="ai:home")
    b.adjust(1)
    return b.as_markup()


def chat_active_kb(section: AISection) -> InlineKeyboardMarkup:
    """أزرار أثناء المحادثة/البرمجة (تُرفق مع كل رد)."""
    b = InlineKeyboardBuilder()
    b.button(text="🆕 جلسة جديدة", callback_data=f"ai:new:{section.id}", style="primary")
    b.button(text="🗂 الجلسات", callback_data=f"ai:sessions:{section.id}")
    b.button(text="🔙 خروج", callback_data="ai:home", style="danger")
    b.adjust(2, 1, 1)
    return b.as_markup()


def sessions_list_kb(
    section: AISection, sessions: list[AISession]
) -> InlineKeyboardMarkup:
    """قائمة جلسات المستخدم في قسم معيّن — يمكن فتح أي جلسة."""
    b = InlineKeyboardBuilder()
    for s in sessions:
        label = (s.title or "جلسة")[:40]
        b.button(
            text=f"📄 {label} ({s.messages_count} رسالة)",
            callback_data=f"ai:view:{s.id}",
        )
    b.button(text="🆕 جلسة جديدة", callback_data=f"ai:new:{section.id}", style="success")
    b.button(text="🔙 {0}".format(section.title), callback_data=f"ai:sec:{section.id}")
    b.adjust(1)
    return b.as_markup()


def session_view_kb(session: AISession) -> InlineKeyboardMarkup:
    """عرض جلسة سابقة: متابعة + رجوع لقائمة الجلسات."""
    b = InlineKeyboardBuilder()
    b.button(
        text="▶️ متابعة هذه الجلسة",
        callback_data=f"ai:resume:{session.id}",
        style="success",
    )
    b.button(
        text="🔙 كل الجلسات",
        callback_data=f"ai:sessions:{session.section_id}",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ قسم واتساب ══════════════


def wa_home_active_kb() -> InlineKeyboardMarkup:
    """القسم مفعّل والمستخدم مشترك."""
    b = InlineKeyboardBuilder()
    b.button(text="📲 ربط رقم واتساب", callback_data="wa:link", style="success")
    b.button(text="🧭 أوامر واتساب", callback_data="wa:menu", style="primary")
    b.button(text="🔌 حالة الاتصال", callback_data="wa:status")
    b.button(text="🔓 فصل الرقم", callback_data="wa:unlink", style="danger")
    b.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def wa_home_inactive_kb(has_linked: bool = False) -> InlineKeyboardMarkup:
    """غير مشترك أو القسم يحتاج اشتراك."""
    b = InlineKeyboardBuilder()
    b.button(text="✅ اشترك يوم بـ 1$ ", callback_data="wa:subscribe", style="success")
    if has_linked:
        b.button(text="🔌 حالة الاتصال", callback_data="wa:status")
    b.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def wa_pairing_kb() -> InlineKeyboardMarkup:
    """بعد إرسال كود الاقتران."""
    b = InlineKeyboardBuilder()
    b.button(text="🔄 تحققت، افحص الحالة", callback_data="wa:status", style="primary")
    b.button(text="🧭 أوامر واتساب", callback_data="wa:menu")
    b.button(text="🔙 قسم واتساب", callback_data="wa:home")
    b.adjust(1)
    return b.as_markup()


def wa_bridge_menu_kb(link: WhatsAppLink) -> InlineKeyboardMarkup:
    """
    يعرض أزرار البوت الثاني القادمة من الجسر.
    كل زر يحمل فهرس عمله؛ يُترجم عند الضغط إلى الـ action المحفوظ.
    """
    b = InlineKeyboardBuilder()
    buttons = []
    try:
        import json

        data = json.loads(link.last_menu_json or "{}")
        buttons = [item for item in (data.get("buttons") or []) if item.get("text") and item.get("action")]
    except (ValueError, TypeError):
        buttons = []
    for index, item in enumerate(buttons[:32]):
        b.button(
            text=str(item.get("text"))[:64],
            callback_data=f"wa:go:{link.id}:{index}",
        )
    b.button(text="🔄 تحديث القائمة", callback_data="wa:menu", style="primary")
    b.button(text="🔙 قسم واتساب", callback_data="wa:home")
    b.adjust(1)
    return b.as_markup()


def wa_status_kb(link_status: WALinkStatus) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if link_status == WALinkStatus.PENDING:
        b.button(text="🔄 فحص مرة أخرى", callback_data="wa:status", style="primary")
        b.button(text="🧭 أوامر واتساب", callback_data="wa:menu")
    elif link_status == WALinkStatus.LINKED:
        b.button(text="🧭 أوامر واتساب", callback_data="wa:menu", style="success")
    else:
        b.button(text="📲 ربط رقم جديد", callback_data="wa:link", style="primary")
    b.button(text="🔙 قسم واتساب", callback_data="wa:home")
    b.adjust(1)
    return b.as_markup()

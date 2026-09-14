"""كيبوردات لوحة الأدمن لأقسام الذكاء الاصطناعي وجسر واتساب."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import AISection


def ai_admin_home_kb(sections: list[AISection]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for section in sections:
        status = "🟢" if section.is_enabled else "🔴"
        b.button(
            text=f"{status} {section.emoji} {section.title}",
            callback_data=f"admin:ai:sec:{section.id}",
        )
    if sections:
        b.adjust(1)
    b.button(text="➕ قسم جديد", callback_data="admin:ai:new", style="success")
    b.button(text="🔑 مفتاح NanoGPT", callback_data="admin:ai:key", style="primary")
    b.button(text="🧾 جلسات المستخدمين", callback_data="admin:ai:users")
    b.button(text="📱 إعدادات قسم واتساب", callback_data="admin:wa")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main", style="danger")
    b.adjust(1)
    return b.as_markup()


def ai_admin_section_kb(section: AISection) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle_label = "⏸ إيقاف القسم" if section.is_enabled else "▶️ تفعيل القسم"
    b.button(text=toggle_label, callback_data=f"admin:ai:toggle:{section.id}", style="primary")
    b.button(text="✏️ تعديل الحقول", callback_data=f"admin:ai:edit:{section.id}")
    b.button(text="🗑 حذف القسم", callback_data=f"admin:ai:del:{section.id}", style="danger")
    b.button(text="🔙 إدارة الأقسام", callback_data="admin:ai")
    b.adjust(1)
    return b.as_markup()


def ai_admin_wizard_start_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ إلغاء", callback_data="admin:ai")
    b.adjust(1)
    return b.as_markup()


def ai_admin_wizard_mode_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👨‍💻 برمجة (الرد كملف)", callback_data="aiw:mode:code", style="success")
    b.button(text="💬 دردشة حرة", callback_data="aiw:mode:chat", style="primary")
    b.button(text="🧩 مخصص", callback_data="aiw:mode:custom")
    b.adjust(1)
    return b.as_markup()


def ai_admin_wizard_pricing_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="📊 حسب استهلاك المزود × مضاعف (موصى به)",
        callback_data="aiw:pricing:usage",
        style="success",
    )
    b.button(text="💵 سعر ثابت للرسالة", callback_data="aiw:pricing:fixed")
    b.adjust(1)
    return b.as_markup()


# ══════════════ قسم واتساب (الأدمن) ══════════════


def wa_admin_home_kb(enabled: bool, configured: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle_label = "⏸ تعطيل القسم" if enabled else "▶️ تفعيل القسم"
    b.button(text=toggle_label, callback_data="admin:wa:toggle", style="success" if not enabled else "danger")
    b.button(text="🌐 رابط سيرفر الجسر", callback_data="admin:wa:url")
    b.button(text="🔑 مفتاح الجسر", callback_data="admin:wa:key")
    b.button(text="💵 سعر اليوم", callback_data="admin:wa:price")
    b.button(text="📝 وصف القسم (للمستخدمين)", callback_data="admin:wa:desc")
    b.button(text="🤖 يوزر البوت الثاني (رابط احتياطي)", callback_data="admin:wa:bot")
    b.button(text="🧪 اختبار الاتصال بالجسر", callback_data="admin:wa:test", style="primary")
    b.button(text="👥 المشتركون", callback_data="admin:wa:subs")
    b.button(text="🔙 إدارة أقسام الذكاء الاصطناعي", callback_data="admin:ai", style="danger")
    b.adjust(1)
    return b.as_markup()

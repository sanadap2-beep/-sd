"""
أزرار القسم الرئيسي للذكاء الاصطناعي (المستخدم + لوحة الأدمن).
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import AiSection, AiSession
from services.i18n_service import I18nService
from services.ai_section_service import AiSectionService

# ══════════════ المستخدم ══════════════


def _fmt_price(price) -> str:
    text = f"{price:.4f}".rstrip("0").rstrip(".")
    return f"${text}"


def ai_home_kb(sections: list[AiSection], language: str = "ar") -> InlineKeyboardMarkup:
    """قائمة الأقسام الفعالة في القسم الرئيسي للذكاء الاصطناعي."""
    t = lambda key, **kw: I18nService.t(key, language, **kw)  # noqa: E731
    b = InlineKeyboardBuilder()
    for section in sections:
        name = section.name_en if (language == "en" and section.name_en) else section.name_ar
        emoji = "💻" if section.kind == "coding" else "💬"
        b.button(
            text=f"{emoji} {name} · {_fmt_price(AiSectionService.sell_price(section))}/رسالة",
            callback_data=f"ai:open:{section.id}",
            style="success",
        )
    b.button(text=t("ai_back_to_menu"), callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def ai_section_kb(section_id: int, has_session: bool, language: str = "ar") -> InlineKeyboardMarkup:
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    b.button(text=t("ai_new_session"), callback_data=f"ai:new:{section_id}", style="primary")
    if has_session:
        b.button(text=t("ai_continue_session"), callback_data=f"ai:continue:{section_id}")
    b.button(text=t("ai_my_sessions"), callback_data=f"ai:history:{section_id}")
    b.button(text=t("ai_back"), callback_data="ai:home")
    b.adjust(1)
    return b.as_markup()


def ai_prompt_kb(section_id: int, language: str = "ar") -> InlineKeyboardMarkup:
    """بعد الرد: إكمال الجلسة أو الخروج."""
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    b.button(text=t("ai_send_more"), callback_data=f"ai:stay:{section_id}", style="primary")
    b.button(text=t("ai_new_session"), callback_data=f"ai:new:{section_id}")
    b.button(text=t("ai_my_sessions"), callback_data=f"ai:history:{section_id}")
    b.button(text=t("ai_cancel"), callback_data="ai:cancel")
    b.adjust(2)
    return b.as_markup()


def ai_history_kb(section_id: int, sessions: list[AiSession], language: str = "ar") -> InlineKeyboardMarkup:
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    for item in sessions:
        title = item.title or "…"
        b.button(
            text=f"📜 {title[:40]} · {item.message_count // 2} {t('ai_msgs_word')}",
            callback_data=f"ai:hist_view:{item.id}",
        )
    b.button(text=t("ai_back"), callback_data=f"ai:open:{section_id}")
    b.adjust(1)
    return b.as_markup()


def ai_hist_view_kb(section_id: int, ai_session_id: int, language: str = "ar") -> InlineKeyboardMarkup:
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    b.button(text=t("ai_back"), callback_data=f"ai:history:{section_id}")
    return b.as_markup()


def ai_insufficient_kb(language: str = "ar") -> InlineKeyboardMarkup:
    from keyboards.main_menu import insufficient_balance_kb

    return insufficient_balance_kb(language)


def ai_error_kb(section_id: int, language: str = "ar") -> InlineKeyboardMarkup:
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    b.button(text=t("ai_retry"), callback_data=f"ai:stay:{section_id}", style="primary")
    b.button(text=t("ai_topup"), callback_data="menu:deposit")
    b.button(text=t("ai_cancel"), callback_data="ai:cancel")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الأدمن ══════════════


def admin_ai_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧩 الأقسام", callback_data="admin:ai_list", style="primary")
    b.button(text="➕ إضافة قسم", callback_data="admin:ai_new", style="success")
    b.button(text="🔌 مزود NanoGPT", callback_data="admin:ai_provider")
    b.button(text="📊 الإحصاءات", callback_data="admin:ai_stats")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(2, 2, 1)
    return b.as_markup()


def admin_ai_list_kb(sections: list[AiSection]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if not sections:
        pass
    for section in sections:
        status = "🟢" if section.enabled else "🔴"
        b.button(
            text=f"{status} {section.name_ar} ({section.model})",
            callback_data=f"admin:ai_edit:{section.id}",
        )
        b.button(
            text=("🔴 تعطيل" if section.enabled else "🟢 تفعيل"),
            callback_data=f"admin:ai_toggle:{section.id}",
            style="primary" if not section.enabled else "danger",
        )
    b.button(text="➕ إضافة قسم", callback_data="admin:ai_new", style="success")
    b.button(text="🔙", callback_data="admin:ai_sections")
    b.adjust(2, 2)
    return b.as_markup()


def admin_ai_kind_kb(section_id: int | None) -> InlineKeyboardMarkup:
    """اختيار نوع القسم: برمجة (ملفات) أو دردشة."""
    prefix = f"admin:ai_kind_edit:{section_id}" if section_id else "admin:ai_kind"
    b = InlineKeyboardBuilder()
    b.button(text="💻 برمجة (كود/ملفات)", callback_data=f"{prefix}:coding", style="primary")
    b.button(text="💬 دردشة", callback_data=f"{prefix}:chat")
    return b.as_markup()


def admin_ai_section_kb(section_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ تعديل البيانات", callback_data=f"admin:ai_edit:{section_id}", style="primary")
    b.button(text="🔙 قائمة الأقسام", callback_data="admin:ai_list")
    return b.as_markup()


def admin_ai_provider_kb(configured: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ عنوان الـ API", callback_data="admin:ai_prov_url")
    b.button(text="🔑 مفتاح الـ API", callback_data="admin:ai_prov_key")
    b.button(
        text="🧪 اختبار الاتصال",
        callback_data="admin:ai_prov_test",
        style="success" if configured else "danger",
    )
    b.button(text="🔙", callback_data="admin:ai_sections")
    b.adjust(1)
    return b.as_markup()

"""أنماط ألوان الأزرار الرسمية في تيليجرام (Native Button Style).

تعتمد على ``aiogram.enums.ButtonStyle`` (متوفّرة من aiogram 3.31.0+).
العملاء القدامى الذين لا يدعمون الحقل ``style`` يعرضون النمط الافتراضي
الرمادي تلقائياً — سلوك متوافق للخلف حسب توثيق تيليجرام.
"""

from __future__ import annotations

from aiogram.enums import ButtonStyle

# 🟢 أخضر: إجراءات أساسية/متجر/إحصائيات إنجاز
STYLE_STORE = ButtonStyle.SUCCESS
# 🔵 أزرق: مالية/حساب/نقاط/طلبات
STYLE_ACCOUNT = ButtonStyle.PRIMARY
# 🔴 أحمر: شروط/تحذيرات/إلغاء
STYLE_WARNING = ButtonStyle.DANGER

__all__ = [
    "ButtonStyle",
    "STYLE_STORE",
    "STYLE_ACCOUNT",
    "STYLE_WARNING",
    "add_styled",
]


def add_styled(builder, text: str, callback_data: str, style: str, **kwargs):
    """إضافة زر بنمط لوني موحّد عبر ``InlineKeyboardBuilder``."""
    builder.button(text=text, callback_data=callback_data, style=style, **kwargs)
    return builder

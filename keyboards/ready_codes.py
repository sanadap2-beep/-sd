"""Keyboards for the Ready Codes (التطبيقات والأكواد الجاهزة) section."""
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

def ready_codes_list_kb(items, language: str = "ar") -> InlineKeyboardMarkup:
    """قائمة العناصر مع أزرار التفاصيل."""
    b = InlineKeyboardBuilder()
    for item in items:
        price = "مجاني" if item.price_usd == 0 else f"${item.price_usd}"
        b.button(text=f"{item.name_ar} - {price}", callback_data=f"readycode:view:{item.id}")
    b.button(text="🔙 رجوع", callback_data="store:home")
    b.adjust(1)
    return b.as_markup()

def ready_code_detail_kb(item_id: int, price_usd, language: str = "ar") -> InlineKeyboardMarkup:
    """تفاصيل العنصر مع زر شراء."""
    b = InlineKeyboardBuilder()
    b.button(text="💳 شراء", callback_data=f"readycode:buy:{item_id}", style="primary")
    b.button(text="🔙 رجوع", callback_data="readycode:list")
    b.adjust(2)
    return b.as_markup()

def admin_ready_codes_kb(items, language: str = "ar") -> InlineKeyboardMarkup:
    """لوحة إدارة العناصر."""
    b = InlineKeyboardBuilder()
    for item in items:
        status = "✅" if item.is_active else "❌"
        b.button(text=f"{status} {item.name_ar} - ${item.price_usd}", callback_data=f"admin:readycode:edit:{item.id}")
    b.button(text="➕ إضافة جديد", callback_data="admin:readycode:add", style="success")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()

def admin_ready_code_edit_kb(item) -> InlineKeyboardMarkup:
    """قائمة تعديل عنصر جاهز: كل حقل قابل للتغيير + تفعيل/تعطيل + حذف."""
    b = InlineKeyboardBuilder()
    b.button(text=f"✏️ الاسم: {item.name_ar}", callback_data=f"arc:field:name:{item.id}")
    b.button(text=f"📝 الوصف: {(item.description or '—')[:22]}", callback_data=f"arc:field:description:{item.id}")
    b.button(text=f"💰 السعر: ${item.price_usd}", callback_data=f"arc:field:price:{item.id}")
    b.button(text=f"📋 التعليمات: {(item.instructions or '—')[:22]}", callback_data=f"arc:field:instructions:{item.id}")
    b.button(text=f"🔗 الرابط: {(item.file_url or '—')[:24]}", callback_data=f"arc:field:file_url:{item.id}")
    toggle = "⚠️ تعطيل (إخفاء)" if item.is_active else "✅ تفعيل (إظهار)"
    b.button(text=toggle, callback_data=f"arc:toggle:{item.id}")
    b.button(text="🗑 حذف", callback_data=f"arc:delete:{item.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:readycodes")
    b.adjust(1)
    return b.as_markup()

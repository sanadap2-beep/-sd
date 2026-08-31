"""
كل أزرار طرق الدفع الست.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ══════════════ القائمة الرئيسية لطرق الدفع ══════════════


def deposit_methods_kb(
    shamcash_manual_enabled: bool = False,
    stars_enabled: bool = False,
    usdt_manual_enabled: bool = False,
    shamcash_auto_enabled: bool = False,
    usdt_auto_enabled: bool = False,
    other_enabled: bool = False,
) -> InlineKeyboardMarkup:
    """
    قائمة طرق الشحن (حتى 6 أزرار).
    الأزرار تظهر فقط إذا كانت مفعلة من لوحة الأدمن.
    """
    b = InlineKeyboardBuilder()

    if shamcash_manual_enabled:
        b.button(
            text="💵 شام كاش يدوي",
            callback_data="deposit:shamcash_manual",
        )

    if stars_enabled:
        b.button(
            text="⭐ نجوم تليجرام",
            callback_data="deposit:stars",
        )

    if shamcash_auto_enabled:
        b.button(
            text="💳 شام كاش تلقائي",
            callback_data="deposit:shamcash_auto",
        )

    if usdt_auto_enabled:
        b.button(
            text="₮ USDT تلقائي",
            callback_data="deposit:usdt_auto",
        )

    if usdt_manual_enabled:
        b.button(
            text="₮ USDT يدوي",
            callback_data="deposit:usdt_manual",
        )

    if other_enabled:
        b.button(
            text="📞 طرق دفع أخرى",
            callback_data="deposit:other",
        )

    b.button(
        text="🔙 رجوع للقائمة الرئيسية",
        callback_data="back_to_main",
    )

    b.adjust(2, 2, 2, 1)
    return b.as_markup()


# ══════════════ زر البدء بعد الشرح ══════════════


def start_deposit_kb(method: str) -> InlineKeyboardMarkup:
    """يظهر بعد شرح الطريقة، للانتقال لإدخال المبلغ."""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ ابدأ الشحن",
        callback_data=f"deposit_start:{method}",
    )
    b.button(
        text="🔙 رجوع",
        callback_data="menu:deposit",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ مبالغ شحن جاهزة ══════════════


def amount_presets_kb(
    method: str,
    currency: str = "USD",
    back_callback: str = "menu:deposit",
    amounts: list[int] | tuple[int, ...] | None = None,
) -> InlineKeyboardMarkup:
    """أزرار مبالغ جاهزة تقلل الاحتكاك خصوصاً لشام كاش.

    مبالغ SYP لا تكون ضخمة ثابتة؛ الأفضل أن يمررها الهاندلر محسوبة من
    سعر الصرف اليومي الذي يحدده الأدمن. إن لم تُمرّر، نستعمل تقريب 1$ ≈ 130.
    """
    b = InlineKeyboardBuilder()
    if amounts is None:
        amounts = (1, 3, 5, 10, 25) if currency == "USD" else (130, 390, 650, 1300, 3250)
    suffix = "$" if currency == "USD" else "ل.س"
    for amount in amounts:
        b.button(
            text=f"{int(amount):,} {suffix}",
            callback_data=f"dep_amount:{method}:{currency}:{int(amount)}",
        )
    b.button(
        text="✍️ مبلغ مخصص",
        callback_data=f"dep_custom:{method}:{currency}",
    )
    b.button(
        text="🔙 رجوع",
        callback_data=back_callback,
    )
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()


# ══════════════ اختيار شبكة USDT ══════════════


def usdt_networks_kb(mode: str) -> InlineKeyboardMarkup:
    """
    قائمة اختيار شبكة USDT.
    mode: "manual" أو "auto"
    """
    b = InlineKeyboardBuilder()

    if mode == "manual":
        b.button(
            text="🟢 TRC20 (الأرخص)",
            callback_data="usdt_net:manual:TRC20",
        )
        b.button(
            text="🔵 ERC20",
            callback_data="usdt_net:manual:ERC20",
        )
        b.button(
            text="🟡 BEP20",
            callback_data="usdt_net:manual:BEP20",
        )
    else:
        b.button(
            text="🟢 TRC20 (موصى به)",
            callback_data="usdt_net:auto:TRC20",
        )

    b.button(
        text="🔙 رجوع",
        callback_data="menu:deposit",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ اختيار عملة شام كاش تلقائي ══════════════


def shamcash_currency_kb() -> InlineKeyboardMarkup:
    """اختيار عملة الدفع (USD أو SYP) للفاتورة التلقائية."""
    b = InlineKeyboardBuilder()
    b.button(
        text="💵 دولار (USD)",
        callback_data="shamcash_curr:USD",
    )
    b.button(
        text="🇸🇾 ليرة سورية (SYP)",
        callback_data="shamcash_curr:SYP",
    )
    b.button(
        text="🔙 رجوع",
        callback_data="menu:deposit",
    )
    b.adjust(2, 1)
    return b.as_markup()


# ══════════════ أزرار الفاتورة التلقائية - شام كاش ══════════════


def shamcash_invoice_kb(
    invoice_id: int,
    payment_url: str | None = None,
) -> InlineKeyboardMarkup:
    """
    أزرار الفاتورة التلقائية لشام كاش.
    تعرض:
    - زر إدخال رقم العملية
    - رابط صفحة الدفع (اختياري)
    - إلغاء
    """
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ أدخلت رقم العملية",
        callback_data=f"sc_verify:{invoice_id}",
    )

    if payment_url:
        b.row(
            InlineKeyboardButton(
                text="🌐 فتح صفحة الدفع",
                url=payment_url,
            )
        )

    b.button(
        text="❌ إلغاء الفاتورة",
        callback_data=f"sc_cancel:{invoice_id}",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ أزرار الفاتورة التلقائية - USDT ══════════════


def usdt_auto_invoice_kb(
    invoice_id: int,
    payment_url: str | None = None,
) -> InlineKeyboardMarkup:
    """
    أزرار الفاتورة التلقائية لـ USDT.
    """
    b = InlineKeyboardBuilder()
    b.button(
        text="🔄 تحقق الآن",
        callback_data=f"usdt_check:{invoice_id}",
    )

    if payment_url:
        b.row(
            InlineKeyboardButton(
                text="🌐 فتح صفحة الدفع",
                url=payment_url,
            )
        )

    b.button(
        text="❌ إلغاء الفاتورة",
        callback_data=f"usdt_cancel:{invoice_id}",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ زر إلغاء عام ══════════════


def cancel_deposit_kb() -> InlineKeyboardMarkup:
    """زر إلغاء عملية الشحن."""
    b = InlineKeyboardBuilder()
    b.button(
        text="❌ إلغاء",
        callback_data="menu:deposit",
    )
    return b.as_markup()

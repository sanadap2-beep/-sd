"""
خدمة تحويل العملات.
تُستخدم لتحويل عملات المزودين إلى دولار.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


# ── أسعار صرف افتراضية (يمكن تحديثها من لوحة الأدمن) ──
DEFAULT_RATES_TO_USD = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.08"),
    "RUB": Decimal("0.011"),
    "TRY": Decimal("0.03"),
    "IDR": Decimal("0.000064"),
    "INR": Decimal("0.012"),
    "PKR": Decimal("0.0036"),
    "BDT": Decimal("0.0091"),
    "SYP": Decimal("0.000067"),
    "SAR": Decimal("0.27"),
    "AED": Decimal("0.27"),
    "EGP": Decimal("0.020"),
    "IQD": Decimal("0.00076"),
    "JOD": Decimal("1.41"),
    "LBP": Decimal("0.000011"),
    "MAD": Decimal("0.10"),
    "DZD": Decimal("0.0075"),
    "TND": Decimal("0.32"),
    "LYD": Decimal("0.21"),
    "YER": Decimal("0.004"),
    "SDG": Decimal("0.0017"),
    "QAR": Decimal("0.27"),
    "OMR": Decimal("2.60"),
    "KWD": Decimal("3.25"),
    "BHD": Decimal("2.65"),
    "IRR": Decimal("0.000024"),
    "CNY": Decimal("0.14"),
    "GBP": Decimal("1.27"),
    "CAD": Decimal("0.73"),
    "AUD": Decimal("0.65"),
    "BRL": Decimal("0.20"),
    "MXN": Decimal("0.058"),
    "ARS": Decimal("0.001"),
    "COP": Decimal("0.00025"),
    "VND": Decimal("0.000041"),
    "THB": Decimal("0.029"),
    "PHP": Decimal("0.018"),
    "MYR": Decimal("0.22"),
    "SGD": Decimal("0.75"),
    "KRW": Decimal("0.00074"),
    "JPY": Decimal("0.0068"),
    "HKD": Decimal("0.13"),
    "TWD": Decimal("0.031"),
    "ZAR": Decimal("0.055"),
    "NGN": Decimal("0.00065"),
    "UAH": Decimal("0.025"),
    "PLN": Decimal("0.25"),
    "UZS": Decimal("0.000080"),
    "KZT": Decimal("0.0022"),
}


# ── عملات العرض للمستخدم (الحسابات كلها بالدولار داخلياً) ──
# symbol_ar/symbol_en: الرمز المعروض، decimals: عدد المنازل العشرية عند العرض.
DISPLAY_CURRENCIES: dict[str, dict] = {
    "USD": {"symbol_ar": "$", "symbol_en": "$", "name_ar": "دولار أمريكي", "name_en": "US Dollar", "decimals": 2},
    "EUR": {"symbol_ar": "€", "symbol_en": "€", "name_ar": "يورو", "name_en": "Euro", "decimals": 2},
    "EGP": {"symbol_ar": "ج.م", "symbol_en": "E£", "name_ar": "جنيه مصري", "name_en": "Egyptian Pound", "decimals": 2},
    "SYP": {"symbol_ar": "ل.س", "symbol_en": "SYP", "name_ar": "ليرة سورية", "name_en": "Syrian Pound", "decimals": 0},
}

# مفاتيح إعدادات أسعار الصرف اليومية (1 USD = ? عملة) التي يضبطها الأدمن.
DISPLAY_RATE_SETTING_KEYS = {
    "SYP": "usd_to_syp_rate",
    "EUR": "usd_to_eur_rate",
    "EGP": "usd_to_egp_rate",
}

# قيم افتراضية احتياطية إذا لم يضبط الأدمن السعر بعد.
DEFAULT_DISPLAY_RATES = {
    "USD": Decimal("1"),
    "EUR": Decimal("0.92"),
    "EGP": Decimal("48.5"),
    "SYP": Decimal("130"),
}


class CurrencyService:
    """خدمة تحويل العملات إلى دولار + عرض المبالغ بعملة المستخدم."""

    @staticmethod
    def get_default_rate(currency: str) -> Decimal:
        """يجلب سعر الصرف الافتراضي لعملة."""
        return DEFAULT_RATES_TO_USD.get(currency.upper(), Decimal("1"))

    @staticmethod
    async def get_rate_to_usd(currency: str, session=None) -> Decimal:
        """
        يجلب سعر تحويل العملة إلى الدولار.
        أولاً من الإعدادات، ثم من القيم الافتراضية.
        """
        currency = currency.upper()

        if currency == "USD":
            return Decimal("1")

        setting_key = f"rate_{currency.lower()}_to_usd"
        rate_str = await SettingsService.get(setting_key)

        if rate_str:
            try:
                return Decimal(rate_str)
            except Exception:
                pass

        return CurrencyService.get_default_rate(currency)

    @staticmethod
    async def convert_to_usd(
        amount: Decimal,
        currency: str,
        session=None,
    ) -> Decimal:
        """
        يحول مبلغ من عملة معينة إلى دولار.
        """
        if currency.upper() == "USD":
            return amount

        rate = await CurrencyService.get_rate_to_usd(currency, session)
        result = amount * rate
        return result.quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )

    @staticmethod
    def get_supported_currencies() -> list[str]:
        """يجلب قائمة العملات المدعومة."""
        return list(DEFAULT_RATES_TO_USD.keys())

    @staticmethod
    async def set_custom_rate(
        session,
        currency: str,
        rate: Decimal,
    ) -> None:
        """يحفظ سعر صرف مخصص لعملة."""
        currency = currency.upper()
        setting_key = f"rate_{currency.lower()}_to_usd"
        await SettingsService.set(session, setting_key, str(rate))
        logger.info(f"تم تحديث سعر الصرف: 1 {currency} = {rate} USD")

    # ─────────── عملات العرض (USD/EUR/EGP/SYP) ───────────

    @staticmethod
    def is_display_currency(code: str | None) -> bool:
        """هل الرمز من عملات العرض المدعومة؟"""
        return (code or "").upper() in DISPLAY_CURRENCIES

    @staticmethod
    def normalize_display_currency(code: str | None) -> str:
        """يرجع رمز عملة عرض صالحاً (USD افتراضياً)."""
        code = (code or "").upper().strip()
        return code if code in DISPLAY_CURRENCIES else "USD"

    @staticmethod
    async def get_display_rate(session, currency: str) -> Decimal:
        """سعر عرض 1 USD بعملة العرض (يضبطه الأدمن يومياً)."""
        currency = CurrencyService.normalize_display_currency(currency)
        if currency == "USD":
            return Decimal("1")
        setting_key = DISPLAY_RATE_SETTING_KEYS[currency]
        rate = await SettingsService.get_decimal(
            setting_key, DEFAULT_DISPLAY_RATES[currency]
        )
        if rate <= 0 or not rate.is_finite():
            # سعر غير صالح → ارجع للافتراضي بدل كسر العرض.
            return DEFAULT_DISPLAY_RATES[currency]
        return rate

    @staticmethod
    async def convert_from_usd(
        amount_usd: Decimal,
        currency: str,
        session=None,
    ) -> Decimal:
        """يحوّل مبلغاً بالدولار إلى عملة العرض."""
        currency = CurrencyService.normalize_display_currency(currency)
        if currency == "USD":
            return amount_usd
        rate = await CurrencyService.get_display_rate(session, currency)
        decimals = DISPLAY_CURRENCIES[currency]["decimals"]
        return (amount_usd * rate).quantize(
            Decimal("1").scaleb(-decimals), rounding=ROUND_HALF_UP
        )

    @staticmethod
    async def format_from_usd(
        amount_usd: Decimal | float | str,
        currency: str,
        session=None,
        language: str = "ar",
        always_include_symbol: bool = True,
    ) -> str:
        """ينسّق مبلغاً بالدولار بعملة العرض مع فواصل الآلاف.

        مثال: (Decimal('5'), 'SYP') → «75,000 ل.س»
        """
        currency = CurrencyService.normalize_display_currency(currency)
        amount = amount_usd if isinstance(amount_usd, Decimal) else Decimal(str(amount_usd))
        converted = await CurrencyService.convert_from_usd(amount, currency, session)
        meta = DISPLAY_CURRENCIES[currency]
        symbol = meta["symbol_en"] if (language or "ar").lower().startswith("en") else meta["symbol_ar"]
        formatted = f"{converted:,.{meta['decimals']}f}"
        if currency == "USD":
            return f"${formatted}"
        if not always_include_symbol:
            return formatted
        return f"{formatted} {symbol}"

    @staticmethod
    async def format_user_amount(amount_usd, db_user, session=None) -> str:
        """ينسّق مبلغاً بعملة العرض الخاصة بالمستخدم (لديه db_user)."""
        currency = CurrencyService.normalize_display_currency(
            getattr(db_user, "display_currency", "USD")
        )
        language = getattr(db_user, "language_code", "ar") or "ar"
        return await CurrencyService.format_from_usd(
            amount_usd, currency, session, language
        )

    @staticmethod
    async def format_dual(
        amount_usd,
        db_user,
        session=None,
    ) -> str:
        """«5.00$ (≈ 75,000 ل.س)» — دولار + ما يعادله بعملة العرض."""
        amount = amount_usd if isinstance(amount_usd, Decimal) else Decimal(str(amount_usd))
        usd_text = f"${amount:,.2f}"
        currency = CurrencyService.normalize_display_currency(
            getattr(db_user, "display_currency", "USD")
        )
        if currency == "USD":
            return usd_text
        converted = await CurrencyService.format_user_amount(amount, db_user, session)
        return f"{usd_text} (≈ {converted})"

    @staticmethod
    async def syp_note(
        amount_usd,
        db_user,
        session=None,
    ) -> str:
        """ملحق «≈ X ل.س» بجانب السعر بالدولار — ميزة syp_display.

        يُظهر ما يعادل المبلغ بالليرة السورية عند سعر الصرف الحالي حتى لو
        كانت عملة عرض المستخدم هي الدولار. إن كانت العملة أصلًا SYP أو
        الميزة معطلة لا يُضاف شيء (السعر معروض مسبقاً).
        """
        try:
            from services.feature_service import FeatureService

            if not await FeatureService.enabled("syp_display"):
                return ""
            currency = CurrencyService.normalize_display_currency(
                getattr(db_user, "display_currency", "USD")
            )
            if currency == "SYP":
                return ""
            amount = amount_usd if isinstance(amount_usd, Decimal) else Decimal(str(amount_usd))
            rate = await CurrencyService.get_display_rate(session, "SYP")
            syp = (amount * rate).quantize(Decimal("1"))
            return f" (≈ {syp:,} ل.س)"
        except Exception:
            return ""

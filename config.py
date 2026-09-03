"""
إعدادات المشروع — تُقرأ من ملف .env
القيم المالية هنا تُستخدم فقط كـ "بذرة أولية" (seed) عند أول تشغيل،
وبعدها تُدار بالكامل من قاعدة البيانات (جدول settings) عبر لوحة الأدمن.
"""

from decimal import Decimal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "local.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ── البوت الأساسي ──
    BOT_TOKEN: str
    # Optional: deep links now resolve the live username via bot.get_me().
    # This remains as a sanitized fallback for offline/local contexts.
    BOT_USERNAME: str = ""
    ADMIN_IDS: str

    # ── قنوات الإشعارات ──
    ADMIN_NOTIFY_CHAT_ID: int
    PUBLIC_CHANNEL_ID: int = 0
    BACKUP_CHANNEL_ID: int = 0

    # ── مزودو الأرقام ──
    FIVESIM_API_KEY: str = ""
    HEROSMS_API_KEY: str = ""
    SMS_ACTIVATE_API_KEY: str = ""
    SMSHUB_API_KEY: str = ""

    # ── Sam API (شام كاش تلقائي) ──
    SAM_API_KEY: str = ""
    SAM_API_WALLET_ADDRESS: str = ""
    SAM_API_URL: str = "https://www.sam-api.pro/api"

    # ── Plisio (USDT تلقائي) ──
    PLISIO_SECRET_KEY: str = ""
    PLISIO_API_URL: str = "https://api.plisio.net/api/v1"
    PLISIO_FEE_PERCENT: Decimal = Decimal("3.0")
    PLISIO_MIN_AMOUNT_USD: Decimal = Decimal("2.0")
    PLISIO_MAX_AMOUNT_USD: Decimal = Decimal("500.0")
    PLISIO_INVOICE_EXPIRE_MINUTES: int = 30
    PLISIO_POLLING_INTERVAL_SECONDS: int = 30

    # ── عناوين محافظ USDT اليدوي ──
    USDT_TRC20_ADDRESS: str = ""
    USDT_ERC20_ADDRESS: str = ""
    USDT_BEP20_ADDRESS: str = ""

    # ── عنوان شام كاش اليدوي ──
    SHAMCASH_MANUAL_ADDRESS: str = ""
    SHAMCASH_MANUAL_NAME: str = ""

    # ── قاعدة البيانات والتخزين ──
    DATABASE_URL: str = "sqlite+aiosqlite:///./bot_database.db"
    # اختياري: يحفظ حالات المحادثة بعد إعادة التشغيل عند استخدام Redis.
    REDIS_URL: str = ""
    # Fernet key for digital inventory. Generate with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    INVENTORY_ENCRYPTION_KEY: str = ""

    # ── Mini App / API ──
    WEBAPP_URL: str = ""
    ADMIN_WEBAPP_URL: str = ""
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8080
    # One-time local setup key; supplied by the launcher, never committed.
    SETUP_KEY: str = ""
    SENTRY_DSN: str = ""
    ENVIRONMENT: str = "production"

    # ── الإعدادات المالية الافتراضية (بالدولار فقط) ──
    DEFAULT_PROFIT_MARGIN_PERCENT: Decimal = Decimal("50")

    # الحدود الدنيا لكل طريقة دفع
    MIN_DEPOSIT_SHAMCASH_USD: Decimal = Decimal("0.5")
    MIN_DEPOSIT_USDT_USD: Decimal = Decimal("2")
    MIN_DEPOSIT_STARS_USD: Decimal = Decimal("1")

    # سعر صرف افتراضي USD → SYP (يُدار من لوحة الأدمن)
    DEFAULT_USD_TO_SYP_RATE: Decimal = Decimal("130")

    LARGE_TRANSACTION_THRESHOLD_USD: Decimal = Decimal("20")
    REFERRAL_BONUS_USD: Decimal = Decimal("0.015")
    REFERRAL_PERCENT: Decimal = Decimal("5")
    CASHBACK_PERCENT: Decimal = Decimal("0")
    LOYALTY_POINTS_PER_USD: Decimal = Decimal("10")
    LOYALTY_DAILY_POINTS: int = 25
    LOYALTY_POINTS_PER_USD_REDEEM: int = 1000
    LOYALTY_MIN_REDEEM_POINTS: int = 100
    STARS_RATE_USD: Decimal = Decimal("0.013")
    MAX_ACTIVE_ORDERS: int = 3
    RATE_LIMIT_SECONDS: int = 30
    GLOBAL_RATE_LIMIT_PER_MINUTE: int = 60
    CALLBACK_RATE_LIMIT_PER_MINUTE: int = 120
    API_RATE_LIMIT_PER_MINUTE: int = 120
    API_RATE_LIMIT_BURST: int = 240
    PROVIDER_LOW_BALANCE_THRESHOLD: Decimal = Decimal("10")
    LARGE_ORDER_CONFIRM_USD: Decimal = Decimal("20")

    # ── إعدادات عامة ──
    REQUIRE_SUBSCRIPTION_FOR_REFERRAL: bool = True
    SUPPORT_USERNAME: str = "@support"
    PAYMENT_METHOD_TEXT: str = "سيتم إضافة طريقة الدفع قريباً"
    ORDER_TIMEOUT_MINUTES: int = 5

    @property
    def admin_ids_list(self) -> list[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip()]


settings = Settings()

"""
نماذج قاعدة البيانات (SQLAlchemy 2.0 Async Style).

قرارات تصميم:
1) كل الحقول المالية Numeric(18,4) وتُعامل كـ Decimal حصراً (لا float أبداً).
2) العملة الداخلية: دولار أمريكي (USD) بالكامل - لا روبل.
3) transactions دفتر أستاذ كامل لكل حركة رصيد.
4) نظام ديناميكي كامل: categories, sub_categories, products, api_providers.
5) settings جدول key-value لكل الإعدادات القابلة للتعديل من لوحة الأدمن.
6) provider_services: يحفظ كل خدمات المزودين المسحوبة (ليست منتجات للبيع بعد).
7) products: مرتبطة بـ provider_service وتحتوي سعر البيع النهائي.
8) audit_logs: يسجل كل تعديلات الأدمن للمراجعة.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(18, 4)


class Base(DeclarativeBase):
    pass


# ═══════════════════════════ Enums ═══════════════════════════


class TransactionType(str, enum.Enum):
    DEPOSIT = "deposit"
    PURCHASE = "purchase"
    REFUND = "refund"
    REFERRAL_BONUS = "referral_bonus"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    ADMIN_ADD = "admin_add"
    ADMIN_DEDUCT = "admin_deduct"
    CASHBACK = "cashback"
    STARS_DEPOSIT = "stars_deposit"
    COUPON_BONUS = "coupon_bonus"
    LOYALTY_REDEEM = "loyalty_redeem"
    GIFT_REDEEM = "gift_redeem"
    AI_USAGE = "ai_usage"
    WA_SUBSCRIPTION = "wa_subscription"


class DepositStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CODE_RECEIVED = "code_received"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class ProviderName(str, enum.Enum):
    FIVESIM = "fivesim"
    HEROSMS = "herosms"
    SMS_ACTIVATE = "sms_activate"
    SMSHUB = "smshub"
    SMSPOOL = "smspool"
    GRIZZLY = "grizzly"


class CategoryType(str, enum.Enum):
    NUMBERS = "numbers"
    SMM = "smm"
    GAMES = "games"
    APPS = "apps"
    BALANCES = "balances"
    CARDS = "cards"
    SUBSCRIPTIONS = "subscriptions"
    VERIFICATION = "verification"
    CODES = "codes"
    CUSTOM = "custom"


class ProductStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ProductFulfillmentType(str, enum.Enum):
    API = "api"
    INVENTORY = "inventory"
    MANUAL = "manual"


class PromotionDiscountType(str, enum.Enum):
    PERCENT = "percent"
    FIXED = "fixed"


class InventoryItemStatus(str, enum.Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    SOLD = "sold"
    VOID = "void"


class SupportTicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ProductRequestStatus(str, enum.Enum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"


class ChallengeStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class UnifiedOrderStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIAL = "partial"


class ApiProviderType(str, enum.Enum):
    NUMBERS = "numbers"
    GAMES = "games"
    SMM = "smm"
    APPS = "apps"
    BALANCES = "balances"
    CARDS = "cards"
    SUBSCRIPTIONS = "subscriptions"
    VERIFICATION = "verification"
    CODES = "codes"
    STORE = "store"
    CUSTOM = "custom"


class ApiProtocolType(str, enum.Enum):
    """نوع بروتوكول التواصل مع المزود."""

    SMS = "sms"
    SMM_V2 = "smm_v2"
    GAMES_GENERIC = "games_generic"
    CUSTOM = "custom"


class ProviderServiceStatus(str, enum.Enum):
    """حالة خدمة المزود المسحوبة."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED_FROM_PROVIDER = "deleted_from_provider"


class ProviderPriceType(str, enum.Enum):
    """نوع تسعير الخدمة عند المزود."""

    PER_1000 = "per_1000"
    PER_ITEM = "per_item"
    FIXED = "fixed"


class ProductPricingType(str, enum.Enum):
    """نوع تسعير المنتج للمستخدم."""

    FIXED = "fixed"
    MARGIN_PERCENT = "margin_percent"


class ProductDisplayType(str, enum.Enum):
    """كيف يُعرض السعر للمستخدم."""

    PER_1000 = "per_1000"
    PER_MIN_QUANTITY = "per_min_quantity"
    FIXED_TOTAL = "fixed_total"


class AutoInvoiceMethod(str, enum.Enum):
    SHAMCASH_AUTO = "shamcash_auto"
    USDT_AUTO = "usdt_auto"


class AutoInvoiceStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AuditAction(str, enum.Enum):
    """أنواع عمليات الأدمن المُسجّلة في Audit Log."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"
    SYNC = "sync"
    PRICE_CHANGE = "price_change"
    OTHER = "other"


# ═══════════════════════════ Models ═══════════════════════════


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_code: Mapped[str] = mapped_column(String(8), default="ar")
    # عملة العرض المفضلة (USD/EUR/EGP/SYP) — الحسابات كلها تبقى بالدولار.
    display_currency: Mapped[str] = mapped_column(String(8), default="USD")

    balance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)

    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    referral_bonus_paid: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── حماية الإحالة من البوتات ──
    # المستخدم الذي دخل عبر رابط ref_ يبقى pending حتى يجتاز اختبار البشر،
    # وعندها فقط يُفعَّل ويُدفع لمحيله. الفشل المتكرر = روبوت → عقوبة للمحيل.
    referral_check_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    referral_check_fails: Mapped[int] = mapped_column(Integer, default=0)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)

    total_spent_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    cashback_earned_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    # مستوى التحقق الذكي: 0 جديد، 1 موثوق بالسلوك، 2 موثق يدوياً
    verification_tier: Mapped[int] = mapped_column(Integer, default=0)

    # برنامج الولاء والمكافآت
    loyalty_points: Mapped[int] = mapped_column(Integer, default=0)
    loyalty_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_checkin_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    referrer: Mapped["User"] = relationship(remote_side=[id], backref="referrals")


class AgentCode(Base):
    """
    كود وكالة يصدره الأدمن لشخص محدد.

    الكود يُستعمل مرة واحدة: يدخله المستخدم في البوت فيصبح
    وكلاً بنسبة الخصم المثبتة على الكود.
    """

    __tablename__ = "agent_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    percent: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("10"))
    status: Mapped[str] = mapped_column(String(16), default="unused", index=True)  # unused/used/voided
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    redeemer: Mapped["User | None"] = relationship(foreign_keys=[user_id])


class AgentProfile(Base):
    """
    وكيل فعّال (أو سُبقت وكالته) لدى البوت.

    - percent: نسبة الخصم الحالية على جميع المنتجات والخدمات
      (قابلة للرفع/الخفض من «إدارة الوكلاء»).
    - السحب: إذا بقي إيداع الوكيل الأسبوعي أقل من الحد
      (min_weekly_deposit_usd) تُسحب وكالته مع إشعار الإدارة.
    """

    __tablename__ = "agent_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    code_id: Mapped[int | None] = mapped_column(ForeignKey("agent_codes.id"), nullable=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    percent: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("10"))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active/revoked

    activated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # آخر أسبوع فُحص فيه الإيداع (لمنع سحب متكرر لنفس الأسبوع)
    last_checked_week: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    code: Mapped["AgentCode | None"] = relationship()
    granter: Mapped["User | None"] = relationship(foreign_keys=[granted_by])


class LoyaltyEvent(Base):
    """سجل نقاط الولاء مع مفتاح يمنع احتساب الحدث مرتين."""

    __tablename__ = "loyalty_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    points: Mapped[int] = mapped_column(Integer)
    related_table: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    user: Mapped["User"] = relationship()


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    balance_after: Mapped[Decimal] = mapped_column(MONEY)

    related_table: Mapped[str | None] = mapped_column(String(32), nullable=True)
    related_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # مرجع فريد للدفعات الخارجية (Telegram Stars / invoices). يمنع إضافة
    # نفس الدفعة مرتين عند إعادة إرسال التحديث أو تشغيل أكثر من مهمة مراقبة.
    payment_reference: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )

    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class DepositRequest(Base):
    __tablename__ = "deposit_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    proof_photo_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proof_tx_number: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payment_method: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[DepositStatus] = mapped_column(
        SAEnum(DepositStatus), default=DepositStatus.PENDING
    )

    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    admin_chat_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])


class NumberOrder(Base):
    __tablename__ = "number_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    provider: Mapped[ProviderName] = mapped_column(SAEnum(ProviderName))
    provider_order_id: Mapped[str] = mapped_column(String(64))

    service: Mapped[str] = mapped_column(String(64))
    country_code: Mapped[str] = mapped_column(String(32))
    operator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    phone_number: Mapped[str] = mapped_column(String(32))

    price_provider_usd: Mapped[Decimal] = mapped_column(MONEY)
    price_sell_usd: Mapped[Decimal] = mapped_column(MONEY)

    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.PENDING)
    sms_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_sms_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    purchased_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    status_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # هل صُرف تعويض تأمين عن هذا الطلب (يمنع المطالبة المزدوجة)
    insurance_claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    # بصمة الرقم — دليل يثبت لمن بيع الرقم ومتى، يحسم نزاع «الرقم مستخدم»
    number_fingerprint: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    # الأرقام المقيمة: تبقى ملك المستخدم حتى هذا التاريخ.
    # user_id = None يعني الرقم في البركة المُسخَّنة لم يُسند لأحد بعد.
    dedicated_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    # قابلية النقل: يبقى الرقم محفوظاً لمالكه حتى هذا التاريخ
    portable_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    status_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    awaiting_extra_code: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_codes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship()


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(64))
    flag: Mapped[str] = mapped_column(String(8), default="🌍")

    fivesim_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    herosms_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sms_activate_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    smshub_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    smspool_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    grizzly_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    added_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NumberService(Base):
    """
    خدمات الأرقام الديناميكية.
    تُدار بالكامل من لوحة الأدمن بدل SERVICE_MAP الثابت.
    """

    __tablename__ = "number_services"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="📱")

    fivesim_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    herosms_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sms_activate_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    smshub_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    smspool_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    grizzly_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NumberServer(Base):
    """سيرفر/مزود ديناميكي ضمن خدمة أرقام.

    المفهوم:
    - يمكن أن تحوي خدمة الأرقام الواحدة (مثل WhatsApp) عدة سيرفرات؛
      كل سيرفر مربوط بمزود مختلف (5sim / HeroSMS / SMS-Activate / SMSHub ...).
    - قبل أن يرى المستخدم الدول، يختار السيرفر (المزود) الذي يريد الشراء منه.
    - الأدمن يتحكم بالكامل: إضافة، تعطيل، إعادة ترتيب، حذف، وبحث لكل سيرفر.
    - نفس النمط قابل للتعميم لاحقاً على أقسام الرشق والألعاب (سيرفر لكل متجر/مزود).
    """

    __tablename__ = "number_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    number_service_id: Mapped[int] = mapped_column(
        ForeignKey("number_services.id", ondelete="CASCADE"), index=True
    )
    # provider يحمل قيمة ProviderName التي يقرأها ProviderManager،
    # وسيُفسَّر لاحقاً كمفتاح عام لأي مزود (رقم/رشق/ألعاب).
    provider: Mapped[str] = mapped_column(String(64), index=True)
    name_ar: Mapped[str] = mapped_column(String(96))
    emoji: Mapped[str] = mapped_column(String(8), default="🖥")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # نسبة ربح السيرفر (تتجاوز هامش الخدمة/الدولة/المزود العام عند ضبطها).
    margin_percent: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 🟢 النقطة الخضراء: الأدمن يعلّم بها السيرفر الذي يعمل فعلياً الآن.
    # مستقلة تماماً عن is_active (التفعيل/التعطيل): السيرفر قد يكون مفعّلاً
    # لكن غير معلَّم كشغّال. تظهر النقطة للمستخدم أمام «سيرفر 2» مثلاً.
    is_working: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StoreServer(Base):
    """سيرفر/مزود ديناميكي عام يعمل على أي قسم.

    المفهوم (يغطي كل الأقسام، ليس الأرقام فقط):
    - كل قسم (رئيسي ``category`` أو فرعي ``subcategory``) أو خدمة أرقام
      يمكن أن يحوي عدة سيرفرات.
    - كل سيرفر مربوط بمزود:
        * ``provider_kind='api'`` + ``api_provider_id`` → مزود المتجر
          (رشق / ألعاب / تطبيقات / متجر عام ...).
        * ``provider_kind='number'`` + ``provider_value`` → مزود أرقام
          (5sim / HeroSMS / SMS-Activate / SMSHub ...).
    - ``margin_percent`` نسبة ربح السيرفر (تتجاوز هامش القسم/العام عند ضبطه).
    - من لوحة الأدمن يمكن إضافة/تعديل/تعطيل/حذف وأي سيرفر لأي قسم بلا كود.
    """

    __tablename__ = "store_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # category | subcategory | number_service | global
    scope: Mapped[str] = mapped_column(String(32), index=True)
    scope_id: Mapped[int] = mapped_column(Integer, index=True)

    # number | api
    provider_kind: Mapped[str] = mapped_column(String(16), default="api")
    provider_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_providers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name_ar: Mapped[str] = mapped_column(String(96))
    emoji: Mapped[str] = mapped_column(String(8), default="🖥")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    margin_percent: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MandatoryChannel(Base):
    __tablename__ = "mandatory_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username_or_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ServicePricing(Base):
    __tablename__ = "service_pricing"

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(64))
    country_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[ProviderName | None] = mapped_column(SAEnum(ProviderName), nullable=True)

    margin_type: Mapped[str] = mapped_column(String(16), default="percent")
    margin_value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("50"))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, onupdate=func.now(), server_default=func.now()
    )


class ProviderStatus(Base):
    __tablename__ = "provider_status"

    provider: Mapped[ProviderName] = mapped_column(SAEnum(ProviderName), primary_key=True)
    balance: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, onupdate=func.now(), server_default=func.now()
    )


class BroadcastLog(Base):
    __tablename__ = "broadcast_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    total_sent: Mapped[int] = mapped_column(Integer, default=0)
    total_failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SpecialOffer(Base):
    """عرض خاص لمدة 24 ساعة، يدوي أو مربوط بمزود API."""

    __tablename__ = "special_offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_type: Mapped[str] = mapped_column(String(16), default="manual", index=True)  # manual / api
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    input_label: Mapped[str] = mapped_column(String(128), default="أرسل المطلوب")
    eta_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft/active/expired/deleted
    api_provider_id: Mapped[int | None] = mapped_column(ForeignKey("api_providers.id"), nullable=True)
    provider_service_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_service_ref_id: Mapped[int | None] = mapped_column(ForeignKey("provider_services.id"), nullable=True)
    required_quantity: Mapped[int] = mapped_column(Integer, default=1)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    extend_votes: Mapped[int] = mapped_column(Integer, default=0)
    extended_once: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_4h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    api_provider: Mapped["ApiProvider | None"] = relationship()
    provider_service: Mapped["ProviderService | None"] = relationship()


class SpecialOfferOrder(Base):
    """طلب شراء على عرض خاص."""

    __tablename__ = "special_offer_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("special_offers.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target: Mapped[str] = mapped_column(String(500))
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    external_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    voted_extend: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    offer: Mapped["SpecialOffer"] = relationship()
    user: Mapped["User"] = relationship()


class SponsoredAd(Base):
    """إعلان مدفوع يراجعه الأدمن ثم يظهر داخل البوت وفي قناة الإشعارات."""

    __tablename__ = "sponsored_ads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)
    item_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact: Mapped[str] = mapped_column(String(128))
    photo_file_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    amount_paid_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    renewal_count: Mapped[int] = mapped_column(Integer, default=0)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_channel_post_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class Notification(Base):
    """سجل إشعارات موحد: صندوق وارد + سجل إرسال + منع تكرار."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    recipient_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="user", index=True)
    category: Mapped[str] = mapped_column(String(32), default="system", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal", index=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    user: Mapped["User | None"] = relationship()


class NotificationPreference(Base):
    """تفضيلات المستخدم للإشعارات غير الحرجة."""

    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", "category", name="uq_notification_pref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship()


class NotificationTemplate(Base):
    """قوالب إشعارات قابلة للتعديل من لوحة الأدمن."""

    __tablename__ = "notification_templates"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="system")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SupportTicket(Base):
    """تذكرة دعم قابلة للمتابعة من المستخدم والأدمن."""

    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[SupportTicketStatus] = mapped_column(
        SAEnum(SupportTicketStatus),
        default=SupportTicketStatus.OPEN,
        index=True,
    )
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        onupdate=func.now(),
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    admin: Mapped["User | None"] = relationship(foreign_keys=[admin_id])


class ProductRequest(Base):
    """طلبات المستخدمين للخدمات غير الموجودة في الكتالوج."""

    __tablename__ = "product_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(128), index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_title: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[ProductRequestStatus] = mapped_column(
        SAEnum(ProductRequestStatus),
        default=ProductRequestStatus.OPEN,
        index=True,
    )
    votes_count: Mapped[int] = mapped_column(Integer, default=1)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, onupdate=func.now(), server_default=func.now()
    )
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    handler: Mapped["User | None"] = relationship(foreign_keys=[handled_by])
    votes: Mapped[list["ProductRequestVote"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
    )


class ProductRequestVote(Base):
    __tablename__ = "product_request_votes"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "user_id",
            name="uq_product_request_vote",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("product_requests.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    request: Mapped["ProductRequest"] = relationship(back_populates="votes")


# ══════════════ نظام الأقسام الديناميكي ══════════════


class Category(Base):
    """
    الأقسام الرئيسية الديناميكية.
    مثال: شحن ألعاب، رشق سوشيال، تطبيقات دردشة.
    تُنشأ وتُدار بالكامل من لوحة الأدمن.
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="📦")
    type: Mapped[CategoryType] = mapped_column(SAEnum(CategoryType))
    # شرح القسم الذي يظهر للزبون عند فتحه (يُضبط من لوحة الأدمن).
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # هامش ربح القسم (نسخة): يُطبق على كل منتجات القسم ما لم يُضبط
    # هامش أخص على القسم الفرعي أو المنتج. null = يرث الهامش العالمي.
    profit_margin_percent: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sub_categories: Mapped[list["SubCategory"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )


class SubCategory(Base):
    """
    الأقسام الفرعية الديناميكية.
    مثال: ببجي، فري فاير، إنستقرام، تيك توك.

    يمكن للقسم الفرعي أن يحتوي أقساماً داخلية (Sections) عبر
    ``parent_sub_category_id``: في قسم الرشق يمثل التطبيق (إنستغرام)
    ويمثل الفرع الداخلي النوع (متابعون/لايكات/مشاهدات...). ``kind_key``
    يحفظ النوع المعياري من services.smm_catalog لربط الأقسام المولّدة
    تلقائياً بخدمات المزود المسحوبة.
    """

    __tablename__ = "sub_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    parent_sub_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("sub_categories.id"), nullable=True, index=True
    )
    # النوع المعياري (followers/likes/views/...) للأقسام الداخلية المولّدة آلياً.
    kind_key: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="📱")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # هامش ربح القسم الفرعي (نسخة): أولوية أعلى من هامش القسم الرئيسي.
    # null = يرث (قسمه الرئيسي ثم العالمي).
    profit_margin_percent: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    category: Mapped["Category"] = relationship(back_populates="sub_categories")
    parent: Mapped["SubCategory | None"] = relationship(
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[list["SubCategory"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    products: Mapped[list["Product"]] = relationship(
        back_populates="sub_category", cascade="all, delete-orphan"
    )


class ApiProvider(Base):
    """
    مزودو الألعاب والـ SMM (محسّن).
    مختلف تماماً عن مزودي الأرقام (fivesim/herosms).
    يُدار بالكامل من لوحة الأدمن.
    """

    __tablename__ = "api_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[ApiProviderType] = mapped_column(SAEnum(ApiProviderType))
    protocol_type: Mapped[ApiProtocolType] = mapped_column(
        SAEnum(ApiProtocolType),
        default=ApiProtocolType.SMM_V2,
    )
    api_url: Mapped[str] = mapped_column(String(500))
    api_key: Mapped[str] = mapped_column(String(255))
    balance: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    rate_to_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    low_balance_threshold: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("10"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_services: Mapped[int] = mapped_column(Integer, default=0)
    custom_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    services: Mapped[list["ProviderService"]] = relationship(
        back_populates="api_provider", cascade="all, delete-orphan"
    )
    products: Mapped[list["Product"]] = relationship(back_populates="api_provider")


class ProviderService(Base):
    """
    جدول خدمات المزودين المسحوبة.
    كل خدمة تكون متاحة للأدمن ليُنشئ منها منتجات للبيع.
    """

    __tablename__ = "provider_services"
    __table_args__ = (
        UniqueConstraint(
            "api_provider_id",
            "external_service_id",
            name="uq_provider_service",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    api_provider_id: Mapped[int] = mapped_column(ForeignKey("api_providers.id"), index=True)

    external_service_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(500))
    # الاسم العربي المُعرَّب عند السحب (للعرض والبيع والبحث).
    # الاسم الأصلي يبقى في ``name`` ولا يُمسّ شيء لدى المزود.
    name_ar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    service_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    rate: Mapped[Decimal] = mapped_column(MONEY)
    rate_usd: Mapped[Decimal] = mapped_column(MONEY)
    price_type: Mapped[ProviderPriceType] = mapped_column(
        SAEnum(ProviderPriceType),
        default=ProviderPriceType.PER_1000,
    )

    min_quantity: Mapped[int] = mapped_column(Integer, default=1)
    max_quantity: Mapped[int] = mapped_column(Integer, default=1000000)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    requires_link: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_quantity: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_player_id: Mapped[bool] = mapped_column(Boolean, default=False)

    supports_refill: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_cancel: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[ProviderServiceStatus] = mapped_column(
        SAEnum(ProviderServiceStatus),
        default=ProviderServiceStatus.ACTIVE,
    )

    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime,
        onupdate=func.now(),
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    api_provider: Mapped["ApiProvider"] = relationship(back_populates="services")
    products: Mapped[list["Product"]] = relationship(back_populates="provider_service")


class Product(Base):
    """
    المنتجات القابلة للشراء (محسّنة).
    تنتمي لقسم فرعي وترتبط بخدمة مزود (provider_service).
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sub_category_id: Mapped[int] = mapped_column(ForeignKey("sub_categories.id"), index=True)
    api_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_providers.id"), nullable=True
    )
    provider_service_ref_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_services.id"), nullable=True
    )

    provider_service_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    estimated_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    cost_price_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    pricing_type: Mapped[ProductPricingType] = mapped_column(
        SAEnum(ProductPricingType),
        default=ProductPricingType.FIXED,
    )
    profit_margin_percent: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    # True = الأدمن ضبط هامش المنتج يدوياً (لا يُلمس عند تغيير هامش قسمه).
    # False = هامش ضمني/تلقائي من السحب (يتأثر بهامش القسم الفرعي/القسم).
    margin_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    display_type: Mapped[ProductDisplayType] = mapped_column(
        SAEnum(ProductDisplayType),
        default=ProductDisplayType.PER_1000,
    )

    min_quantity: Mapped[int] = mapped_column(Integer, default=1)
    max_quantity: Mapped[int] = mapped_column(Integer, default=1)

    requires_player_id: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_link: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_quantity: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[ProductStatus] = mapped_column(
        SAEnum(ProductStatus), default=ProductStatus.ACTIVE
    )
    fulfillment_type: Mapped[ProductFulfillmentType] = mapped_column(
        SAEnum(ProductFulfillmentType),
        default=ProductFulfillmentType.API,
    )
    # منتج أُنشئ تلقائياً من «أول N خدمات مسحوبة» في أقسام الرشق الداخلية.
    # يستخدمه البناء التلقائي لإعادة الترتيب/الإخفاء عند تغيّر أسعار المزود،
    # ويبقى مميزاً عن المنتجات التي ينشرها الأدمن يدوياً بسعره الخاص.
    is_auto_published: Mapped[bool] = mapped_column(Boolean, default=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bestseller: Mapped[bool] = mapped_column(Boolean, default=False)

    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    total_sold: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sub_category: Mapped["SubCategory"] = relationship(back_populates="products")
    api_provider: Mapped["ApiProvider | None"] = relationship(back_populates="products")
    provider_service: Mapped["ProviderService | None"] = relationship(back_populates="products")
    inventory_items: Mapped[list["DigitalInventoryItem"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
    )
    promotions: Mapped[list["Promotion"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
    )
    cart_items: Mapped[list["CartItem"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
    )


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_cart_user_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, onupdate=func.now(), server_default=func.now()
    )

    product: Mapped["Product"] = relationship(back_populates="cart_items")


class Promotion(Base):
    """عرض زمني ديناميكي على منتج محدد."""

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    discount_type: Mapped[PromotionDiscountType] = mapped_column(SAEnum(PromotionDiscountType))
    discount_value: Mapped[Decimal] = mapped_column(MONEY)
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="promotions")


class DigitalInventoryItem(Base):
    """كود/ترخيص رقمي مؤمّن من مخزون منتج شرعي."""

    __tablename__ = "digital_inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    encrypted_value: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[InventoryItemStatus] = mapped_column(
        SAEnum(InventoryItemStatus),
        default=InventoryItemStatus.AVAILABLE,
        index=True,
    )
    unified_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("unified_orders.id"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    product: Mapped["Product"] = relationship(back_populates="inventory_items")


class ProductWatch(Base):
    """اشتراك مستخدم بتنبيه تغير السعر أو عودة المخزون."""

    __tablename__ = "product_watches"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_product_watch"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    last_seen_price: Mapped[Decimal] = mapped_column(MONEY)
    last_seen_stock: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()
    product: Mapped["Product"] = relationship()


class ProductReview(Base):
    """تقييم المستخدم لمنتج بعد طلب مكتمل."""

    __tablename__ = "product_reviews"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_product_review"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    unified_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("unified_orders.id"), nullable=True
    )
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()
    product: Mapped["Product"] = relationship()


class GiftCode(Base):
    """بطاقة هدية صادرة من الإدارة."""

    __tablename__ = "gift_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GiftRedemption(Base):
    __tablename__ = "gift_redemptions"
    __table_args__ = (UniqueConstraint("gift_code_id", "user_id", name="uq_gift_code_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    gift_code_id: Mapped[int] = mapped_column(
        ForeignKey("gift_codes.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    redeemed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Challenge(Base):
    """تحدٍ تسويقي/تفاعلي قابل للإدارة من لوحة الأدمن."""

    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(500))
    event_type: Mapped[str] = mapped_column(String(32))
    target_value: Mapped[int] = mapped_column(Integer, default=1)
    reward_points: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ChallengeStatus] = mapped_column(
        SAEnum(ChallengeStatus), default=ChallengeStatus.ACTIVE, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChallengeProgress(Base):
    __tablename__ = "challenge_progress"
    __table_args__ = (UniqueConstraint("challenge_id", "user_id", name="uq_challenge_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    challenge_id: Mapped[int] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AbuseEvent(Base):
    """حدث إساءة استخدام محفوظ للمراجعة والتحليل."""

    __tablename__ = "abuse_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[int] = mapped_column(Integer, default=1)
    details: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class ResellerAccount(Base):
    """حساب ريسيلر مرتبط بمستخدم مع مفاتيح API منفصلة."""

    __tablename__ = "reseller_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    markup_percent: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("10"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()
    keys: Mapped[list["ResellerApiKey"]] = relationship(
        back_populates="reseller", cascade="all, delete-orphan"
    )


class ResellerApiKey(Base):
    __tablename__ = "reseller_api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    reseller_id: Mapped[int] = mapped_column(
        ForeignKey("reseller_accounts.id", ondelete="CASCADE"), index=True
    )
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16))
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    reseller: Mapped["ResellerAccount"] = relationship(back_populates="keys")


class UserFavorite(Base):
    """
    منتجات المفضلة عند المستخدم.
    """

    __tablename__ = "user_favorites"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "product_id",
            name="uq_user_product_favorite",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()
    product: Mapped["Product"] = relationship()


class UnifiedOrder(Base):
    """
    الطلبات الموحدة للألعاب والتطبيقات والـ SMM.
    الأرقام لها جدول NumberOrder المنفصل.
    """

    __tablename__ = "unified_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # nullable لأن بعض أنواع الطلبات (مثل التطبيقات والأكواد الجاهزة) لا
    # ترتبط بمنتج حقيقي في جدول products. القيمة 0 كانت تُستخدم سابقاً في
    # تلك الحالات ففشلت قيد FOREIGN KEY.
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    api_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_providers.id"), nullable=True
    )
    promotion_id: Mapped[int | None] = mapped_column(ForeignKey("promotions.id"), nullable=True)

    external_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    cost_price_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    status: Mapped[UnifiedOrderStatus] = mapped_column(
        SAEnum(UnifiedOrderStatus), default=UnifiedOrderStatus.PENDING
    )
    status_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    start_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remains: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── ضمان التعويض الآلي (refill) ──
    refill_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_refill_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── ضمان المفتاح: عدد مرات تبديل الكود الفاسد ──
    key_swaps: Mapped[int] = mapped_column(Integer, default=0)

    # ── التدريج المجدول (Drip-Feed) ──
    # الطلب الأب quantity=0 وحالته PROCESSING، وكل دفعة صف مستقل
    # يشير إليه بـ drip_parent_id ويُنفَّذ عند حلول موعده.
    drip_parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("unified_orders.id"), nullable=True, index=True
    )
    drip_run_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drip_total_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drip_scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()
    product: Mapped["Product"] = relationship()
    api_provider: Mapped["ApiProvider | None"] = relationship()


class StarsPackage(Base):
    """
    باقات شحن الرصيد بنجوم تليجرام.
    تُدار بالكامل من لوحة الأدمن.
    """

    __tablename__ = "stars_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    stars_amount: Mapped[int] = mapped_column(Integer)
    usd_amount: Mapped[Decimal] = mapped_column(MONEY)
    label: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    discount_type: Mapped[str] = mapped_column(String(16), default="percent")
    discount_value: Mapped[Decimal] = mapped_column(MONEY)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    min_order_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    usages: Mapped[list["CouponUsage"]] = relationship(back_populates="coupon")


class CouponUsage(Base):
    __tablename__ = "coupon_usages"
    __table_args__ = (UniqueConstraint("coupon_id", "user_id", name="uq_coupon_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int] = mapped_column(ForeignKey("coupons.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    discount_applied: Mapped[Decimal] = mapped_column(MONEY)
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    coupon: Mapped["Coupon"] = relationship(back_populates="usages")


class CashbackLog(Base):
    __tablename__ = "cashback_logs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "order_id",
            "order_type",
            name="uq_cashback_order",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(32))
    order_amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    cashback_usd: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AutoInvoice(Base):
    """
    الفواتير التلقائية لطرق الدفع الآلية.
    تُستخدم مع Sam API (شام كاش) و Cryptomus (USDT).
    """

    __tablename__ = "auto_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    method: Mapped[AutoInvoiceMethod] = mapped_column(SAEnum(AutoInvoiceMethod))

    external_invoice_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    amount_original: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8))

    network: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payment_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    qr_code_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[AutoInvoiceStatus] = mapped_column(
        SAEnum(AutoInvoiceStatus),
        default=AutoInvoiceStatus.PENDING,
    )
    transaction_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    status_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship()


class RateLimitLog(Base):
    __tablename__ = "rate_limit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    """
    سجل تعديلات الأدمن.
    يسجل كل تعديل مهم قام به أي أدمن.

    مثال:
    - أنشأ قسم جديد
    - عدل سعر منتج
    - حذف مزود
    - غيّر إعداد
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[AuditAction] = mapped_column(SAEnum(AuditAction))

    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    admin: Mapped["User"] = relationship()


class TaskType(str, enum.Enum):
    """أنواع المهام المدعومة، ولكل نوع آلية تحقق ذكية خاصة به."""

    DAILY_CHECKIN = "daily_checkin"          # مرة يومياً
    FIRST_DEPOSIT = "first_deposit"          # أول عملية شحن
    INVITE_FRIEND = "invite_friend"          # إحالة صديق (تُحتسب بعد تفعيله)
    REVIEW_PRODUCT = "review_product"        # تقييم منتج بعد طلب مكتمل
    JOIN_CHANNEL = "join_channel"            # الاشتراك بقناة (يُتحقق منه فعلياً)
    REPORT_PROVIDER = "report_provider"      # إبلاغ عن مزود سيء (يحتاج موافقة أدمن)
    TRANSLATE_TEXT = "translate_text"        # ترجمة نص (يحتاج موافقة أدمن)
    WATCH_AD = "watch_ad"                    # مشاهدة إعلان/رابط
    PROFILE_COMPLETE = "profile_complete"    # إكمال الملف (لغة/عملة/حسابات)
    CUSTOM = "custom"                        # مهمة يحددها الأدمن بالكامل


class TaskRewardType(str, enum.Enum):
    POINTS = "points"
    BALANCE_USD = "balance_usd"


class TaskVerification(str, enum.Enum):
    """كيف يتحقق البوت من إتمام المهمة قبل صرف المكافأة."""

    NONE = "none"                  # بلا تحقق (مهام آمنة)
    ONCE_PER_DAY = "once_per_day"  # مرة واحدة في اليوم
    ONCE_PER_USER = "once_per_user"
    ADMIN_APPROVAL = "admin_approval"
    TELEGRAM_MEMBERSHIP = "telegram_membership"
    PROOF_ORDER = "proof_order"    # يتطلب طلباً مكتملاً فعلياً
    CAPTCHA = "captcha"            # تحدٍّ حسابي بسيط ضد البوتات
    COOLDOWN_MINUTES = "cooldown"


class MarketListingStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"   # بانتظار موافقة الأدمن وتحديد العمولة
    APPROVED = "approved"               # منشور لكل المستخدمين
    REJECTED = "rejected"
    SOLD = "sold"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class MarketListingKind(str, enum.Enum):
    GAME_ACCOUNT = "game_account"       # حساب لعبة (يُسلَّم عبر الأدمن وسيطاً)
    DIGITAL_CODE = "digital_code"       # كود/بطاقة (يُسلَّم آلياً ومشفراً)
    SMS_NUMBER = "sms_number"           # رقم (يُتحقق من ملكيته قبل العرض)
    SERVICE = "service"                 # خدمة ينفذها البائع للمشتري
    OTHER = "other"


class EscrowStatus(str, enum.Enum):
    AWAITING_BUYER = "awaiting_buyer"
    FUNDED = "funded"             # أموال المشتري محجوزة عند البوت
    DELIVERED = "delivered"       # البائع سلّم
    RELEASED = "released"         # أُفرج عن المبلغ للبائع
    REFUNDED = "refunded"         # أُعيد للمشتري
    DISPUTED = "disputed"


# ═══════════════════════════ Feature Flags ═══════════════════════════


class FeatureFlag(Base):
    """
    مفتاح تفعيل/تعطيل لكل إضافة جديدة في البوت.

    كل ميزة جديدة تُسجَّل هنا فيمكن للأدمن:
    - تفعيلها أو إيقافها فوراً بدون إعادة تشغيل.
    - تعديل إعداداتها الخاصة (config_json).

    السجل المرجعي للقيم الافتراضية موجود في services/feature_registry.py،
    وهذا الجدول يحفظ تجاوزات الأدمن فقط.
    """

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class FeatureEvent(Base):
    """
    سجل أحداث الميزات: يقيس استخدام كل ميزة حتى تظهر للأدمن
    أيها مستعمل فعلاً وأيها ميت، قبل أن يقرر إيقافها أو تطويرها.
    """

    __tablename__ = "feature_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Task(Base):
    """
    مهمة يحددها الأدمن بالكامل: العنوان، النوع، قيمة النقاط،
    الحد اليومي، وآلية التحقق.

    الأدمن يتحكم بكل شيء من لوحة «مركز المهام» بدون لمس الكود.
    """

    __tablename__ = "tasks_catalog"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title_ar: Mapped[str] = mapped_column(String(128))
    title_en: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description_ar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    emoji: Mapped[str] = mapped_column(String(8), default="🎯")

    task_type: Mapped[TaskType] = mapped_column(SAEnum(TaskType), default=TaskType.CUSTOM)
    reward_type: Mapped[TaskRewardType] = mapped_column(
        SAEnum(TaskRewardType), default=TaskRewardType.POINTS
    )
    reward_points: Mapped[int] = mapped_column(Integer, default=10)
    reward_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    verification: Mapped[TaskVerification] = mapped_column(
        SAEnum(TaskVerification), default=TaskVerification.NONE
    )
    # قيمة إضافية حسب نوع التحقق:
    # - COOLDOWN_MINUTES: عدد الدقائق
    # - TELEGRAM_MEMBERSHIP: chat_id المطلوب
    # - PROOF_ORDER: الحد الأدنى لقيمة الطلب
    verification_target: Mapped[str | None] = mapped_column(String(255), nullable=True)

    daily_limit: Mapped[int] = mapped_column(Integer, default=1)
    total_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = بلا حد
    min_account_age_hours: Mapped[int] = mapped_column(Integer, default=0)
    min_orders_required: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TaskProgress(Base):
    """تقدّم كل مستخدم في كل مهمة، مع منع الاحتساب المزدوج."""

    __tablename__ = "task_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "task_id", "period_key", name="uq_task_progress"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks_catalog.id"), index=True)
    # "daily:2026-08-25" للمهام اليومية، "total" للمهام العامة
    period_key: Mapped[str] = mapped_column(String(32), default="total")
    completions: Mapped[int] = mapped_column(Integer, default=0)
    points_earned: Mapped[int] = mapped_column(Integer, default=0)
    usd_earned: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship()
    task: Mapped["Task"] = relationship()


class TaskSubmission(Base):
    """
    تقديمات تحتاج موافقة الأدمن (ترجمة/إبلاغ عن مزود/مهام مخصصة).
    لا تُصرف المكافأة إلا بعد الموافقة.
    """

    __tablename__ = "task_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks_catalog.id"), index=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    admin_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()
    task: Mapped["Task"] = relationship()


class MarketListing(Base):
    """
    إعلان في سوق المستخدمين.

    دورة الحياة:
    DRAFT -> PENDING_REVIEW (وصل لإشعارات الأدمن بكل التفاصيل)
          -> APPROVED (الأدمن حدد العمولة وسمح بالنشر) أو REJECTED
          -> SOLD / CANCELLED / EXPIRED

    العمولة يحددها الأدمن لكل إعلان على حدة قبل النشر، وتُضاف فوق
    سعر البائع، فالمشتري يدفع (السعر + العمولة) ولا تُخصم من البائع.
    """

    __tablename__ = "market_listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    kind: Mapped[MarketListingKind] = mapped_column(
        SAEnum(MarketListingKind), default=MarketListingKind.OTHER
    )
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    seller_price_usd: Mapped[Decimal] = mapped_column(MONEY)
    commission_percent: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    # للأكواد الرقمية: يُخزَّن مشفراً ولا يُكشف إلا للمشتري بعد الدفع.
    secret_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    # إثبات ملكية (رقم طلب رقم SMS سابق، أو نص تحقق).
    ownership_proof: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[MarketListingStatus] = mapped_column(
        SAEnum(MarketListingStatus), default=MarketListingStatus.DRAFT, index=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    view_count: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    seller: Mapped["User"] = relationship()
    photos: Mapped[list["MarketListingPhoto"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class MarketListingPhoto(Base):
    """صور الإعلان (file_id من تليجرام حتى لا نستهلك تخزيناً)."""

    __tablename__ = "market_listing_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("market_listings.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    listing: Mapped["MarketListing"] = relationship(back_populates="photos")


class MarketTransaction(Base):
    """
    عملية شراء في السوق مع ضمان الوسيط (Escrow).

    الأموال تُخصم من المشتري فوراً وتُحتجز عند البوت، ثم:
    - تُفرج للبائع ناقص العمولة بعد تأكيد التسليم.
    - أو تُعاد للمشتري إذا فشل التسليم أو كسب النزاع.

    العمولة تُسجَّل في صف مستقل حتى لا تضيع أبداً ولا تُحتسب مرتين.
    """

    __tablename__ = "market_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("market_listings.id"), index=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    seller_price_usd: Mapped[Decimal] = mapped_column(MONEY)
    commission_percent: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    commission_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    total_charged_usd: Mapped[Decimal] = mapped_column(MONEY)
    points_used: Mapped[int] = mapped_column(Integer, default=0)
    points_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    status: Mapped[EscrowStatus] = mapped_column(
        SAEnum(EscrowStatus), default=EscrowStatus.AWAITING_BUYER, index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispute_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    listing: Mapped["MarketListing"] = relationship()
    seller: Mapped["User"] = relationship(foreign_keys=[seller_id])
    buyer: Mapped["User"] = relationship(foreign_keys=[buyer_id])


class MarketProfile(Base):
    """حساب سوق منفصل باسم مستعار وكلمة سر لحماية هوية التاجر."""

    __tablename__ = "market_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    alias: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    successful_sales: Mapped[int] = mapped_column(Integer, default=0)
    failed_sales: Mapped[int] = mapped_column(Integer, default=0)
    disputes_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class WithdrawalRequest(Base):
    """طلب سحب رصيد من التاجر/المستخدم يراجعه الأدمن يدوياً."""

    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column(String(32))  # shamcash / usdt
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    network: Mapped[str | None] = mapped_column(String(16), nullable=True)
    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    payout_amount: Mapped[Decimal] = mapped_column(MONEY)
    payout_address: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class ProductProviderRoute(Base):
    """
    مسار بديل لتنفيذ منتج عند مزود آخر.

    المشكلة التي يحلها هذا الجدول: `Product.api_provider_id` مفتاح أجنبي
    واحد، فإن تعطّل ذلك المزود صار المنتج «غير جاهز للطلب» رغم أن نفس
    الخدمة متوفرة عند مزودين آخرين مسجّلين في النظام.

    كل صف يربط المنتج بمزود بديل **وبمعرف الخدمة عنده تحديداً**، لأن
    `provider_service_id` يختلف من مزود لآخر حتى للخدمة نفسها.
    المسار الأساسي يبقى في Product نفسه، وهذه صفوف احتياطية مرتبة.
    """

    __tablename__ = "product_provider_routes"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "api_provider_id", name="uq_product_provider_route"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    api_provider_id: Mapped[int] = mapped_column(
        ForeignKey("api_providers.id", ondelete="CASCADE"), index=True
    )
    provider_service_id: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product"] = relationship()
    api_provider: Mapped["ApiProvider"] = relationship()


class SubscriptionPlan(Base):
    """اشتراك منتج رقمي بمدة وسعر محددين (نتفلكس/سبوتيفاي/أدوات AI)."""

    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    days: Mapped[int] = mapped_column(Integer, default=30)
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    auto_renew_default: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    product: Mapped["Product"] = relationship()


class UserSubscription(Base):
    """
    اشتراك مستخدم فعلي مع تاريخ انتهائه.

    بدون هذا الجدول لا يعرف البوت متى ينتهي اشتراك المستخدم، فلا يستطيع
    تنبيهه ولا تجديده، ويضيع الإيراد المتكرر.
    """

    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscription_plans.id"), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    last_reminded_days_left: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()
    product: Mapped["Product"] = relationship()
    plan: Mapped["SubscriptionPlan | None"] = relationship()


class PriceLimitOrder(Base):
    """
    أمر حدّ في بورصة الأرقام: «اشترِ N رقم تلقائياً حين ينزل السعر تحت X».
    يُنفَّذ آلياً عند تحقق الشرط بدل أن يراقب المستخدم الشاشة.
    """

    __tablename__ = "price_limit_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    service_code: Mapped[str] = mapped_column(String(32), index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    target_price_usd: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    triggered_price_usd: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class VipCertificate(Base):
    """
    شهادة ملكية دائمة لرقم نادر، قابلة للتحويل بين المستخدمين.
    التسلسلي هو ما يُتداول، لا الرقم نفسه.
    """

    __tablename__ = "vip_certificates"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    serial: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    transferred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    owner: Mapped["User"] = relationship()


class EscrowHold(Base):
    """
    حجز أموال مستقل قابل لإعادة الاستخدام في أي تدفق (مزادات، خدمات
    مخصصة، اتفاقات بين المستخدمين). القاعدة: لا يُفرج إلا عبر release()
    الذي يقتطع العمولة في نفس اللحظة، فلا مسار يدفع كامل المبلغ.
    """

    __tablename__ = "escrow_holds"

    id: Mapped[int] = mapped_column(primary_key=True)
    payer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    commission_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    purpose: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="held", index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    payer: Mapped["User"] = relationship()


class P2PCodeListing(Base):
    """
    كود يعرضه مستخدم للبيع. يُخزَّن مشفراً لحظة العرض، فلا يستطيع
    البائع سحبه بعد البيع، وتُكشف قيمته للمشتري فقط بعد الخصم.
    """

    __tablename__ = "p2p_code_listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    buyer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(128))
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    commission_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    encrypted_code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    seller: Mapped["User"] = relationship(foreign_keys=[seller_id])
    buyer: Mapped["User | None"] = relationship(foreign_keys=[buyer_id])


class AutonomousJob(Base):
    """
    مهمة شراء مستقلة متكررة: «اشترِ N رقم كل X ساعة لمدة Y دورة،
    وتوقف لو ارتفع السعر فوق حد».

    فحص السعر قبل الشراء هو جوهر الفكرة: الوكيل لا ينفذ أعمى، بل
    يتخطى الدورة حين يتجاوز السعر الحد الذي وضعه المستخدم.
    """

    __tablename__ = "autonomous_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    service_code: Mapped[str] = mapped_column(String(32))
    country_code: Mapped[str] = mapped_column(String(8))
    quantity_per_run: Mapped[int] = mapped_column(Integer, default=1)
    total_runs: Mapped[int] = mapped_column(Integer, default=1)
    completed_runs: Mapped[int] = mapped_column(Integer, default=0)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    max_unit_price_usd: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class JurisdictionRule(Base):
    """
    قاعدة امتثال لكل دولة: ما يُسمح بيعه، ومستوى التوثيق المطلوب.
    تُطبَّق قبل الشراء لا بعد المشكلة.
    """

    __tablename__ = "jurisdiction_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    blocked_services: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_kyc_tier: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookEndpoint(Base):
    """نقطة ويبhook لمطوّر، بمفتاح توقيع وصلاحيات دقيقة."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(String(128))
    scopes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class WebhookDelivery(Base):
    """سجل كل محاولة تسليم — للتشخيص وإعادة المحاولة."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(64))
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Tenant(Base):
    """
    مستأجر في مصنع العلامات البيضاء: بوت بعلامته ولغته وهامشه.
    يحسم من bot_username أي علامة يخدم هذا الطلب.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    brand_name: Mapped[str] = mapped_column(String(128))
    bot_username: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    default_language: Mapped[str] = mapped_column(String(8), default="ar")
    display_currency: Mapped[str] = mapped_column(String(8), default="USD")
    commission_percent: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProviderBid(Base):
    """
    عرض سعر ساري المفعول من مزود. الأحدث يستبدل الأقدم لكل
    مزود/خدمة/دولة، وأرخص عرض ساري هو ما يُعتمد.
    """

    __tablename__ = "provider_bids"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("api_providers.id"), index=True)
    service_code: Mapped[str] = mapped_column(String(32), index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RevenueShareToken(Base):
    """
    سهم من حصة إحالة: المصدر يقبض كاشاً الآن، والحامل يأخذ النسبة
    من العمولات المستقبلية. قابل للتداول بين المستخدمين.
    """

    __tablename__ = "revenue_share_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    holder_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    share_percent: Mapped[int] = mapped_column(Integer, default=5)
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    issuer: Mapped["User"] = relationship(foreign_keys=[issuer_id])
    holder: Mapped["User | None"] = relationship(foreign_keys=[holder_id])


class PurchaseRoom(Base):
    """
    غرفة شراء جماعي: مستخدمون يتجمعون على طلب كبير حتى يكتمل النصاب
    فيُفتح خصم الجملة. النمو يأتي من الدعوات لا من الإعلانات.
    """

    __tablename__ = "purchase_rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    target_quantity: Mapped[int] = mapped_column(Integer, default=1000)
    members: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    creator: Mapped["User"] = relationship()
    product: Mapped["Product"] = relationship()


class ExperimentResult(Base):
    """نتيجة عرض واحد في اختبار A/B — تُجمع لتحديد الفائز."""

    __tablename__ = "experiment_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment: Mapped[str] = mapped_column(String(64), index=True)
    variant: Mapped[str] = mapped_column(String(32), index=True)
    converted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WarmPoolNumber(Base):
    """
    رقم مُشترى مسبقاً وجاهز للتسليم الفوري.

    جدول مستقل لأن `number_orders.user_id` غير قابل للفراغ، والرقم في
    البركة لا مالك له بعد. عند الإسناد يُنشأ NumberOrder حقيقي ويُحذف
    الصف من هنا.
    """

    __tablename__ = "warm_pool_numbers"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[ProviderName] = mapped_column(SAEnum(ProviderName))
    provider_order_id: Mapped[str] = mapped_column(String(64))
    phone_number: Mapped[str] = mapped_column(String(32), index=True)
    service_code: Mapped[str] = mapped_column(String(32), index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    cost_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReadyCodeItem(Base):
    """عناصر قسم التطبيقات والأكواد الجاهزة."""
    __tablename__ = "ready_code_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, onupdate=func.now(), server_default=func.now())


class ReferralAbuseLog(Base):
    """سجل إساءة استعمال الإحالة."""
    __tablename__ = "referral_abuse_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    joiner_telegram_id: Mapped[int] = mapped_column(BigInteger)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    referrer: Mapped["User"] = relationship()


class UnifiedRefund(Base):
    """
    سجل الاسترجاع الموحّد لكل البوت.

    قبل هذا الجدول كان لكل ميزة مسار استرجاع خاص، فلم يكن ممكناً
    الإجابة عن «كم استرجعنا هذا الشهر ولماذا». الآن كل استرجاع يمر
    من هنا بمفتاح idempotent، فلا يُدفع مرتين.
    """

    __tablename__ = "unified_refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[int] = mapped_column(Integer)
    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    reason: Mapped[str] = mapped_column(String(32), index=True)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    user: Mapped["User"] = relationship()


# ══════════════════════════════════════════════════════════════
#  القسم الرئيسي للذكاء الاصطناعي (برمجة / دردشة / أقسام مستقبلية)
# ══════════════════════════════════════════════════════════════


class AiSection(Base):
    """
    قسم واحد داخل القسم الرئيسي للذكاء الاصطناعي.

    الأدمن ينشئ القسم من لوحة الأدمن: الاسم، الوصف، نموذج NanoGPT،
    تكلفة الرسالة التقريبية، ومضاعف الربح. سعر البيع للمستخدم =
    التكلفة + (التكلفة × مضاعف الربح)، ويُخصم من رصيد المستخدم
    لكل رسالة ناجحة.
    """

    __tablename__ = "ai_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    name_en: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # شرح يدوي من الأدمن: ماذا يسوي القسم وما مهمته.
    description_ar: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    # coding = يولد كود/ملفات ويرسلها كملف، chat = محادثة عادية.
    kind: Mapped[str] = mapped_column(String(16), default="chat")
    # نموذج NanoGPT المحدد لهذا القسم (يضيفه الأدمن من اللوحة).
    model: Mapped[str] = mapped_column(String(128))
    # التكلفة التقريبية للرسالة عند المزود (الدولار).
    cost_per_message_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.01"))
    # مضاعف الربح: سعر البيع = التكلفة × (1 + المضاعف). الافتراضي 3.
    profit_multiplier: Mapped[float] = mapped_column(Float, default=3.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list["AiSession"]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )

    @property
    def sell_price_usd(self) -> Decimal:
        """سعر الرسالة للمستخدم: تكلفة المزود + الربح."""
        return (
            self.cost_per_message_usd * (Decimal(1) + Decimal(str(self.profit_multiplier)))
        ).quantize(Decimal("0.0001"))


class AiSession(Base):
    """
    جلسة محادثة لمستخدم داخل قسم ذكاء اصطناعي محدد.

    كل قسم + مستخدم = جلسة واحدة نشطة (الأحدث). تُحفظ الرسائل
    داخلها لسهولة الرجوع إليها.
    """

    __tablename__ = "ai_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("ai_sections.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), index=True
    )

    user: Mapped["User"] = relationship()
    section: Mapped["AiSection"] = relationship(back_populates="sessions")
    messages: Mapped[list["AiMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AiMessage(Base):
    """رسالة واحدة داخل جلسة ذكاء اصطناعي (user أو assistant)."""

    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("ai_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    # تكلفة الرسالة عند المزود (لحساب الإيراد/الربح). يُملأ على رد المساعد.
    cost_usd: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped["AiSession"] = relationship(back_populates="messages")


# ══════════════════════════════════════════════════════════════
#  قسم واتساب (مربوط ببوت الجسر الثاني — باقات يومية)
# ══════════════════════════════════════════════════════════════


class WaLinkState(str, enum.Enum):
    NONE = "none"          # لم يطلب الربط بعد
    PENDING = "pending"    # أرسل الكود للمستخدم ولم يكمل
    LINKED = "linked"      # الجلسة مربوطة وتشتغل
    EXPIRED = "expired"    # انتهت جلسة الواتساب عند المزود


class WaSubscription(Base):
    """
    اشتراك مستخدم واحد بقسم واتساب (صف واحد لكل مستخدم).

    الباقة (يوم/3/7/30 يوم) تمدد active_until، والتجديد التلقائي
    يخصم price_per_day كل يوم قبل الانتهاء بساعات محددة.
    """

    __tablename__ = "wa_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    # رقم الواتساب الذي طلب المستخدم ربطه.
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    link_state: Mapped[str] = mapped_column(
        String(16), default=WaLinkState.NONE.value, index=True
    )
    # صلاحية الباقة الحالية (null = بلا اشتراك نشط).
    active_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    # آخر باقة اشتراها (للإظهار).
    last_package_days: Mapped[int] = mapped_column(Integer, default=1)
    last_renewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # منع تكرار تنبيه الانتهاء.
    expire_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship()


# ══════════════════════════════════════════════════════════════
#  الإضافات الجديدة (مكافآت الشحن، عجلة الحظ، إنذارات السعر،
#  ألقاب المشترين، التحديات الأسبوعية، سوق الأرقام المستعملة،
#  تقييم المزودين)
# ══════════════════════════════════════════════════════════════


class DepositBonusRule(Base):
    """
    قاعدة مكافأة شحن: إذا أودع المستخدم مبلغاً >= min_deposit_usd،
    تُضاف له نسبة مئوية bonus_percent كرصيد مجاني.

    الأدمن يتحكم بالكامل: إضافة/تعديل/تفعيل/حذف من لوحة الأدمن.
    """

    __tablename__ = "deposit_bonus_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    min_deposit_usd: Mapped[Decimal] = mapped_column(MONEY)
    bonus_percent: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("5"))
    # المكافأة القصوى بالدولار (0 = بلا حد)
    max_bonus_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DepositBonusGrant(Base):
    """
    سجل مكافأة شحن مُصرَّفة — يمنع تكرار المكافأة لنفس الإيداع
    ويُحسب إجمالي المكافآت الموزعة.
    """

    __tablename__ = "deposit_bonus_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    deposit_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # نوع الإيداع (deposit_requests / auto_invoices / stars)
    deposit_source: Mapped[str] = mapped_column(String(32), default="deposit")
    deposit_amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    bonus_usd: Mapped[Decimal] = mapped_column(MONEY)
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class SpinPrize(Base):
    """
    جائزة داخل عجلة الحظ. الأدمن يتحكم بكل شيء:
    الاسم، النوع (رصيد/نقاط/بلا شيء)، القيمة، الاحتمال.
    """

    __tablename__ = "spin_prizes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    # balance / points / loyalty_points / nothing
    prize_type: Mapped[str] = mapped_column(String(24), default="balance")
    # القيمة: دولار (balance) أو نقاط (points)
    value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    # الاحتمال المرجح شكله 100 / المجموع
    weight: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SpinHistory(Base):
    """
    سجل لفات المستخدمين — مع قيد منع اللف المتكرر لنفس اليوم.
    """

    __tablename__ = "spin_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    prize_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prize_type: Mapped[str] = mapped_column(String(24))
    prize_label: Mapped[str] = mapped_column(String(128))
    value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    day_key: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class PriceAlert(Base):
    """
    تنبيه سعر: المستخدم يحدد خدمة + دولة + سعر مستهدف،
    ويُشعَر تلقائياً عندما ينخفض السعر عن الهدف.
    """

    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    service_code: Mapped[str] = mapped_column(String(32), index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    target_price_usd: Mapped[Decimal] = mapped_column(MONEY)
    last_checked_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class WeeklyChallenge(Base):
    """
    تحدٍّ أسبوعي: الأدمن يحدد الهدف (عدد الطلبات/الصرف/الإحالات)
    والمكافأة. يُحصى التقدم تلقائياً من الأحداث الحقيقية.
    """

    __tablename__ = "weekly_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(500))
    emoji: Mapped[str] = mapped_column(String(8), default="🏆")
    # orders / spend_usd / checkins / referrals
    metric: Mapped[str] = mapped_column(String(32), default="orders")
    target_value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("10"))
    reward_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("1"))
    reward_points: Mapped[int] = mapped_column(Integer, default=0)
    week_start: Mapped[str] = mapped_column(String(16), index=True)  # "2026-W37"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WeeklyChallengeProgress(Base):
    """
    تقدّم مستخدم داخل تحدٍّ أسبوعي. قيد مركب يمنع الحساب المزدوج.
    """

    __tablename__ = "weekly_challenge_progress"
    __table_args__ = (
        UniqueConstraint(
            "challenge_id", "user_id", name="uq_weekly_challenge_user"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    challenge_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_challenges.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    progress: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    challenge: Mapped["WeeklyChallenge"] = relationship()
    user: Mapped["User"] = relationship()


class NumberResaleListing(Base):
    """
    رقم مستعمل يعرضه مالكه للبيع بسعر يحدده (أرخص من الشراء الجديد).
    المشتري يشتريه بضغطة واحدة من رصيده، والبائع يستلم رصيداً فوراً بعد خصم عمولة.
    """

    __tablename__ = "number_resale_listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    buyer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # رقم الطلب الأصلي (رقم SMS) الذي يثبت الملكية
    source_order_id: Mapped[int] = mapped_column(Integer, index=True)
    phone_number: Mapped[str] = mapped_column(String(32))
    service_name: Mapped[str] = mapped_column(String(64))
    country_name: Mapped[str] = mapped_column(String(64))
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    commission_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open/sold/cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    seller: Mapped["User"] = relationship(foreign_keys=[seller_id])
    buyer: Mapped["User | None"] = relationship(foreign_keys=[buyer_id])


class ProviderReview(Base):
    """
    تقييم المستخدم لمزود أرقام/خدمة بعد طلب مكتمل:
    نجوم + تعليق. يعرض المتوسط لكل مزود قبل الشراء.
    """

    __tablename__ = "provider_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    order_type: Mapped[str] = mapped_column(String(16), default="number")  # number/unified
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1..5
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class CampaignCode(Base):
    """
    كود خصم خاص بحملة إعلانية مع وسم تتبّع المصدر.
    نفس منطق الكوبون لكن بترميز (حركة#حملة) ليعرف الأدمن كم طلب
    جلبت كل حملة إعلانية — التناسب مع «دفعة النمو» في البوت.
    """

    __tablename__ = "campaign_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    tracking: Mapped[str] = mapped_column(String(64), default="", index=True)
    discount_type: Mapped[str] = mapped_column(String(16), default="percent")
    discount_value: Mapped[Decimal] = mapped_column(MONEY)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    min_order_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    usages: Mapped[list["CampaignCodeUsage"]] = relationship(back_populates="campaign")


class CampaignCodeUsage(Base):
    __tablename__ = "campaign_code_usages"
    __table_args__ = (
        UniqueConstraint("campaign_id", "user_id", name="uq_campaign_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign_codes.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    discount_applied: Mapped[Decimal] = mapped_column(MONEY)
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    campaign: Mapped["CampaignCode"] = relationship(back_populates="usages")


class TopupGiftRequest(Base):
    """
    طلب «اشحن لأهلك» — تحويلات الشتات.
    المستخدم يختار مشغّل الجوال في الوطن والمبلغ ورقم المستلم،
    والطلب يصل للوحة الأدمن ليُعتمد (المشغل لا يكمل الشبكة تلقائياً).
    """

    __tablename__ = "topup_gift_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    operator: Mapped[str] = mapped_column(String(32))  # mtn / syriatel
    recipient_number: Mapped[str] = mapped_column(String(32))
    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/approved/rejected
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    admin_chat_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])

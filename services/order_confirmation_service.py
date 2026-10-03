"""رسالة تأكيد الطلب الموحّدة لكل أقسام المتجر.

الهدف: أي طلب يُنشئه المستخدم (رشق، ألعاب، برامج، اشتراكات، أرقام،
جلسات جاهزة…) ينتهي برسالة واحدة بتنسيق عصري موحّد:

    ✅ <b>تم إنشاء طلبك بنجاح</b>
    ━━━━━━━━━━━━━━━━━━━━
    🎮 <b>القسم:</b> قسم شحن الألعاب
    📦 <b>الخدمة:</b> شحن 325 UC — ببجي
    🎯 <b>معرّف اللاعب:</b> <code>5123456789</code>
    ...
    💵 <b>السعر:</b> 5.00$
    ━━━━━━━━━━━━━━━━━━━━
    🔔 <b>سيتم إشعارك عند اكتمال الخدمة</b>

الرسالة تتكيّف مع نوع الخدمة (تسمية الهدف، الوقت المتوقع، الحالة)،
وتُختم دائماً بسطر «سيتم إشعارك عند اكتمال الخدمة» ما لم يكن الطلب
قد سلّم فوراً — حينها يُستبدل السطر بعبارة التسليم الفوري.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from services.html_guard import esc

SEPARATOR = "━━━━━━━━━━━━━━━━━━"

# أيقونة + اسم القسم حسب نوع الفئة في قاعدة البيانات.
SECTION_META: dict[str, tuple[str, str, str]] = {
    # type: (أيقونة القسم, الاسم عربي, الاسم إنجليزي)
    "numbers": ("📞", "قسم الأرقام", "Numbers"),
    "smm": ("📈", "قسم الرشق", "SMM"),
    "games": ("🎮", "قسم شحن الألعاب", "Game top-ups"),
    "apps": ("📱", "قسم شحن البرامج", "Apps"),
    "balances": ("💳", "قسم شحن الرصيد", "Balance top-ups"),
    "cards": ("🎴", "قسم البطاقات", "Cards"),
    "subscriptions": ("✨", "قسم الاشتراكات الرقمية", "Digital subscriptions"),
    "verification": ("🛡", "قسم التوثيق", "Verification"),
    "codes": ("🎟", "قسم الأكواد", "Codes"),
    "custom": ("🧩", "قسم مخصص", "Custom"),
}

# الوقت المتوقع حسب نوع القسم.
ETA_META: dict[str, tuple[str, str]] = {
    "smm": ("من بضع دقائق وحتى ساعة", "From a few minutes up to an hour"),
    "games": ("من دقائق وحتى ساعتين", "From minutes up to 2 hours"),
    "numbers": ("فوراً — الكود خلال ١٥ دقيقة غالباً", "Instant — code usually within 15 minutes"),
    "balances": ("من دقائق وحتى ساعة", "From a few minutes up to an hour"),
    "apps": ("من دقائق وحتى ساعتين", "From minutes up to 2 hours"),
    "subscriptions": ("من دقائق وحتى ساعتين", "From minutes up to 2 hours"),
    "cards": ("من دقائق وحتى ساعتين", "From minutes up to 2 hours"),
    "codes": ("من دقائق وحتى ساعتين", "From minutes up to 2 hours"),
    "verification": ("من دقائق وحتى ساعة", "From a few minutes up to an hour"),
}
ETA_DEFAULT = ("خلال ٢٤ ساعة كحد أقصى", "Within 24 hours at most")


def _lang(language: str | None) -> str:
    return "en" if str(language or "").lower().startswith("en") else "ar"


def _money(value: object) -> str:
    """تنسيق مبلغ نقدي: 5.10$ بدون أصفار زائدة غير لازمة."""
    try:
        amount = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return f"{value}$"
    return f"{amount.quantize(Decimal('0.01')):.2f}$"


def _section_label(category_type: object | None, language: str) -> str | None:
    if category_type is None:
        return None
    key = str(getattr(category_type, "value", category_type) or "").lower()
    meta = SECTION_META.get(key)
    if meta is None:
        return None
    return meta[2] if language == "en" else meta[1]


def _section_emoji(category_type: object | None) -> str:
    if category_type is None:
        return "🛍"
    key = str(getattr(category_type, "value", category_type) or "").lower()
    return SECTION_META.get(key, ("🛍", "", ""))[0]


def _eta(category_type: object | None, language: str) -> str:
    key = str(getattr(category_type, "value", category_type) or "").lower()
    pair = ETA_META.get(key, ETA_DEFAULT)
    return pair[1] if language == "en" else pair[0]


def _target_label(product: object | None, language: str) -> str:
    """تسمية حقل الهدف حسب نوع الخدمة: رابط، معرّف لاعب، …"""
    if language == "en":
        default = "Target"
    else:
        default = "الهدف"
    if product is None:
        return default
    placeholder = (getattr(product, "custom_input_placeholder", None) or "").strip()
    if placeholder:
        return placeholder.rstrip(":：").strip() or default
    name = (getattr(product, "name_ar", "") or getattr(product, "name", "") or "").lower()
    if getattr(product, "requires_link", False):
        return "Link" if language == "en" else "الرابط"
    if getattr(product, "requires_player_id", False):
        return "Player ID" if language == "en" else "معرّف اللاعب"
    if getattr(product, "requires_username", False):
        return "Username" if language == "en" else "اسم المستخدم"
    if any(token in name for token in ("رشق", "متابع", "لايك", "مشاهدة", "smm")):
        return "Link" if language == "en" else "الرابط"
    if any(token in name for token in ("ببجي", "فري فاير", "لعبة", "شحن", "uc", "جواهر")):
        return "Player ID" if language == "en" else "معرّف اللاعب"
    return default


# ══════════════════════════════════════════════════════════════════
# باني الرسالة
# ══════════════════════════════════════════════════════════════════


@dataclass
class OrderConfirmation:
    """بيانات رسالة تأكيد طلب — تُبنى ثم تُ Render كنص HTML."""

    title: str
    order_ref: str | None = None
    emoji: str = "✅"
    section: str | None = None
    section_emoji: str = "🛍"
    service: str | None = None
    service_emoji: str = "📦"
    rows: list[tuple[str, str, str]] = field(default_factory=list)  # (أيقونة, تسمية, قيمة)
    price_usd: Decimal | None = None
    discount_usd: Decimal | None = None
    discount_label: str = "الخصم"
    cashback_usd: Decimal | None = None
    paid_usd: Decimal | None = None
    balance_after: Decimal | None = None
    eta: str | None = None
    status: str | None = None
    delivery_html: str | None = None
    note: str | None = None
    footer: str | None = None
    pending_notice: bool = True
    language: str = "ar"

    # ── إضافة صفوف ───────────────────────────────────────────────
    def row(self, icon: str, label: str, value: object, *, mono: bool = False) -> "OrderConfirmation":
        """صف تفصيل واحد. ``mono=True`` يضع القيمة داخل ``<code>``."""
        text = "" if value is None else str(value)
        if not text.strip():
            return self
        rendered = f"<code>{esc(text)}</code>" if mono else esc(text)
        self.rows.append((icon, esc(str(label)), rendered))
        return self

    # ── الصادر النهائي ───────────────────────────────────────────
    def render(self) -> str:
        lang = _lang(self.language)
        en = lang == "en"

        lines: list[str] = [f"{self.emoji} <b>{esc(self.title)}</b>", SEPARATOR, ""]

        if self.section:
            lines.append(f"{self.section_emoji} <b>{'Section' if en else 'القسم'}:</b> {esc(self.section)}")
        if self.service:
            lines.append(f"{self.service_emoji} <b>{'Service' if en else 'الخدمة'}:</b> {esc(self.service)}")
        for icon, label, value in self.rows:
            lines.append(f"{icon} <b>{label}:</b> {value}")
        if self.order_ref:
            lines.append(f"🆔 <b>{'Order ID' if en else 'رقم الطلب'}:</b> <code>{esc(self.order_ref)}</code>")

        money_block: list[str] = []
        if self.price_usd is not None:
            money_block.append(f"💵 <b>{'Price' if en else 'السعر'}:</b> {_money(self.price_usd)}")
        if self.discount_usd:
            money_block.append(f"🎁 <b>{esc(self.discount_label)}:</b> -{_money(self.discount_usd)}")
        if self.cashback_usd:
            money_block.append(f"🎁 <b>{'Cashback' if en else 'كاشباك'}:</b> {_money(self.cashback_usd)}")
        if self.paid_usd is not None:
            money_block.append(f"💳 <b>{'Paid' if en else 'المخصوم من رصيدك'}:</b> {_money(self.paid_usd)}")
        if self.balance_after is not None:
            money_block.append(f"🏦 <b>{'Balance now' if en else 'رصيدك الآن'}:</b> {_money(self.balance_after)}")
        if money_block:
            lines.append("")
            lines.extend(money_block)

        tail: list[str] = []
        if self.eta:
            tail.append(f"⏱ <b>{'Expected time' if en else 'الوقت المتوقع'}:</b> {esc(self.eta)}")
        if self.status:
            tail.append(f"📌 <b>{'Status' if en else 'الحالة'}:</b> {esc(self.status)}")
        if tail:
            lines.append("")
            lines.extend(tail)

        if self.delivery_html:
            lines.append("")
            lines.append(f"🎁 <b>{'Delivered instantly — your data' if en else 'تم التسليم فوراً — بياناتك'}:</b>")
            lines.append(self.delivery_html)

        if self.note:
            lines.append("")
            lines.append(esc(self.note))

        lines.append("")
        lines.append(SEPARATOR)
        if self.footer:
            lines.append(self.footer)
        elif self.pending_notice:
            lines.append(
                f"🔔 <b>{'You will be notified when the service is completed' if en else 'سيتم إشعارك عند اكتمال الخدمة'}</b>"
            )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# بُناة جاهزة لكل نوع طلب
# ══════════════════════════════════════════════════════════════════


class OrderConfirmationService:
    """بناء رسالة التأكيد المناسبة لكل مسار شراء."""

    # ── الطلبات الموحّدة (رشق/ألعاب/برامج/اشتراكات/…) ─────────────
    @staticmethod
    async def _category_of(product):
        """قسم المنتج بأمان: من العلاقة إن كانت محمّلة، وإلا باستعلام أعمدة.

        الوصول المباشر إلى ``product.sub_category`` على جلسة غير متزامنة
        يرفع ``MissingGreenlet`` إن لم تكن العلاقة محمّلة مسبقاً — لذلك
        القراءة محاطة بـ try/except مع استعلام احتياطي على الأعمدة فقط.
        """
        if product is None:
            return None
        try:
            sub = getattr(product, "sub_category", None)
        except Exception:  # noqa: BLE001
            sub = None
        if sub is not None:
            try:
                category = getattr(sub, "category", None)
            except Exception:  # noqa: BLE001
                category = None
            if category is not None:
                return category
        sub_id = getattr(product, "sub_category_id", None)
        if sub_id is None:
            return None
        try:
            from sqlalchemy import select

            from database.engine import async_session_maker
            from database.models import Category, SubCategory

            async with async_session_maker() as session:
                sub_row = (
                    await session.execute(
                        select(SubCategory).where(SubCategory.id == sub_id)
                    )
                ).scalar_one_or_none()
                if sub_row is None:
                    return None
                return (
                    await session.execute(
                        select(Category).where(Category.id == sub_row.category_id)
                    )
                ).scalar_one_or_none()
        except Exception:  # noqa: BLE001 — نص تجميلي: لا يُسقط الطلب
            return None

    @staticmethod
    async def unified(
        *,
        order,
        product,
        user=None,
        target: str | None = None,
        quantity: int | None = None,
        price_usd: Decimal | None = None,
        discount_usd: Decimal | None = None,
        discount_label: str = "الخصم",
        cashback_usd: Decimal | None = None,
        status: str | None = None,
        delivery_html: str | None = None,
        note: str | None = None,
        balance_after: Decimal | None = None,
        language: str = "ar",
        title: str | None = None,
        emoji: str | None = None,
        footer: str | None = None,
    ) -> str:
        lang = _lang(language)
        en = lang == "en"
        category = await OrderConfirmationService._category_of(product)
        category_type = getattr(category, "type", None)

        section = _section_label(category_type, lang)
        if section is None and category is not None:
            category_name = getattr(category, "name_ar", None)
            if category_name:
                section = str(category_name)
        section_icon = _section_emoji(category_type)

        paid = price_usd
        if paid is not None and discount_usd:
            try:
                paid = Decimal(str(paid)) - Decimal(str(discount_usd))
            except Exception:  # noqa: BLE001
                paid = price_usd

        conf = OrderConfirmation(
            title=title or ("Order created successfully" if en else "تم إنشاء طلبك بنجاح"),
            order_ref=f"#{order.id}" if order is not None and getattr(order, "id", None) else None,
            emoji=emoji or _section_emoji(category_type),
            section=section,
            section_emoji="🗂",
            service=str(getattr(product, "name_ar", None) or getattr(product, "name", "") or "—")
            if product is not None
            else None,
            service_emoji=section_icon,
            price_usd=price_usd,
            discount_usd=discount_usd,
            discount_label=discount_label,
            cashback_usd=cashback_usd,
            paid_usd=paid,
            balance_after=balance_after,
            eta=_eta(category_type, lang) if not delivery_html else None,
            status=status,
            delivery_html=delivery_html,
            note=note,
            language=lang,
        )
        if footer:
            conf.footer = esc(footer)
        elif delivery_html:
            # سُلّم فوراً: لا معنى لانتظار إشعار الاكتمال.
            conf.footer = f"\U0001f389 <b>{'Delivered instantly — enjoy!' if en else 'تم تسليم طلبك فوراً — شكراً لثقتك بنا'}</b>"

        if target:
            conf.row("🎯", _target_label(product, lang), target, mono=True)
        if quantity and int(quantity) > 1:
            conf.row("🔢", "الكمية" if not en else "Quantity", quantity)
        return conf.render()

    # ── أرقام الاستلام (SMS/واتساب/تيليجرام) ─────────────────────
    @staticmethod
    def number(
        *,
        order,
        phone_number: str,
        service_name: str,
        country_name: str,
        flag: str = "🌍",
        price_usd: Decimal | None = None,
        balance_after: Decimal | None = None,
        timeout_minutes: int | None = None,
        language: str = "ar",
        emoji: str = "📞",
    ) -> str:
        lang = _lang(language)
        en = lang == "en"
        conf = OrderConfirmation(
            title="تم شراء الرقم بنجاح" if not en else "Number purchased successfully",
            order_ref=f"#{order.id}" if order is not None and getattr(order, "id", None) else None,
            emoji=emoji,
            section="قسم الأرقام" if not en else "Numbers",
            section_emoji="🗂",
            service=f"{service_name}",
            service_emoji="📨",
            price_usd=price_usd,
            paid_usd=price_usd,
            balance_after=balance_after,
            eta=_eta("numbers", lang),
            note=("سيصلك الكود في هذه الرسالة فور وصوله من المزود.")
            if not en
            else ("The code will appear in this message as soon as it arrives."),
            language=lang,
        )
        conf.row("📱", "الرقم" if not en else "Phone", phone_number, mono=True)
        conf.row(flag or "🌍", "الدولة" if not en else "Country", country_name)
        if timeout_minutes:
            conf.row("⏳", "مهلة الانتظار" if not en else "Waiting window", f"{timeout_minutes} " + ("دقيقة" if not en else "minutes"))
        return conf.render()

    # ── جلسات تيليجرام الجاهزة ───────────────────────────────────
    @staticmethod
    def tg_ready(
        *,
        item,
        country_name: str,
        flag: str = "🌍",
        phone_number: str,
        price_usd: Decimal | None = None,
        balance_after: Decimal | None = None,
        stock_left: int | None = None,
        language: str = "ar",
    ) -> str:
        lang = _lang(language)
        en = lang == "en"
        conf = OrderConfirmation(
            title="تم شراء الحساب الجاهز بنجاح" if not en else "Ready account purchased",
            order_ref=f"#{item.id}" if item is not None and getattr(item, "id", None) else None,
            emoji="⚡️",
            section="أرقام تيليجرام الجاهزة" if not en else "Ready Telegram numbers",
            section_emoji="🗂",
            service=country_name,
            service_emoji=flag or "🌍",
            price_usd=price_usd,
            paid_usd=price_usd,
            balance_after=balance_after,
            note=(
                "افتح تيليجرام وأدخل الرقم واطلب الكود، ثم اضغط «📩 طلب الكود» "
                "وسيصلك الكود والرمز السري."
            )
            if not en
            else (
                "Open Telegram, enter the number and request the code, then tap "
                "“📩 Request code” to receive it."
            ),
            language=lang,
        )
        conf.row("📱", "الرقم" if not en else "Phone", phone_number, mono=True)
        if stock_left is not None:
            conf.row("📦", "المتبقي" if not en else "Left in stock", stock_left)
        return conf.render()

    # ── نتيجة السلة (عدة طلبات في مرة واحدة) ─────────────────────
    @staticmethod
    def cart(
        *,
        completed: int,
        failed: int = 0,
        saved_usd: Decimal | None = None,
        charged_usd: Decimal | None = None,
        deliveries: list[str] | None = None,
        language: str = "ar",
    ) -> str:
        lang = _lang(language)
        en = lang == "en"
        conf = OrderConfirmation(
            title="تم تنفيذ طلبات السلة" if not en else "Cart processed",
            emoji="🧾",
            service=("السلة" if not en else "Cart"),
            service_emoji="🛒",
            price_usd=None,
            paid_usd=charged_usd,
            language=lang,
        )
        conf.row("🧮", "عدد الطلبات" if not en else "Orders", completed)
        if failed:
            conf.row("⚠️", "تعذّر تنفيذها" if not en else "Failed", failed)
        if saved_usd:
            conf.row("🎟", "وفّرت" if not en else "You saved", _money(saved_usd))
        if deliveries:
            conf.delivery_html = "\n".join(f"<code>{esc(v)}</code>" for v in deliveries if v)
        conf.note = (
            "ستصلك إشعار لكل طلب على حدة فور اكتماله."
            if not en
            else "You will get a separate notification for each order once completed."
        )
        return conf.render()

    # ── دفعة أرقام (شراء بالجملة) ────────────────────────────────
    @staticmethod
    def bulk_numbers(
        *,
        requested: int,
        succeeded: int,
        failed: int,
        net_charged_usd: Decimal | None = None,
        refunded_usd: Decimal | None = None,
        service_name: str | None = None,
        country_name: str | None = None,
        flag: str = "🌍",
        language: str = "ar",
    ) -> str:
        lang = _lang(language)
        en = lang == "en"
        conf = OrderConfirmation(
            title="تم تنفيذ دفعة الأرقام" if not en else "Bulk numbers processed",
            emoji="📦",
            section="قسم الأرقام" if not en else "Numbers",
            section_emoji="🗂",
            service=service_name,
            service_emoji="📨",
            price_usd=None,
            paid_usd=net_charged_usd,
            language=lang,
        )
        conf.row("🔢", "المطلوب" if not en else "Requested", requested)
        conf.row("✅", "تم شراؤه" if not en else "Purchased", succeeded)
        if failed:
            conf.row("❌", "فشل" if not en else "Failed", failed)
        if country_name:
            conf.row(flag or "🌍", "الدولة" if not en else "Country", country_name)
        if refunded_usd:
            conf.row("↩️", "المسترجع لرصيدك" if not en else "Refunded", _money(refunded_usd))
        conf.note = (
            "سيتم إرسال الأكواد فور وصولها من المزود."
            if not en
            else "Codes will be sent as soon as they arrive."
        )
        return conf.render()

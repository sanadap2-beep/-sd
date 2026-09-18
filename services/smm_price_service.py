"""Live SMM pricing and service detail display."""
import json
import logging
from decimal import Decimal
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.models import ProviderService
from services.margin_service import MarginService
logger = logging.getLogger(__name__)

# الوقت الموحد لاكتمال طلبات خدمات الرشق (1 - 25 دقيقة).
SMM_DEFAULT_ETA = "1 - 25 دقيقة"

def _raw(raw_data):
    if not raw_data:
        return {}
    try:
        return json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except Exception:
        return {}

def _get(raw, *keys, default="-"):
    for k in keys:
        v = raw.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default

async def live_provider_price(product, session):
    if not product.provider_service_ref_id:
        return None
    svc = await session.get(ProviderService, product.provider_service_ref_id)
    if svc is None or svc.rate_usd is None or svc.rate_usd <= 0:
        return None
    return Decimal(str(svc.rate_usd))

async def live_sell_price(product, session):
    cost = await live_provider_price(product, session)
    if cost is None:
        return Decimal(str(product.price_usd or "0"))
    margin, _ = await MarginService.effective_margin(session, product)
    return MarginService.price_from_cost(cost, margin)

async def service_details(product, session):
    svc = await session.get(ProviderService, product.provider_service_ref_id) if product.provider_service_ref_id else None
    if svc is None:
        return {"estimated_time": SMM_DEFAULT_ETA}
    raw = _raw(svc.raw_data)
    # التعبئة قد تُخزَّن كمدة (شهر/يوم) في raw_data أو كقيمة دعم refill.
    refill_period = _get(raw, "refill_period", "refill_time", "period", "package", default="")
    # وقت اكتمال طلبات الرشق موحد: بين 1 و 25 دقيقة.
    estimated = _get(raw, "time", "estimated_time", "EstimatedTime", default="")
    if not estimated or estimated == "-":
        estimated = SMM_DEFAULT_ETA
    else:
        estimated = SMM_DEFAULT_ETA
    return {
        "type": svc.service_type or _get(raw, "type", "service_type"),
        "name": svc.name, "rate": svc.rate_usd,
        "min": svc.min_quantity, "max": svc.max_quantity,
        "refill": svc.supports_refill, "cancel": svc.supports_cancel,
        "refill_period": refill_period,
        "description": svc.description,
        "speed": _get(raw, "speed", "Speed", "rate_per_hour"),
        "quality": _get(raw, "quality", "Quality", "qty"),
        "drop_rate": _get(raw, "drop", "drop_rate", "DropRate"),
        "estimated_time": estimated,
    }

def format_details(details, sell_price):
    refill = details.get("refill_period") or ("✅" if details.get("refill") else "❌")
    eta = details.get("estimated_time") or SMM_DEFAULT_ETA
    if eta == "-":
        eta = SMM_DEFAULT_ETA
    return (
        f"📦 <b>{details.get('name', 'خدمة')}</b>\n\n"
        f"• النوع: <b>{details.get('type', '-')}</b>\n"
        f"• السعر: <b>${sell_price}</b>\n"
        f"• السرعة: <b>{details.get('speed', '-')}</b>\n"
        f"• التعبئة: <b>{refill}</b>\n"
        f"• الجودة: <b>{details.get('quality', '-')}</b>\n"
        f"• النزول: <b>{details.get('drop_rate', '-')}</b>\n"
        f"• الحد الأدنى: <b>{details.get('min', '-')}</b>\n"
        f"• الحد الأقصى: <b>{details.get('max', '-')}</b>\n"
        f"• الوقت: <b>{eta}</b>\n"
        + (f"\n📝 <i>{details.get('description')}</i>" if details.get("description") else "")
    )


def format_details_kb(details, sell_price, back_callback: str) -> InlineKeyboardMarkup:
    """تفاصيل الخدمة كأزرار Inline مرتبة صفّين صفّين (قيمة + عنوان).

    التصميم المطلوب: كل صف فيه زرّان — القيمة والعنوان مع الإيموجي —
    حتى تظهر التفاصيل كـ «بوكسات» منسّقة بدل نص عادي. الأزرار شكلية
    (callback_data="none") وليس لها وظيفة فعلية، إلا «رجوع» في الأسفل.
    القيم كلها ديناميكية من المزود وقاعدة البيانات.
    """
    refill = details.get("refill_period") or ("✅" if details.get("refill") else "❌")
    price_label = f"${_fmt_price(sell_price)}"
    eta_value = details.get("estimated_time") or SMM_DEFAULT_ETA
    if eta_value == "-":
        eta_value = SMM_DEFAULT_ETA

    rows = [
        (details.get("type", "-"), "⭐ النوع :"),
        (price_label, "💰 السعر 1k :"),
        (details.get("speed", "-"), "🚀 السرعة :"),
        (refill, "📦 التعبئة :"),
        (details.get("quality", "-"), "✅ الجودة :"),
        (details.get("drop_rate", "-"), "📉 النزول :"),
        (str(details.get("min", "-")), "🔴 الحد الأدنى :"),
        (str(details.get("max", "-")), "🟢 الحد الأقصى :"),
        (eta_value, "⏰ الوقت :"),
    ]

    b = InlineKeyboardBuilder()
    for value, label in rows:
        b.button(text=str(value)[:40], callback_data="none")
        b.button(text=str(label)[:40], callback_data="none")
    b.button(text="❌ رجوع", callback_data=back_callback[:64])
    b.adjust(2, 2, 2, 2, 2, 2, 2, 2, 2, 1)
    return b.as_markup()


def _fmt_price(amount) -> str:
    """تنسيق السعر للمستخدم دون فواصل زائدة."""
    try:
        a = Decimal(str(amount))
    except Exception:
        return str(amount)
    if a == a.to_integral_value():
        return f"{a:,.0f}"
    return f"{a:,.2f}".rstrip("0").rstrip(".")

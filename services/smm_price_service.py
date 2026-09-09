"""Live SMM pricing and service detail display."""
import json, logging
from decimal import Decimal
from database.models import ProviderService
from services.margin_service import MarginService
logger = logging.getLogger(__name__)

def _raw(raw_data):
    if not raw_data: return {}
    try: return json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except: return {}

def _get(raw, *keys, default="-"):
    for k in keys:
        v = raw.get(k)
        if v is not None and str(v).strip(): return str(v).strip()
    return default

async def live_provider_price(product, session):
    if not product.provider_service_ref_id: return None
    svc = await session.get(ProviderService, product.provider_service_ref_id)
    if svc is None or svc.rate_usd is None or svc.rate_usd <= 0: return None
    return Decimal(str(svc.rate_usd))

async def live_sell_price(product, session):
    cost = await live_provider_price(product, session)
    if cost is None: return Decimal(str(product.price_usd or "0"))
    margin, _ = await MarginService.effective_margin(session, product)
    return MarginService.price_from_cost(cost, margin)

async def service_details(product, session):
    svc = await session.get(ProviderService, product.provider_service_ref_id) if product.provider_service_ref_id else None
    if svc is None: return {}
    raw = _raw(svc.raw_data)
    return {
        "type": svc.service_type or _get(raw, "type","service_type"),
        "name": svc.name, "rate": svc.rate_usd,
        "min": svc.min_quantity, "max": svc.max_quantity,
        "refill": svc.supports_refill, "cancel": svc.supports_cancel,
        "description": svc.description,
        "speed": _get(raw, "speed","Speed","rate_per_hour"),
        "quality": _get(raw, "quality","Quality","qty"),
        "drop_rate": _get(raw, "drop","drop_rate","DropRate"),
        "estimated_time": _get(raw, "time","estimated_time","EstimatedTime"),
    }

def format_details(details, sell_price):
    return (
        f"📦 <b>{details.get('name','خدمة')}</b>\n\n"
        f"• النوع: <b>{details.get('type','-')}</b>\n"
        f"• السعر: <b>${sell_price}</b>\n"
        f"• السرعة: <b>{details.get('speed','-')}</b>\n"
        f"• التعبئة: {'✅' if details.get('refill') else '❌'}\n"
        f"• الجودة: <b>{details.get('quality','-')}</b>\n"
        f"• النزول: <b>{details.get('drop_rate','-')}</b>\n"
        f"• الحد الأدنى: <b>{details.get('min','-')}</b>\n"
        f"• الحد الأقصى: <b>{details.get('max','-')}</b>\n"
        f"• الوقت: <b>{details.get('estimated_time','-')}</b>\n"
        + (f"\n📝 <i>{details['description']}</i>" if details.get('description') else "")
    )
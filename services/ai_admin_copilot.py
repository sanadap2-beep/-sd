"""Private admin copilot: deterministic insights without leaking data to an LLM."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import desc, func, select

from database.models import (
    Product,
    ProductStatus,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
)


class AIAdminCopilot:
    @staticmethod
    async def brief(session, days: int = 7) -> dict:
        since = datetime.utcnow() - timedelta(days=days)
        revenue = (
            await session.execute(
                select(func.coalesce(func.sum(-Transaction.amount), 0)).where(
                    Transaction.type == TransactionType.PURCHASE,
                    Transaction.amount < 0,
                    Transaction.created_at >= since,
                )
            )
        ).scalar_one()
        orders = (
            await session.execute(
                select(func.count(UnifiedOrder.id)).where(UnifiedOrder.created_at >= since)
            )
        ).scalar_one()
        failures = (
            await session.execute(
                select(func.count(UnifiedOrder.id)).where(
                    UnifiedOrder.status.in_(
                        [UnifiedOrderStatus.FAILED, UnifiedOrderStatus.REFUNDED]
                    ),
                    UnifiedOrder.created_at >= since,
                )
            )
        ).scalar_one()
        top = await session.execute(
            select(Product.name_ar, Product.total_sold, Product.price_usd)
            .where(Product.status == ProductStatus.ACTIVE)
            .order_by(desc(Product.total_sold))
            .limit(5)
        )
        top_products = [
            {"name": name, "sold": sold or 0, "price_usd": price} for name, sold, price in top.all()
        ]
        failure_rate = round(failures / orders * 100, 2) if orders else 0
        suggestions = []
        if orders == 0:
            suggestions.append("فعّل عرضاً تجريبياً لجذب أول طلبات الفترة.")
        if failure_rate > 10:
            suggestions.append("راجع المزودين؛ نسبة فشل الطلبات أعلى من 10%.")
        if top_products and top_products[0]["sold"] > 0:
            suggestions.append(f"ضع المنتج الأكثر مبيعاً ({top_products[0]['name']}) في الواجهة.")
        return {
            "period_days": days,
            "revenue_usd": Decimal(str(revenue or 0)),
            "orders": orders,
            "failures": failures,
            "failure_rate": failure_rate,
            "top_products": top_products,
            "suggestions": suggestions,
            "generated_at": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def draft_reply(topic: str) -> str:
        text = topic.casefold()
        if "تأخير" in text or "delay" in text:
            return "نعتذر عن التأخير. نتابع الطلب مع المزود وسنرسل تحديثاً فورياً، وإذا فشل التنفيذ سيُسترجع الرصيد تلقائياً."
        if "شحن" in text or "deposit" in text:
            return "يمكنك اختيار طريقة الشحن من قسم شحن الرصيد. يرجى التأكد من الشبكة ورقم العملية قبل الإرسال."
        if "استرجاع" in text or "refund" in text:
            return "تم تسجيل طلبك للمراجعة. نتحقق من العملية ونضيف الاسترجاع إلى الرصيد بعد التأكد."
        return "شكراً لتواصلك. أرسل رقم الطلب والتفاصيل، وسيقوم فريق الدعم بمتابعتها."

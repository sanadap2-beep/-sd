"""مساعد كتالوج خفيف وسريع بدون إرسال بيانات المستخدم إلى طرف ثالث."""

from __future__ import annotations

import re

from services.product_service import ProductService


class AssistantService:
    @staticmethod
    def _terms(text: str) -> list[str]:
        return [
            term for term in re.findall(r"[\w\u0600-\u06ff]+", text.casefold()) if len(term) >= 2
        ]

    @staticmethod
    async def recommend(session, request: str):
        request = request.strip()
        products = await ProductService.search_products(
            session, request, limit=20, active_only=True
        )
        if not products:
            seen = set()
            for term in AssistantService._terms(request):
                for product in await ProductService.search_products(
                    session, term, limit=8, active_only=True
                ):
                    if product.id not in seen:
                        seen.add(product.id)
                        products.append(product)
        if "ارخص" in request or "الأرخص" in request or "cheap" in request.casefold():
            products.sort(key=lambda product: product.price_usd)
            reason = "رتبت النتائج من الأرخص إلى الأغلى."
        elif any(word in request.casefold() for word in ("best", "الأفضل", "مميز", "popular")):
            products.sort(key=lambda product: product.total_sold or 0, reverse=True)
            reason = "رتبت النتائج حسب الأكثر طلباً."
        else:
            reason = "اخترت النتائج الأقرب لوصفك من الكتالوج الحالي."
        if products:
            return products[:10], reason
        return await ProductService.get_bestsellers(
            session, limit=5, active_only=True
        ), "لم أجد تطابقاً حرفياً، فهذه أكثر المنتجات طلباً حالياً."

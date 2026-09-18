"""Inline query product search.

يسمح للمستخدم بكتابة @اسم_البوت كلمة البحث داخل أي محادثة ومشاركة كارت
منتج مختصر مع زر شراء يفتح البوت مباشرة على المنتج.
"""

from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from database.engine import async_session_maker
from database.models import Category, Product, ProductStatus, SubCategory
from services.bot_identity import resolve_bot_username

router = Router(name="inline_search")


def _short(value: str | None, limit: int = 90) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@router.inline_query()
async def inline_product_search(inline_query: InlineQuery):
    query = (inline_query.query or "").strip()
    limit = 10

    async with async_session_maker() as session:
        stmt = (
            select(Product)
            .join(Product.sub_category)
            .join(SubCategory.category)
            .options(selectinload(Product.sub_category).selectinload(SubCategory.category))
            .where(
                Product.status == ProductStatus.ACTIVE,
                SubCategory.is_active.is_(True),
                Category.is_active.is_(True),
            )
        )
        if query:
            like = f"%{query}%"
            stmt = stmt.where(
                or_(
                    Product.name_ar.ilike(like),
                    Product.description.ilike(like),
                    SubCategory.name_ar.ilike(like),
                    Category.name_ar.ilike(like),
                )
            )
        stmt = stmt.order_by(Product.is_featured.desc(), Product.total_sold.desc(), Product.sort_order, Product.id).limit(limit)
        products = list((await session.execute(stmt)).scalars().unique().all())

    username = await resolve_bot_username(getattr(inline_query, "bot", None))
    results = []
    for product in products:
        sub = product.sub_category
        category = sub.category if sub else None
        subtitle = f"{category.emoji if category else '📦'} {sub.name_ar if sub else 'عام'} · {product.price_usd}$"
        text = (
            f"🛍 <b>{escape(product.name_ar)}</b>\n\n"
            f"{escape(_short(product.description, 220) or 'خدمة رقمية جاهزة للطلب')}\n\n"
            f"💰 السعر: <b>{product.price_usd}$</b>\n"
            "اضغط زر الشراء لفتح البوت وإتمام الطلب بأمان."
        )
        if username:
            button = InlineKeyboardButton(
                text="🛒 شراء عبر البوت",
                url=f"https://t.me/{username}?start=prod_{product.id}",
            )
        else:
            # Fallback for tests/local setups without a resolvable username.
            button = InlineKeyboardButton(
                text="🛒 شراء المنتج",
                callback_data=f"prod:{product.id}", style="success",
            )
        results.append(
            InlineQueryResultArticle(
                id=f"product-{product.id}",
                title=_short(product.name_ar, 64),
                description=_short(subtitle, 120),
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode="HTML",
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]),
            )
        )

    if not results and query:
        results.append(
            InlineQueryResultArticle(
                id="no-results",
                title="لا توجد نتائج",
                description="جرّب كلمة بحث أخرى أو افتح المتجر من البوت.",
                input_message_content=InputTextMessageContent(
                    message_text="🔎 لا توجد منتجات مطابقة حالياً. افتح البوت وتصفح المتجر.",
                ),
            )
        )

    await inline_query.answer(results, cache_time=15, is_personal=True)

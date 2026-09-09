"""HTTP API and Telegram Mini App application."""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
import asyncio
import hmac
import os
import re
import time
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from api.admin import router as admin_router
from api.deps import get_current_user, get_session
from api.reseller import router as reseller_router
from api.schemas import (
    CatalogOut,
    CartItemIn,
    CartItemOut,
    CategoryOut,
    CheckoutIn,
    CheckoutOut,
    GiftRedeemIn,
    MeOut,
    OrderOut,
    ProductOut,
    SubCategoryOut,
    WatchIn,
)
from config import settings
from database.models import (
    Category,
    NumberOrder,
    Product,
    SubCategory,
    ProductReview,
    ProductStatus,
    ProductWatch,
    UnifiedOrder,
    User,
)
from database.seed import init_db
from services.abuse_guard_service import AbuseGuardService
from services.cart_service import CartError, CartService
from services.checkout_service import CheckoutError, CheckoutService
from services.gift_service import GiftCodeError, GiftService
from services.loyalty_service import LoyaltyService
from services.observability import init_observability
from services.operation_lock_service import OperationBusyError, OperationLockService
from services.promotion_service import PromotionService
from services.receipt_service import ReceiptService
from services.upsell_service import UpsellService
from services.watch_service import WatchService

init_observability()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Number Bot Marketplace API",
    version="2.0.0",
    lifespan=lifespan,
)
origins = [settings.WEBAPP_URL] if settings.WEBAPP_URL else []

class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    """Lightweight per-IP/API-key limiter for the public REST API.

    It intentionally stays in-memory so single-node installs work without Redis.
    For multi-worker production, put Nginx/Cloudflare in front or replace with Redis.
    """

    _hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/health", "/app", "/admin-assets", "/setup")):
            return await call_next(request)
        if settings.API_RATE_LIMIT_PER_MINUTE <= 0:
            return await call_next(request)

        identity = request.headers.get("x-reseller-key") or request.headers.get("authorization")
        if not identity:
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            identity = forwarded or (request.client.host if request.client else "unknown")
        key = f"{request.url.path}:{identity}"
        now = time.monotonic()
        window = 60.0
        max_hits = max(settings.API_RATE_LIMIT_PER_MINUTE, settings.API_RATE_LIMIT_BURST)
        hits = self._hits[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= max_hits:
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        hits.append(now)
        return await call_next(request)


app.add_middleware(InMemoryRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Reseller-Key"],
)
app.include_router(admin_router)
app.include_router(reseller_router)

_setup_used = False
_setup_lock = asyncio.Lock()


class SetupPayload(BaseModel):
    bot_token: str


def _setup_is_valid(key: str) -> bool:
    return bool(
        settings.SETUP_KEY
        and len(settings.SETUP_KEY) >= 32
        and not settings.BOT_TOKEN
        and not _setup_used
        and hmac.compare_digest(key, settings.SETUP_KEY)
    )


def _mark_setup_used():
    global _setup_used
    _setup_used = True


def _save_local_token(token: str) -> None:
    """Write only to the ignored local.env file, atomically and privately."""
    path = Path("local.env")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = False
    for index, line in enumerate(lines):
        if line.startswith("BOT_TOKEN="):
            lines[index] = f"BOT_TOKEN={token}"
            updated = True
            break
    if not updated:
        lines.insert(0, f"BOT_TOKEN={token}")
    temporary = path.with_name(".local.env.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


@app.get("/setup/{setup_key}", response_class=HTMLResponse, include_in_schema=False)
def setup_page(setup_key: str):
    if not _setup_is_valid(setup_key):
        raise HTTPException(status_code=404, detail="setup link expired")
    return HTMLResponse(
        """<!doctype html><meta name='viewport' content='width=device-width'>
        <title>Bot setup</title><style>body{font-family:Arial;max-width:520px;margin:40px auto;padding:20px;background:#07111f;color:#eef7ff}input,button{width:100%;padding:14px;margin:8px 0;border-radius:8px;border:1px solid #345;background:#10233b;color:#fff}button{background:#6ee7d2;color:#07111f;font-weight:bold}</style>
        <h2>إعداد البوت</h2><p>أدخل التوكن هنا فقط. لن يتم عرضه أو تسجيله.</p>
        <input id='token' type='password' autocomplete='off' placeholder='BOT_TOKEN'>
        <button onclick='save()'>حفظ التوكن</button><p id='result'></p>
        <script>async function save(){const token=document.getElementById('token').value;const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_token:token})});document.getElementById('result').textContent=r.ok?'تم الحفظ. أغلق الصفحة وأعد تشغيل البوت.':'تعذر الحفظ؛ تحقق من التوكن.';}</script>"""
    )


@app.post("/setup/{setup_key}", include_in_schema=False)
async def save_setup(setup_key: str, payload: SetupPayload):
    global _setup_used
    async with _setup_lock:
        if not _setup_is_valid(setup_key):
            raise HTTPException(status_code=404, detail="setup link expired")
        token = payload.bot_token.strip()
        if not re.fullmatch(r"\\d{5,15}:[A-Za-z0-9_-]{20,}", token):
            raise HTTPException(status_code=400, detail="invalid Telegram bot token")
        _save_local_token(token)
        _mark_setup_used()
        return {"status": "saved"}


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(session=Depends(get_session)):
    try:
        await session.execute(select(1))
        return {"status": "ok", "database": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/app/")


async def _review_map(session, product_ids: list[int]) -> dict[int, tuple[Decimal, int]]:
    if not product_ids:
        return {}
    result = await session.execute(
        select(
            ProductReview.product_id,
            func.avg(ProductReview.rating),
            func.count(ProductReview.id),
        )
        .where(ProductReview.product_id.in_(product_ids))
        .group_by(ProductReview.product_id)
    )
    return {
        product_id: (Decimal(str(avg)).quantize(Decimal("0.1")), count)
        for product_id, avg, count in result.all()
    }


async def _promotion_map(session, products: list[Product]) -> dict[int, dict]:
    promotions = await PromotionService.get_active_promotions(session, limit=200)
    result = {}
    for promotion in promotions:
        product = promotion.product
        if not product:
            continue
        discount = PromotionService.calculate_discount(promotion, product.price_usd)
        current = result.get(product.id)
        if current is None or discount > current["discount"]:
            result[product.id] = {
                "id": promotion.id,
                "name": promotion.name,
                "discount": discount,
                "discounted_price": product.price_usd - discount,
                "ends_at": promotion.ends_at.isoformat(),
            }
    return result


@app.get("/api/v1/catalog", response_model=CatalogOut)
async def catalog(session=Depends(get_session)):
    result = await session.execute(
        select(Category)
        .options(
            selectinload(Category.sub_categories).selectinload(SubCategory.products),
            # أقسام الرشق الداخلية: تطبيق ← أقسام داخلية ← منتجات.
            selectinload(Category.sub_categories)
            .selectinload(SubCategory.children)
            .selectinload(SubCategory.products),
        )
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.id)
    )
    categories = list(result.scalars().unique().all())
    products = [
        product
        for category in categories
        for subcategory in category.sub_categories
        for product in subcategory.products
        if product.status == ProductStatus.ACTIVE
    ]
    ratings = await _review_map(session, [product.id for product in products])
    promotions = await _promotion_map(session, products)

    def product_out(product: Product) -> ProductOut:
        average, count = ratings.get(product.id, (None, 0))
        promotion = promotions.get(product.id)
        promotion_payload = None
        if promotion:
            promotion_payload = {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in promotion.items()
            }
        return ProductOut(
            id=product.id,
            name=product.name_ar,
            description=product.description,
            price_usd=product.price_usd,
            display_type=product.display_type.value,
            min_quantity=product.min_quantity,
            max_quantity=product.max_quantity,
            requires_link=product.requires_link,
            requires_quantity=product.requires_quantity,
            requires_player_id=product.requires_player_id,
            fulfillment_type=product.fulfillment_type.value,
            rating=average,
            reviews_count=count,
            promotion=promotion_payload,
        )

    output = []
    for category in categories:
        subcategories = []
        for subcategory in category.sub_categories:
            if not subcategory.is_active:
                continue
            children = [child for child in (subcategory.children or []) if child.is_active]
            if children:
                # تطبيق يحوي أقساماً داخلية (قسم الرشق): نعرض الأقسام الداخلية
                # كصفوف مسطّحة بسياق التطبيق حتى تصل منتجاتها للمتجر الصغير.
                for child in children:
                    active_products = [
                        product_out(product)
                        for product in child.products
                        if product.status == ProductStatus.ACTIVE
                    ]
                    if not active_products:
                        continue
                    subcategories.append(
                        SubCategoryOut(
                            id=child.id,
                            name=f"{subcategory.name_ar} {child.name_ar}".strip(),
                            emoji=child.emoji,
                            products=active_products,
                        )
                    )
                continue
            active_products = [
                product_out(product)
                for product in subcategory.products
                if product.status == ProductStatus.ACTIVE
            ]
            if active_products:
                subcategories.append(
                    SubCategoryOut(
                        id=subcategory.id,
                        name=subcategory.name_ar,
                        emoji=subcategory.emoji,
                        products=active_products,
                    )
                )
        if subcategories:
            output.append(
                CategoryOut(
                    id=category.id,
                    name=category.name_ar,
                    emoji=category.emoji,
                    type=category.type.value,
                    sub_categories=subcategories,
                )
            )
    return CatalogOut(
        categories=output,
        generated_at=datetime.utcnow().isoformat(),
    )


@app.get("/api/v1/me", response_model=MeOut)
async def me(
    current_user: User = Depends(get_current_user),
):
    tier = LoyaltyService.tier_for_points(current_user.loyalty_points or 0)
    return MeOut(
        telegram_id=current_user.telegram_id,
        username=current_user.username,
        full_name=current_user.full_name,
        balance_usd=current_user.balance,
        loyalty_points=current_user.loyalty_points or 0,
        loyalty_streak=current_user.loyalty_streak or 0,
        loyalty_tier=f"{tier.emoji} {tier.name}",
    )


@app.get("/api/v1/orders", response_model=list[OrderOut])
async def orders(
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    result = await session.execute(
        select(UnifiedOrder)
        .options(selectinload(UnifiedOrder.product))
        .where(UnifiedOrder.user_id == current_user.id)
        .order_by(UnifiedOrder.created_at.desc())
        .limit(100)
    )
    unified = list(result.scalars().all())
    number_result = await session.execute(
        select(NumberOrder)
        .where(NumberOrder.user_id == current_user.id)
        .order_by(NumberOrder.purchased_at.desc())
        .limit(100)
    )
    numbers = list(number_result.scalars().all())
    output = [
        OrderOut(
            order_type="unified",
            id=order.id,
            product_id=order.product_id,
            product_name=order.product.name_ar if order.product else None,
            status=order.status.value,
            price_usd=order.price_usd,
            quantity=order.quantity,
            target=order.target,
            created_at=order.created_at.isoformat(),
            completed_at=order.completed_at.isoformat() if order.completed_at else None,
            status_message=order.status_message,
        )
        for order in unified
    ]
    output.extend(
        OrderOut(
            order_type="numbers",
            id=order.id,
            product_id=None,
            product_name=f"رقم {order.service}",
            status=order.status.value,
            price_usd=order.price_sell_usd,
            quantity=1,
            target=order.phone_number,
            created_at=order.purchased_at.isoformat(),
            completed_at=order.completed_at.isoformat() if order.completed_at else None,
            status_message=order.sms_code,
        )
        for order in numbers
    )
    return sorted(output, key=lambda item: item.created_at, reverse=True)[:100]


@app.get("/api/v1/products/{product_id}/upsell")
async def product_upsell(product_id: int, session=Depends(get_session)):
    products = await UpsellService.recommend(session, product_id)
    return [
        {
            "id": product.id,
            "name": product.name_ar,
            "price_usd": product.price_usd,
            "description": product.description,
        }
        for product in products
    ]


@app.get("/api/v1/orders/{order_type}/{order_id}/receipt")
async def order_receipt(
    order_type: str,
    order_id: int,
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    if order_type == "unified":
        result = await session.execute(
            select(UnifiedOrder)
            .options(selectinload(UnifiedOrder.product))
            .where(
                UnifiedOrder.id == order_id,
                UnifiedOrder.user_id == current_user.id,
            )
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return PlainTextResponse(
            ReceiptService.unified_text(order, current_user, order.product),
            headers={
                "Content-Disposition": f"attachment; filename={ReceiptService.filename(order_id)}"
            },
        )
    if order_type == "numbers":
        result = await session.execute(
            select(NumberOrder).where(
                NumberOrder.id == order_id,
                NumberOrder.user_id == current_user.id,
            )
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return PlainTextResponse(
            ReceiptService.number_text(order, current_user),
            headers={
                "Content-Disposition": f"attachment; filename={ReceiptService.filename(order_id, 'number')}"
            },
        )
    raise HTTPException(status_code=400, detail="invalid order type")


@app.get("/api/v1/cart", response_model=list[CartItemOut])
async def get_cart(
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    items = await CartService.get_items(session, current_user.id)
    return [
        CartItemOut(
            product_id=item.product_id,
            product_name=item.product.name_ar,
            target=item.target,
            quantity=item.quantity,
            unit_price_usd=item.product.price_usd,
            total_price_usd=CartService.item_total(item),
        )
        for item in items
        if item.product and item.product.status == ProductStatus.ACTIVE
    ]


@app.post("/api/v1/cart/items", response_model=CartItemOut)
async def add_cart_item(
    payload: CartItemIn,
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    try:
        item = await CartService.add(
            session,
            current_user.id,
            payload.product_id,
            payload.target.strip(),
            payload.quantity,
        )
    except CartError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CartItemOut(
        product_id=item.product_id,
        product_name=item.product.name_ar,
        target=item.target,
        quantity=item.quantity,
        unit_price_usd=item.product.price_usd,
        total_price_usd=CartService.item_total(item),
    )


@app.delete("/api/v1/cart/items/{product_id}")
async def remove_cart_item(
    product_id: int,
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    return {"removed": await CartService.remove(session, current_user.id, product_id)}


@app.delete("/api/v1/cart")
async def clear_cart(
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    await CartService.clear(session, current_user.id)
    return {"status": "cleared"}


@app.post("/api/v1/cart/checkout")
async def checkout_cart(
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    if await AbuseGuardService.is_blocked(session, current_user.id):
        raise HTTPException(status_code=429, detail="تم إيقاف العملية مؤقتاً للمراجعة")
    try:
        async with OperationLockService.acquire(f"cart-checkout:{current_user.id}"):
            result = await CartService.checkout(session, current_user.id)
    except OperationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    completed = [
        {
            "order_id": checkout.order.id,
            "status": checkout.order.status.value,
            "price_usd": checkout.order.price_usd,
            "delivery": checkout.delivery_value,
        }
        for _item, checkout in result["completed"]
    ]
    failed = [{"product_id": item.product_id, "error": error} for item, error in result["failed"]]
    return {"completed": completed, "failed": failed}


@app.post("/api/v1/checkout", response_model=CheckoutOut)
async def checkout(
    payload: CheckoutIn,
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    if await AbuseGuardService.is_blocked(session, current_user.id):
        raise HTTPException(status_code=429, detail="تم إيقاف العملية مؤقتاً للمراجعة")
    try:
        async with OperationLockService.acquire(f"checkout:{current_user.id}:{payload.product_id}"):
            result = await CheckoutService.purchase(
                session,
                current_user.id,
                payload.product_id,
                payload.target.strip(),
                payload.quantity,
            )
    except OperationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CheckoutError as exc:
        await AbuseGuardService.record_checkout_failure(session, current_user.id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CheckoutOut(
        order_id=result.order.id,
        status=result.order.status.value,
        price_usd=result.order.price_usd,
        discount_usd=result.discount,
        delivery=result.delivery_value,
    )


@app.get("/api/v1/watches")
async def watches(
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    rows = await WatchService.list_user_watches(session, current_user.id)
    return [
        {
            "product_id": row.product_id,
            "product_name": row.product.name_ar if row.product else None,
            "price_usd": row.product.price_usd if row.product else None,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.post("/api/v1/watches")
async def add_watch(
    payload: WatchIn,
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    result = await session.execute(
        select(ProductWatch).where(
            ProductWatch.user_id == current_user.id,
            ProductWatch.product_id == payload.product_id,
        )
    )
    if result.scalar_one_or_none() is None:
        await WatchService.toggle(session, current_user.id, payload.product_id)
    return {"status": "watching", "product_id": payload.product_id}


@app.delete("/api/v1/watches/{product_id}")
async def remove_watch(
    product_id: int,
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    result = await session.execute(
        select(ProductWatch).where(
            ProductWatch.user_id == current_user.id,
            ProductWatch.product_id == product_id,
        )
    )
    watch = result.scalar_one_or_none()
    if watch:
        await session.delete(watch)
        await session.commit()
    return {"status": "not_watching", "product_id": product_id}


@app.post("/api/v1/gifts/redeem")
async def redeem_gift(
    payload: GiftRedeemIn,
    current_user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    try:
        amount = await GiftService.redeem(session, current_user.id, payload.code)
    except GiftCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "redeemed", "amount_usd": amount}


webapp_dir = Path(__file__).resolve().parent.parent / "webapp"
app.mount("/app", StaticFiles(directory=webapp_dir, html=True), name="webapp")
app.mount("/admin-assets", StaticFiles(directory=webapp_dir), name="admin-assets")


@app.get("/admin/", include_in_schema=False)
def admin_webapp():
    return FileResponse(webapp_dir / "admin.html")

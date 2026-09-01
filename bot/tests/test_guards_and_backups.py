"""اختبارات الحمايات التشغيلية: الكوبونات، حرس الإساءة، التوصيات،
المراقبة، لقطة النسخ الاحتياطي، ووضع WAL لقاعدة البيانات."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    SubCategory,
    User,
)
from services.abuse_guard_service import AbuseGuardService
from services.coupon_service import CouponError, CouponService
from services.upsell_service import UpsellService
from services.watch_service import WatchService
from tasks.backup_job import _create_consistent_snapshot


async def make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, full_name=f"User {telegram_id}")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def make_products_in_one_subcategory(session) -> tuple[Product, Product]:
    """منتجان داخل نفس القسم الفرعي لاختبار التوصيات المكمّلة."""
    category = Category(name_ar="Games", emoji="🎮", type=CategoryType.APPS, is_active=True)
    session.add(category)
    await session.flush()
    subcategory = SubCategory(category_id=category.id, name_ar="TopUp", emoji="💎", is_active=True)
    session.add(subcategory)
    await session.flush()
    first = Product(
        sub_category_id=subcategory.id,
        name_ar="Game A",
        price_usd=Decimal("2"),
        cost_price_usd=Decimal("1"),
        status=ProductStatus.ACTIVE,
        fulfillment_type=ProductFulfillmentType.INVENTORY,
    )
    second = Product(
        sub_category_id=subcategory.id,
        name_ar="Game B",
        price_usd=Decimal("3"),
        cost_price_usd=Decimal("1"),
        status=ProductStatus.ACTIVE,
        fulfillment_type=ProductFulfillmentType.INVENTORY,
    )
    session.add_all([first, second])
    await session.commit()
    await session.refresh(first)
    await session.refresh(second)
    return first, second


# ── الكوبونات ──


@pytest.mark.asyncio
async def test_coupon_lifecycle_and_limits():
    async with async_session_maker() as session:
        admin = await make_user(session, 500)
        buyer = await make_user(session, 501)
        other = await make_user(session, 502)

        coupon = await CouponService.create_coupon(
            session,
            code="save10",
            discount_type="percent",
            discount_value=Decimal("10"),
            max_uses=1,
            created_by=admin.id,
            min_order_usd=Decimal("1"),
        )
        assert coupon.code == "SAVE10"

        # الحد الأدنى للطلب
        with pytest.raises(CouponError):
            await CouponService.validate_coupon(session, "SAVE10", buyer.id, Decimal("0.5"))

        # صالح ضمن الحد الأدنى + حساب الخصم النسبي
        valid = await CouponService.validate_coupon(
            session, " save10 ", buyer.id, Decimal("2.0")
        )
        assert valid.id == coupon.id
        discount = CouponService.calculate_discount(valid, Decimal("2.0"))
        assert discount == Decimal("0.2000")

        # خصم ثابت لا يتجاوز قيمة الطلب
        valid.discount_type = "fixed"
        valid.discount_value = Decimal("99")
        assert CouponService.calculate_discount(valid, Decimal("2.0")) == Decimal("2.0000")

        # التطبيق يستهلك الاستخدام الوحيد
        await CouponService.apply_coupon(session, coupon, buyer.id, discount)
        refreshed = await CouponService.get_coupon_by_code(session, "save10")
        assert refreshed.used_count == 1

        # نفس المستخدم لا يمكنه إعادة الاستخدام
        with pytest.raises(CouponError):
            await CouponService.validate_coupon(session, "SAVE10", buyer.id, Decimal("5"))

        # مستخدم آخر: الكوبون استُنفد (max_uses = 1)
        with pytest.raises(CouponError):
            await CouponService.validate_coupon(session, "SAVE10", other.id, Decimal("5"))

        # كوبون منتهي الصلاحية
        expired = await CouponService.create_coupon(
            session,
            code="old",
            discount_type="fixed",
            discount_value=Decimal("1"),
            max_uses=5,
            created_by=admin.id,
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        with pytest.raises(CouponError):
            await CouponService.validate_coupon(session, "OLD", other.id, Decimal("5"))
        assert expired is not None

        # كوبون غير موجود
        with pytest.raises(CouponError):
            await CouponService.validate_coupon(session, "NOPE", other.id, Decimal("5"))


# ── حرس الإساءة ──


@pytest.mark.asyncio
async def test_abuse_guard_blocks_only_repeat_offenders():
    async with async_session_maker() as session:
        abuser = await make_user(session, 600)
        normal = await make_user(session, 601)

        # فشل دفع واحد (4 نقاط) لا يكفي للحجب
        await AbuseGuardService.record_payment_failure(session, abuser.id, "double spend")
        assert await AbuseGuardService.is_blocked(session, abuser.id) is False

        # 5 إخفاقات دفع = 20 نقطة → محجوب
        for _ in range(4):
            await AbuseGuardService.record_payment_failure(session, abuser.id, "double spend")
        assert await AbuseGuardService.score(session, abuser.id) >= AbuseGuardService.BLOCK_SCORE
        assert await AbuseGuardService.is_blocked(session, abuser.id) is True

        # المستخدم الطبيعي غير متأثر
        await AbuseGuardService.record_checkout_failure(session, normal.id, "out of stock")
        assert await AbuseGuardService.is_blocked(session, normal.id) is False


# ── التوصيات المكمّلة والمراقبة ──


@pytest.mark.asyncio
async def test_upsell_recommends_same_subcategory_only():
    async with async_session_maker() as session:
        await make_user(session, 700)
        first, second = await make_products_in_one_subcategory(session)

        recommendations = await UpsellService.recommend(session, first.id)
        assert [p.id for p in recommendations] == [second.id]

        # منتج غير موجود → لا توصيات
        assert await UpsellService.recommend(session, 999_999) == []


@pytest.mark.asyncio
async def test_watch_toggle_roundtrip():
    async with async_session_maker() as session:
        user = await make_user(session, 800)
        first, _second = await make_products_in_one_subcategory(session)

        # تفعيل المراقبة
        assert await WatchService.toggle(session, user.id, first.id) is True
        watches = await WatchService.list_user_watches(session, user.id)
        assert len(watches) == 1
        assert watches[0].last_seen_price == Decimal("2")

        # إيقافها
        assert await WatchService.toggle(session, user.id, first.id) is False
        assert await WatchService.list_user_watches(session, user.id) == []

        # منتج غير فعال → رفض
        first.status = ProductStatus.INACTIVE
        await session.commit()
        with pytest.raises(ValueError):
            await WatchService.toggle(session, user.id, first.id)


# ── النسخ الاحتياطي ووضع WAL ──


@pytest.mark.asyncio
async def test_database_runs_in_wal_mode():
    async with async_session_maker() as session:
        result = await session.execute(text("PRAGMA journal_mode"))
        assert result.scalar_one().lower() == "wal"


@pytest.mark.asyncio
async def test_backup_snapshot_is_consistent():
    async with async_session_maker() as session:
        user = User(telegram_id=900, full_name="Backup Check")
        session.add(user)
        await session.commit()
        user_id = user.id

    snapshot = _create_consistent_snapshot(
        __import__("pathlib").Path("/tmp/number-bot-pytest.db")
    )
    connection = sqlite3.connect(str(snapshot))
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = connection.execute(
            "SELECT id FROM users WHERE telegram_id = 900"
        ).fetchall()
        assert rows == [(user_id,)]
    finally:
        connection.close()
        snapshot.unlink(missing_ok=True)

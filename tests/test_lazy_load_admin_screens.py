"""اختبارات تمنع عودة أخطاء التحميل الكسول (MissingGreenlet).

الخطأ الأصلي: أدمن يعدّل إيموجي/اسم قسم، فيُستدعى ``session.get`` ثم
``session.refresh`` ثم شاشة تفاصيل تقرأ ``category.sub_categories`` أو
``sub.products`` — والعلاقة غير محمّلة، فيرمي AsyncSession:
``greenlet_spawn has not been called``.

كل اختبار هنا يمرّر جلسة AsyncSession حقيقية ويستدعي المعالج مباشرة؛
قبل الإصلاح كان أول سطر في الشاشة يرمي MissingGreenlet.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    Product,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    SubCategory,
    Task,
    TaskRewardType,
    TaskSubmission,
    TaskType,
    User,
)


class _FakeMessage:
    """بديل رسالة تليجرام يجمع ما أُرسل."""

    def __init__(self, text: str = "💰"):
        self.text = text
        self.sent: list[str] = []

    async def answer(self, text: str, **_kwargs):
        self.sent.append(text)

    async def edit_text(self, text: str, **_kwargs):
        self.sent.append(text)


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.answers: list = []

    async def answer(self, *args, **kwargs):
        self.answers.append(kwargs)


class _FakeState:
    def __init__(self, data: dict):
        self._data = dict(data)
        self.cleared = False

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self._data = {}
        self.cleared = True


async def _make_admin(session) -> User:
    admin = User(
        telegram_id=990_001,
        username="owner",
        is_admin=True,
        balance=Decimal("100"),
        joined_at=datetime.utcnow(),
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return admin


async def _make_category_with_subs(session) -> Category:
    category = Category(
        name_ar="قسم الأرصدة",
        emoji="💳",
        type=CategoryType.BALANCES,
        sort_order=1,
        is_active=True,
    )
    session.add(category)
    await session.flush()
    session.add_all(
        [
            SubCategory(category_id=category.id, name_ar="أرصدة ألعاب", emoji="🎮", is_active=True),
            SubCategory(category_id=category.id, name_ar="أرصدة تطبيقات", emoji="📱", is_active=False),
        ]
    )
    await session.commit()
    await session.refresh(category)
    return category


# ══════════════ تعديل قسم رئيسي (الخطأ المُبلَّغ عنه) ══════════════


@pytest.mark.asyncio
async def test_category_edit_emoji_renders_details_without_lazy_load():
    """تعديل إيموجي قسم ثم عرض تفاصيله — دون تحميل كسول للأقسام الفرعية."""
    from handlers.admin.categories import cat_edit_value_received

    async with async_session_maker() as session:
        admin = await _make_admin(session)
        category = await _make_category_with_subs(session)

        message = _FakeMessage(text="💰")
        state = _FakeState({"edit_category_id": category.id, "edit_field": "emoji"})
        await cat_edit_value_received(
            message=message, state=state, session=session, db_user=admin
        )

        updated = await session.get(Category, category.id)
        assert updated.emoji == "💰"
        # شاشة التفاصيل رُسمت فعلاً (عدد الأقسام الفرعية ظاهر)
        assert any("عدد الأقسام الفرعية" in text for text in message.sent), message.sent


@pytest.mark.asyncio
async def test_category_toggle_renders_details_without_lazy_load():
    from handlers.admin.categories import cat_toggle

    async with async_session_maker() as session:
        admin = await _make_admin(session)
        category = await _make_category_with_subs(session)

        callback = _FakeCallback(f"admin:cat_toggle:{category.id}")
        await cat_toggle(callback=callback, session=session, db_user=admin)

        assert any("عدد الأقسام الفرعية" in text for text in callback.message.sent)


# ══════════════ تعديل قسم فرعي ══════════════


async def _make_sub_with_products(session):
    category = Category(
        name_ar="قسم شحن الألعاب",
        emoji="🎮",
        type=CategoryType.GAMES,
        sort_order=2,
        is_active=True,
    )
    session.add(category)
    await session.flush()
    sub = SubCategory(
        category_id=category.id, name_ar="ببجي", emoji="🎯", is_active=True
    )
    session.add(sub)
    await session.flush()
    session.add_all(
        [
            Product(
                sub_category_id=sub.id,
                name_ar="60 UC",
                price_usd=Decimal("1.00"),
                pricing_type=ProductPricingType.FIXED,
                fulfillment_type=ProductFulfillmentType.INVENTORY,
                status=ProductStatus.ACTIVE,
            ),
            Product(
                sub_category_id=sub.id,
                name_ar="325 UC",
                price_usd=Decimal("5.00"),
                pricing_type=ProductPricingType.FIXED,
                fulfillment_type=ProductFulfillmentType.INVENTORY,
                status=ProductStatus.INACTIVE,
            ),
        ]
    )
    await session.commit()
    await session.refresh(sub)
    return sub


@pytest.mark.asyncio
async def test_subcategory_edit_value_renders_details_without_lazy_load():
    from handlers.admin.categories import subcat_edit_value_received

    async with async_session_maker() as session:
        admin = await _make_admin(session)
        sub = await _make_sub_with_products(session)

        message = _FakeMessage(text="ببجي موبايل")
        state = _FakeState({"edit_sub_id": sub.id, "edit_field": "name"})
        await subcat_edit_value_received(
            message=message, state=state, session=session, db_user=admin
        )

        updated = await session.get(SubCategory, sub.id)
        assert updated.name_ar == "ببجي موبايل"
        assert any("عدد المنتجات" in text for text in message.sent), message.sent
        assert any("<b>1</b>" in text for text in message.sent), message.sent


@pytest.mark.asyncio
async def test_subcategory_toggle_renders_details_without_lazy_load():
    from handlers.admin.categories import subcat_toggle

    async with async_session_maker() as session:
        admin = await _make_admin(session)
        sub = await _make_sub_with_products(session)

        callback = _FakeCallback(f"admin:subcat_toggle:{sub.id}")
        await subcat_toggle(callback=callback, session=session, db_user=admin)

        assert any("عدد المنتجات" in text for text in callback.message.sent)


@pytest.mark.asyncio
async def test_create_child_section_renders_parent_without_lazy_load():
    """إنشاء قسم داخلي داخل تطبيق (رشق) يعرض صفحة التطبيق بلا تحميل كسول."""
    from handlers.admin.categories import _create_sub_category

    async with async_session_maker() as session:
        admin = await _make_admin(session)
        parent = await _make_sub_with_products(session)
        category_id = parent.category_id

        message = _FakeMessage(text="-")
        state = _FakeState(
            {
                "parent_category_id": category_id,
                "parent_sub_id": parent.id,
                "name": "متابعون",
                "emoji": "👥",
            }
        )
        await _create_sub_category(
            message=message, state=state, session=session, db_user=admin
        )

        assert any("عدد المنتجات" in text for text in message.sent), message.sent


# ══════════════ مراجعة تقديم مهمة ══════════════


@pytest.mark.asyncio
async def test_task_submission_view_without_lazy_load():
    from handlers.admin.tasks_center import submission_view

    async with async_session_maker() as session:
        student = User(
            telegram_id=990_002, username="student", joined_at=datetime.utcnow()
        )
        session.add(student)
        await session.flush()
        task = Task(
            key="test_task_share",
            title_ar="شارك البوت",
            description_ar="شارك الرابط",
            task_type=TaskType.INVITE_FRIEND,
            reward_type=TaskRewardType.POINTS,
            reward_points=10,
            is_active=True,
        )
        session.add(task)
        await session.flush()
        submission = TaskSubmission(
            user_id=student.id, task_id=task.id, content="تم", status="pending"
        )
        session.add(submission)
        await session.commit()

        callback = _FakeCallback(f"task_sub:{submission.id}")
        await submission_view(callback=callback, session=session)

        assert any("تقديم #" in text for text in callback.message.sent)
        assert any("@student" in text for text in callback.message.sent), callback.message.sent


# ══════════════ هوامش الربح ══════════════


@pytest.mark.asyncio
async def test_resolve_product_margin_loads_category_explicitly():
    from services.margin_service import MarginService

    async with async_session_maker() as session:
        sub = await _make_sub_with_products(session)
        from sqlalchemy import select as _select

        product = (
            await session.execute(_select(Product).where(Product.sub_category_id == sub.id))
        ).scalars().first()

        # القسم الفرعي بلا هامش → يُقرأ هامش القسم الرئيسي (علاقة category).
        percent, source = await MarginService.resolve_product_margin(session, product)
        assert isinstance(percent, Decimal)
        assert source in {"قسم", "قسم فرعي", "منتج", "عالمي"}, source

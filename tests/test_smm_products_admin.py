"""🚀 لوحة «منتجات قسم الرشق»: التطبيقات ← الأقسام الفرعية ← منتجات القسم.

يغطي:
- عدّ منتجات كل تطبيق (تراكمياً مع أقسامه الداخلية) وإخفاء التطبيقات الفارغة.
- أقسام التطبيق مع عدد منتجات كل قسم.
- صفحات منتجات القسم.
- تعطيل/تفعيل منتج واحد، وحذفه.
- تفعيل/تعطيل كل منتجات القسم دفعة واحدة، وحذفها كلها.
- نسبة الربح: تُطبَّق على كل منتجات القسم فوراً (حتى ذات الهامش اليدوي)،
  و0 = إلغاء النسبة والعودة للوراثة.
- الأزرار: وجود المسار في تبويب المتجر وفي الكيبوردات.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    SubCategory,
)
from keyboards.admin import ADMIN_TABS
from keyboards.admin_smm_products import (
    smm_apps_kb,
    smm_products_kb,
    smm_sections_kb,
)
from services.smm_admin_service import SmmAdminService


async def _seed() -> dict:
    """قسم رشق فيه تطبيقان: إنستغرام (قسمان داخليان) وتيك توك (فارغ)."""
    async with async_session_maker() as session:
        # قاعدة الاختبار المزروعة تحوي قسم رشق جاهزاً بتطبيقاته العشرة —
        # نزيله لتبقى الأرقام في هذا الملف محسوبة بدقة.
        existing = (
            await session.execute(
                select(Category).where(Category.type == CategoryType.SMM)
            )
        ).scalars().all()
        for row in existing:
            await session.delete(row)
        await session.flush()

        category = Category(name_ar="رشق سوشيال", emoji="📈", type=CategoryType.SMM)
        session.add(category)
        await session.flush()

        insta = SubCategory(category_id=category.id, name_ar="إنستغرام", emoji="📸")
        tiktok = SubCategory(category_id=category.id, name_ar="تيك توك", emoji="🎵")
        session.add_all([insta, tiktok])
        await session.flush()

        followers = SubCategory(
            category_id=category.id,
            parent_sub_category_id=insta.id,
            kind_key="followers",
            name_ar="متابعون",
            emoji="👤",
        )
        likes = SubCategory(
            category_id=category.id,
            parent_sub_category_id=insta.id,
            kind_key="likes",
            name_ar="لايكات",
            emoji="❤️",
        )
        session.add_all([followers, likes])
        await session.flush()

        def _product(sub_id, name, cost, price, status=ProductStatus.ACTIVE, manual=False):
            return Product(
                sub_category_id=sub_id,
                name_ar=name,
                price_usd=Decimal(price),
                cost_price_usd=Decimal(cost),
                status=status,
                fulfillment_type=ProductFulfillmentType.API,
                margin_manual=manual,
                profit_margin_percent=Decimal("20") if manual else None,
            )

        session.add_all(
            [
                _product(followers.id, "متابعون إنستغرام 1000", "1.00", "1.50"),
                _product(followers.id, "متابعون إنستغرام 5000", "2.00", "3.00"),
                _product(
                    followers.id,
                    "متابعون إنستغرام يدوي",
                    "4.00",
                    "4.80",
                    manual=True,
                ),
                _product(
                    followers.id,
                    "متابعون معطّل",
                    "1.00",
                    "1.50",
                    status=ProductStatus.INACTIVE,
                ),
                _product(likes.id, "لايكات إنستغرام 500", "0.50", "0.75"),
            ]
        )
        await session.commit()

        return {
            "category_id": category.id,
            "insta_id": insta.id,
            "tiktok_id": tiktok.id,
            "followers_id": followers.id,
            "likes_id": likes.id,
        }


# ══════════════ الاستعراض ══════════════


async def test_apps_overview_counts_products_of_inner_sections():
    ids = await _seed()
    async with async_session_maker() as session:
        rows = await SmmAdminService.apps_overview(session, ids["category_id"])

    # تيك توك بلا منتجات → مخفي افتراضياً
    assert [row.sub.id for row in rows] == [ids["insta_id"]]
    insta = rows[0]
    assert insta.total == 5
    assert insta.active == 4
    assert insta.inactive == 1
    assert insta.sections == 2


async def test_apps_overview_can_include_empty_apps():
    ids = await _seed()
    async with async_session_maker() as session:
        rows = await SmmAdminService.apps_overview(
            session, ids["category_id"], only_with_products=False
        )
    by_id = {row.sub.id: row for row in rows}
    assert set(by_id) == {ids["insta_id"], ids["tiktok_id"]}
    assert by_id[ids["tiktok_id"]].total == 0


async def test_sections_overview_lists_inner_sections_with_counts():
    ids = await _seed()
    async with async_session_maker() as session:
        rows = await SmmAdminService.sections_overview(session, ids["insta_id"])
    counts = {row.sub.name_ar: (row.total, row.active) for row in rows}
    assert counts == {"متابعون": (4, 3), "لايكات": (1, 1)}


async def test_products_page_paginates():
    ids = await _seed()
    async with async_session_maker() as session:
        page0, total, pages = await SmmAdminService.products_page(
            session, ids["followers_id"], page=0, per_page=3
        )
        page1, _total, _pages = await SmmAdminService.products_page(
            session, ids["followers_id"], page=1, per_page=3
        )
    assert total == 4
    assert pages == 2
    assert len(page0) == 3
    assert len(page1) == 1


# ══════════════ منتج واحد ══════════════


async def test_toggle_product_disables_then_enables():
    ids = await _seed()
    async with async_session_maker() as session:
        products, _total, _pages = await SmmAdminService.products_page(
            session, ids["likes_id"]
        )
        product_id = products[0].id
        product = await SmmAdminService.toggle_product(session, product_id)
        assert product.status == ProductStatus.INACTIVE
        product = await SmmAdminService.toggle_product(session, product_id)
        assert product.status == ProductStatus.ACTIVE


async def test_delete_single_product():
    ids = await _seed()
    async with async_session_maker() as session:
        products, _total, _pages = await SmmAdminService.products_page(
            session, ids["likes_id"]
        )
        assert await SmmAdminService.delete_product(session, products[0].id) is True

    async with async_session_maker() as session:
        _rows, total, _pages = await SmmAdminService.products_page(
            session, ids["likes_id"]
        )
    assert total == 0


# ══════════════ عمليات جماعية ══════════════


async def test_bulk_disable_and_enable_section():
    ids = await _seed()
    async with async_session_maker() as session:
        changed = await SmmAdminService.bulk_set_status(
            session, ids["followers_id"], active=False
        )
        assert changed == 3  # واحد كان معطّلاً أصلاً
        stats = await SmmAdminService.node_stats(session, ids["followers_id"])
        assert stats.active == 0

        changed = await SmmAdminService.bulk_set_status(
            session, ids["followers_id"], active=True
        )
        assert changed == 4
        stats = await SmmAdminService.node_stats(session, ids["followers_id"])
        assert stats.active == 4


async def test_bulk_status_on_app_covers_inner_sections():
    ids = await _seed()
    async with async_session_maker() as session:
        await SmmAdminService.bulk_set_status(session, ids["insta_id"], active=False)
        stats = await SmmAdminService.node_stats(session, ids["insta_id"])
    assert stats.total == 5
    assert stats.active == 0


async def test_delete_all_section_products_keeps_section():
    ids = await _seed()
    async with async_session_maker() as session:
        deleted, errors = await SmmAdminService.delete_section_products(
            session, ids["followers_id"]
        )
    assert deleted == 4
    assert errors == 0

    async with async_session_maker() as session:
        sub = await SmmAdminService.get_sub(session, ids["followers_id"])
        stats = await SmmAdminService.node_stats(session, ids["followers_id"])
    assert sub is not None  # القسم يبقى
    assert stats.total == 0


# ══════════════ نسبة الربح ══════════════


async def test_apply_margin_reprices_every_product_in_section():
    ids = await _seed()
    async with async_session_maker() as session:
        sub = await SmmAdminService.get_sub(session, ids["followers_id"])
        report = await SmmAdminService.apply_margin(session, sub, Decimal("100"))

    assert report["percent"] == Decimal("100.00")
    assert report["products"] == 4
    assert report["repriced"] == 4

    async with async_session_maker() as session:
        rows, _total, _pages = await SmmAdminService.products_page(
            session, ids["followers_id"], per_page=50
        )
        prices = {row.name_ar: Decimal(str(row.price_usd)) for row in rows}
        sub = await SmmAdminService.get_sub(session, ids["followers_id"])

    assert sub.profit_margin_percent == Decimal("100.00")
    # التكلفة × 2 لكل المنتجات — حتى ذات الهامش اليدوي السابق
    assert prices["متابعون إنستغرام 1000"] == Decimal("2.0000")
    assert prices["متابعون إنستغرام 5000"] == Decimal("4.0000")
    assert prices["متابعون إنستغرام يدوي"] == Decimal("8.0000")


async def test_apply_margin_does_not_touch_sibling_section():
    ids = await _seed()
    async with async_session_maker() as session:
        sub = await SmmAdminService.get_sub(session, ids["followers_id"])
        await SmmAdminService.apply_margin(session, sub, Decimal("100"))

    async with async_session_maker() as session:
        rows, _total, _pages = await SmmAdminService.products_page(
            session, ids["likes_id"]
        )
    assert Decimal(str(rows[0].price_usd)) == Decimal("0.75")


async def test_apply_margin_on_app_covers_all_inner_sections():
    ids = await _seed()
    async with async_session_maker() as session:
        app = await SmmAdminService.get_sub(session, ids["insta_id"])
        report = await SmmAdminService.apply_margin(session, app, Decimal("50"))
    assert report["products"] == 5

    async with async_session_maker() as session:
        rows, _total, _pages = await SmmAdminService.products_page(
            session, ids["likes_id"]
        )
    assert Decimal(str(rows[0].price_usd)) == Decimal("0.7500")


async def test_clearing_margin_returns_to_inherited_value():
    ids = await _seed()
    async with async_session_maker() as session:
        category = await session.get(Category, ids["category_id"])
        category.profit_margin_percent = Decimal("10")
        await session.commit()

        sub = await SmmAdminService.get_sub(session, ids["followers_id"])
        await SmmAdminService.apply_margin(session, sub, Decimal("100"))

    async with async_session_maker() as session:
        sub = await SmmAdminService.get_sub(session, ids["followers_id"])
        await SmmAdminService.apply_margin(session, sub, None)

    async with async_session_maker() as session:
        sub = await SmmAdminService.get_sub(session, ids["followers_id"])
        rows, _total, _pages = await SmmAdminService.products_page(
            session, ids["followers_id"], per_page=50
        )
        prices = {row.name_ar: Decimal(str(row.price_usd)) for row in rows}

    assert sub.profit_margin_percent is None
    # عاد لوراثة هامش القسم الرئيسي (10%)
    assert prices["متابعون إنستغرام 1000"] == Decimal("1.1000")


async def test_effective_margin_text_shows_inheritance():
    ids = await _seed()
    async with async_session_maker() as session:
        sub = await SmmAdminService.get_sub(session, ids["followers_id"])
        inherited = await SmmAdminService.effective_margin_text(session, sub)
        await SmmAdminService.apply_margin(session, sub, Decimal("35"))
        own = await SmmAdminService.effective_margin_text(session, sub)
    assert "موروث" in inherited
    assert own.startswith("35")


# ══════════════ الأزرار ══════════════


def _data(kb) -> list[str]:
    return [button.callback_data for row in kb.inline_keyboard for button in row]


def test_store_tab_has_smm_products_button():
    _title, items = ADMIN_TABS["store"]
    assert ("🚀 منتجات قسم الرشق", "admin:smm_products") in items


def test_apps_keyboard_links_to_each_app():
    class _Sub:
        id = 7
        emoji = "📸"
        name_ar = "إنستغرام"
        sort_order = 0

    class _Row:
        sub = _Sub()
        total = 12
        active = 10
        label = "📸 إنستغرام"

    kb = smm_apps_kb(3, [_Row()], only_with_products=True, has_hidden=True)
    data = _data(kb)
    assert "smmp:app:7" in data
    assert "smmp:cat:3:0" in data  # زر إظهار التطبيقات الفارغة


def test_sections_keyboard_has_margin_and_bulk_buttons():
    class _Sub:
        id = 11
        emoji = "👤"
        name_ar = "متابعون"
        sort_order = 0

    class _Row:
        sub = _Sub()
        total = 4
        active = 3
        label = "👤 متابعون"

    kb = smm_sections_kb(7, 3, [_Row()], direct_products=2)
    data = _data(kb)
    assert "smmp:sec:11:0" in data
    assert "smmp:sec:7:0" in data  # منتجات التطبيق المباشرة
    assert "smmp:margin:7" in data
    assert "smmp:on:7" in data and "smmp:off:7" in data
    assert "smmp:cat:3:1" in data


def test_products_keyboard_has_toggle_delete_and_margin():
    product = Product(
        name_ar="متابعون إنستغرام 1000",
        price_usd=Decimal("1.5"),
        cost_price_usd=Decimal("1"),
        status=ProductStatus.ACTIVE,
    )
    product.id = 55
    kb = smm_products_kb(
        11, [product], page=0, pages=2, parent_id=7, category_id=3
    )
    data = _data(kb)
    assert "smmp:tg:55:0" in data
    assert "smmp:del:55:0" in data
    assert "smmp:margin:11" in data
    assert "smmp:sec_delete:11" in data
    assert "smmp:sec:11:1" in data  # صفحة تالية
    assert "smmp:app:7" in data  # رجوع لأقسام التطبيق


# ══════════════ تجربة المعالجات نفسها (مسار الأدمن كاملاً) ══════════════


class _FakeMessage:
    def __init__(self):
        self.texts: list[str] = []
        self.markups: list[object] = []

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.texts.append(text)
        self.markups.append(reply_markup)

    async def answer(self, text, reply_markup=None, **kwargs):
        self.texts.append(text)
        self.markups.append(reply_markup)


class _FakeCallback:
    def __init__(self, data: str, message: _FakeMessage | None = None):
        self.data = data
        self.message = message or _FakeMessage()
        self.answers: list[tuple] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))


class _FakeState:
    def __init__(self):
        self.data: dict = {}
        self.state = None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        return self.data

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data = {}
        self.state = None


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def test_handler_flow_apps_then_sections_then_products():
    from handlers.admin import smm_products as handler

    ids = await _seed()
    async with async_session_maker() as session:
        home = _FakeCallback("admin:smm_products")
        await handler.smm_products_home(home, session)
        assert "منتجات قسم الرشق" in home.message.texts[-1]
        assert f"smmp:app:{ids['insta_id']}" in _callbacks(home.message.markups[-1])

        app = _FakeCallback(f"smmp:app:{ids['insta_id']}")
        await handler.smm_app_sections(app, session)
        app_buttons = _callbacks(app.message.markups[-1])
        assert f"smmp:sec:{ids['followers_id']}:0" in app_buttons
        assert f"smmp:margin:{ids['insta_id']}" in app_buttons

        section = _FakeCallback(f"smmp:sec:{ids['followers_id']}:0")
        await handler.smm_section_products(section, session)
        section_buttons = _callbacks(section.message.markups[-1])
        assert any(b.startswith("smmp:tg:") for b in section_buttons)
        assert any(b.startswith("smmp:del:") for b in section_buttons)
        assert f"smmp:margin:{ids['followers_id']}" in section_buttons


async def test_handler_toggle_and_delete_single_product():
    from handlers.admin import smm_products as handler

    ids = await _seed()
    async with async_session_maker() as session:
        rows, _total, _pages = await SmmAdminService.products_page(
            session, ids["likes_id"]
        )
        product_id = rows[0].id

        toggle = _FakeCallback(f"smmp:tg:{product_id}:0")
        await handler.smm_toggle_product(toggle, session)
        product = await session.get(Product, product_id)
        assert product.status == ProductStatus.INACTIVE

        ask = _FakeCallback(f"smmp:del:{product_id}:0")
        await handler.smm_delete_product_ask(ask, session)
        assert f"smmp:delete_go:{product_id}:0" in _callbacks(ask.message.markups[-1])

        confirm = _FakeCallback(f"smmp:delete_go:{product_id}:0")
        await handler.smm_delete_product_go(confirm, session)
        assert await session.get(Product, product_id) is None


async def test_handler_margin_flow_sets_percent_for_whole_section():
    from handlers.admin import smm_products as handler

    ids = await _seed()
    state = _FakeState()
    async with async_session_maker() as session:
        start = _FakeCallback(f"smmp:margin:{ids['followers_id']}")
        await handler.smm_margin_start(start, state, session)
        assert state.data["smm_margin_sub_id"] == ids["followers_id"]
        assert state.data["smm_margin_is_app"] is False

        message = _FakeMessage()
        message.text = "٧٥٪"  # أرقام عربية + رمز نسبة
        await handler.smm_margin_received(message, state, session)

    async with async_session_maker() as session:
        sub = await SmmAdminService.get_sub(session, ids["followers_id"])
        rows, _total, _pages = await SmmAdminService.products_page(
            session, ids["followers_id"], per_page=50
        )
        prices = {row.name_ar: Decimal(str(row.price_usd)) for row in rows}

    assert sub.profit_margin_percent == Decimal("75.00")
    assert prices["متابعون إنستغرام 1000"] == Decimal("1.7500")
    assert state.state is None  # انتهى التسلسل


async def test_handler_margin_rejects_invalid_value():
    from handlers.admin import smm_products as handler

    ids = await _seed()
    state = _FakeState()
    async with async_session_maker() as session:
        start = _FakeCallback(f"smmp:margin:{ids['followers_id']}")
        await handler.smm_margin_start(start, state, session)

        message = _FakeMessage()
        message.text = "كثير"
        await handler.smm_margin_received(message, state, session)

    assert "نسبة صحيحة" in message.texts[-1]
    assert state.state is not None  # ما زال ينتظر قيمة صحيحة


async def test_handler_bulk_disable_then_purge_section():
    from handlers.admin import smm_products as handler

    ids = await _seed()
    async with async_session_maker() as session:
        off = _FakeCallback(f"smmp:off:{ids['followers_id']}")
        await handler.smm_bulk_deactivate(off, session)
        stats = await SmmAdminService.node_stats(session, ids["followers_id"])
        assert stats.active == 0

        ask = _FakeCallback(f"smmp:sec_delete:{ids['followers_id']}")
        await handler.smm_purge_ask(ask, session)
        assert f"smmp:sec_delete_go:{ids['followers_id']}" in _callbacks(
            ask.message.markups[-1]
        )

        go = _FakeCallback(f"smmp:sec_delete_go:{ids['followers_id']}")
        await handler.smm_purge_go(go, session)
        stats = await SmmAdminService.node_stats(session, ids["followers_id"])
        assert stats.total == 0
        assert stats.sub is not None

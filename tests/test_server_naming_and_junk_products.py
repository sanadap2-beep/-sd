"""تعديلان مطلوبان من صاحب البوت:

١) سيرفرات قسم الأرقام تُعرض للمستخدم باسم محايد مرقّم (سيرفر 1، سيرفر 2...)
   ولا تكشف اسم المزود (5sim / HeroSMS ...)، ومن لوحة الأدمن تُوضع نقطة
   خضراء 🟢 أمام السيرفر الذي يعمل فعلياً.

٢) منتجات الرشق الوهمية — اسمها فيه «سيرفر» وسعرها صفر/شبه صفر — تُحذف من
   كل التطبيقات وكل أقسامها الفرعية بزر واحد، وتُستبدل بخدمات لها سعر صحيح.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    ApiProtocolType,
    ApiProvider,
    ApiProviderType,
    Category,
    CategoryType,
    NumberServer,
    NumberService,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    ProviderName,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
)
from services.junk_products_service import (
    JUNK_PRICE_THRESHOLD,
    JunkProductsService,
    has_server_word,
    is_junk_price,
    is_junk_product,
    is_junk_service,
)
from services.number_server_service import (
    NumberServerService,
    public_labels_for,
    public_server_label,
    public_server_name,
)
from services.smm_admin_service import SmmAdminService


# ════════════════════════════════════════════════════════════════
#  ١) سيرفرات الأرقام: أسماء مرقّمة + نقطة خضراء
# ════════════════════════════════════════════════════════════════


async def _seed_number_service(session, code="wa") -> NumberService:
    svc = NumberService(
        code=code,
        name_ar="واتساب",
        emoji="💬",
        fivesim_code="wa",
        herosms_code="wa_hero",
        sms_activate_code="wa_act",
        is_active=True,
    )
    session.add(svc)
    await session.commit()
    await session.refresh(svc)
    return svc


def test_public_names_are_numbered_not_provider_names():
    """«سيرفر 1، سيرفر 2...» بدل 5sim / HeroSMS."""
    assert public_server_name(1) == "سيرفر 1"
    assert public_server_name(3) == "سيرفر 3"
    # لا يظهر اسم أي مزود في النص المعروض
    for position in range(1, 6):
        label = public_server_label(position)
        for provider in ("5sim", "HeroSMS", "SMS-Activate", "SMSHub", "herosms"):
            assert provider.lower() not in label.lower()


def test_public_label_shows_green_dot_only_when_working():
    assert public_server_label(2, is_working=True).startswith("🟢")
    assert not public_server_label(2, is_working=False).startswith("🟢")
    assert "سيرفر 2" in public_server_label(2, is_working=True)


async def test_ensure_defaults_saves_numbered_names_not_provider_labels():
    """الإنشاء التلقائي يحفظ «سيرفر N» لأن المستخدم يرى هذا الاسم."""
    async with async_session_maker() as session:
        svc = await _seed_number_service(session, code="wa_num")
        servers = await NumberServerService.ensure_defaults(session, svc)

    names = [server.name_ar for server in servers]
    assert names == ["سيرفر 1", "سيرفر 2", "سيرفر 3"]
    # المزود محفوظ في العمود الخاص به — للأدمن فقط، لا في الاسم
    providers = {server.provider for server in servers}
    assert providers == {
        ProviderName.FIVESIM.value,
        ProviderName.HEROSMS.value,
        ProviderName.SMS_ACTIVATE.value,
    }
    for server in servers:
        assert server.provider not in server.name_ar


async def test_renumber_fixes_legacy_provider_named_servers():
    """قاعدة قديمة فيها «HeroSMS» كاسم → تُصلَح إلى «سيرفر N»."""
    async with async_session_maker() as session:
        svc = await _seed_number_service(session, code="tg_num")
        session.add_all(
            [
                NumberServer(
                    number_service_id=svc.id,
                    provider=ProviderName.HEROSMS.value,
                    name_ar="HeroSMS",
                    emoji="🟠",
                    is_active=True,
                    sort_order=10,
                ),
                NumberServer(
                    number_service_id=svc.id,
                    provider=ProviderName.FIVESIM.value,
                    name_ar="5sim",
                    emoji="🟢",
                    is_active=True,
                    sort_order=20,
                ),
            ]
        )
        await session.commit()

        changed = await NumberServerService.renumber(session, svc.id)
        servers = await NumberServerService.list_servers(session, svc.id, active_only=False)

    assert changed > 0
    assert [s.name_ar for s in servers] == ["سيرفر 1", "سيرفر 2"]
    assert all(s.emoji == "🖥" for s in servers)


async def test_admin_can_mark_working_server_green_dot():
    """الأدمن يعلّم السيرفر الشغّال، فتظهر 🟢 أمامه للمستخدم."""
    async with async_session_maker() as session:
        svc = await _seed_number_service(session, code="ig_num")
        servers = await NumberServerService.ensure_defaults(session, svc)
        hero = next(s for s in servers if s.provider == ProviderName.HEROSMS.value)

        assert hero.is_working is False  # الافتراضي: بلا نقطة

        updated = await NumberServerService.set_working(session, hero.id, True)
        assert updated.is_working is True

        label = await NumberServerService.public_label(session, updated)
        assert label.startswith("🟢")
        # النقطة على السيرفر الصحيح دون كشف أنه HeroSMS
        assert "سيرفر 2" in label
        assert "hero" not in label.lower()

        # الإزالة تعمل أيضاً
        cleared = await NumberServerService.set_working(session, hero.id, False)
        assert cleared.is_working is False


async def test_set_working_exclusive_clears_other_servers():
    async with async_session_maker() as session:
        svc = await _seed_number_service(session, code="fb_num")
        servers = await NumberServerService.ensure_defaults(session, svc)
        await NumberServerService.set_working(session, servers[0].id, True)
        await NumberServerService.set_working(
            session, servers[1].id, True, exclusive=True
        )
        rows = await NumberServerService.list_servers(session, svc.id, active_only=False)

    working = [row.id for row in rows if row.is_working]
    assert working == [servers[1].id]


async def test_user_keyboard_never_shows_provider_name():
    """لوحة اختيار السيرفر للمستخدم: أرقام فقط، بلا أسماء مزودين."""
    from handlers.numbers import _servers_kb

    async with async_session_maker() as session:
        svc = await _seed_number_service(session, code="yt_num")
        servers = await NumberServerService.ensure_defaults(session, svc)
        await NumberServerService.set_working(session, servers[1].id, True)
        active = await NumberServerService.list_servers(session, svc.id, active_only=True)

    kb = _servers_kb("yt_num", active)
    texts = [b.text for row in kb.inline_keyboard for b in row]

    assert "🖥 سيرفر 1" in texts
    assert "🟢 🖥 سيرفر 2" in texts  # النقطة الخضراء على الثاني
    assert "🖥 سيرفر 3" in texts
    joined = " ".join(texts).lower()
    for provider in ("5sim", "herosms", "hero sms", "sms-activate", "smshub"):
        assert provider not in joined


def test_public_labels_for_numbers_sequentially():
    servers = [
        NumberServer(id=5, number_service_id=1, provider="fivesim", name_ar="x"),
        NumberServer(id=9, number_service_id=1, provider="herosms", name_ar="y"),
    ]
    assert public_labels_for(servers) == {5: "سيرفر 1", 9: "سيرفر 2"}


async def test_admin_keyboard_still_shows_provider_for_admin_only():
    """الأدمن وحده يرى المزود بجانب الاسم المرقّم."""
    from keyboards.admin import admin_nsvc_servers_kb

    async with async_session_maker() as session:
        svc = await _seed_number_service(session, code="tw_num")
        servers = await NumberServerService.ensure_defaults(session, svc)
        await NumberServerService.set_working(session, servers[1].id, True)
        rows = await NumberServerService.list_servers(session, svc.id, active_only=False)

    kb = admin_nsvc_servers_kb(svc.id, rows)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    joined = " ".join(texts)

    assert "سيرفر 1" in joined
    assert "herosms" in joined  # المزود ظاهر للأدمن
    assert "🟢شغّال" in joined  # علامة السيرفر الشغّال
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(cb.startswith("admin:nsvc_server_working:") is False for cb in callbacks)
    assert f"admin:nsvc_server_renumber:{svc.id}" in callbacks


async def test_admin_detail_keyboard_has_green_dot_toggle():
    from keyboards.admin import admin_nsvc_server_detail_kb

    async with async_session_maker() as session:
        svc = await _seed_number_service(session, code="sc_num")
        servers = await NumberServerService.ensure_defaults(session, svc)
        server = servers[0]

    kb = admin_nsvc_server_detail_kb(svc.id, server)
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"admin:nsvc_server_working:{server.id}" in callbacks
    # لم يعد الأدمن يعدّل الاسم/الإيموجي يدوياً (الاسم مولّد ومحايد)
    assert f"admin:nsvc_server_edit_name:{server.id}" not in callbacks
    assert f"admin:nsvc_server_edit_emoji:{server.id}" not in callbacks


# ════════════════════════════════════════════════════════════════
#  ٢) منتجات «سيرفر» الوهمية في الرشق
# ════════════════════════════════════════════════════════════════


def test_has_server_word_matches_arabic_and_english():
    assert has_server_word("متابعين انستجرام سيرفر 1")
    assert has_server_word("لايكات تيك توك سيرفر٢")
    assert has_server_word("Instagram Followers [Server 2]")
    assert has_server_word("TikTok Views - server3")
    assert has_server_word("Views (SRV 4)")
    # لا التقاط خاطئ لكلمات عادية
    assert not has_server_word("متابعون حقيقيون سريع")
    assert not has_server_word("Instagram Followers Real")
    assert not has_server_word("")


def test_is_junk_price_uses_threshold():
    assert is_junk_price(Decimal("0")) is True
    assert is_junk_price(Decimal("0.01")) is True
    assert is_junk_price(JUNK_PRICE_THRESHOLD) is True
    assert is_junk_price(Decimal("0.50")) is False
    # سعر بيع بلا تكلفة = لا ربح محسوب
    assert is_junk_price(Decimal("2.00"), cost=Decimal("0")) is True
    assert is_junk_price(Decimal("2.00"), cost=Decimal("1.00")) is False


def test_is_junk_product_requires_both_name_and_price():
    def _p(name, price, cost="0.5"):
        return Product(
            sub_category_id=1,
            name_ar=name,
            price_usd=Decimal(price),
            cost_price_usd=Decimal(cost),
        )

    # اسمه سيرفر + سعره صفر = وهمي
    assert is_junk_product(_p("متابعين انستجرام سيرفر 1", "0", "0")) is True
    assert is_junk_product(_p("لايكات سيرفر 2", "0.01", "0")) is True
    # اسمه سيرفر لكن سعره حقيقي = منتج سليم لا يُحذف
    assert is_junk_product(_p("متابعين سيرفر مميز", "1.50", "1.00")) is False
    # سعره صفر لكن اسمه ليس سيرفر = ليس من اختصاص هذا الزر
    assert is_junk_product(_p("متابعون إنستغرام", "0", "0")) is False


def test_is_junk_service_blocks_unpriced_catalog_lines():
    class _S:
        def __init__(self, name, rate, name_ar=""):
            self.name = name
            self.name_ar = name_ar
            self.rate_usd = Decimal(str(rate))

    assert is_junk_service(_S("Server 1", 0)) is True
    assert is_junk_service(_S("Instagram Followers Server 2", "0.001")) is True
    assert is_junk_service(_S("خدمة", 0, name_ar="سيرفر 3")) is True
    assert is_junk_service(_S("Instagram Followers Real", "0.45")) is False


async def _seed_smm_with_junk() -> dict:
    """قسم رشق: تطبيقان، أقسام داخلية، منتجات سليمة + وهمية «سيرفر»."""
    async with async_session_maker() as session:
        for row in (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalars().all():
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
            parent_sub_category_id=tiktok.id,
            kind_key="likes",
            name_ar="لايكات",
            emoji="❤️",
        )
        session.add_all([followers, likes])
        await session.flush()

        provider = ApiProvider(
            name="SmmKing",
            type=ApiProviderType.SMM,
            protocol_type=ApiProtocolType.SMM_V2,
            api_url="https://example.test/api",
            api_key="key",
            is_active=True,
        )
        session.add(provider)
        await session.flush()

        # خدمة مسحوبة مسعّرة صالحة كبديل لقسم متابعي إنستغرام
        spare = ProviderService(
            api_provider_id=provider.id,
            external_service_id="5001",
            name="Instagram Followers Real HQ",
            name_ar="متابعون حقيقي إنستغرام",
            category="Instagram Followers",
            rate=Decimal("0.80"),
            rate_usd=Decimal("0.80"),
            min_quantity=100,
            max_quantity=10000,
            requires_quantity=True,
            status=ProviderServiceStatus.ACTIVE,
        )
        # سطر كتالوج وهمي: لا يصلح بديلاً
        junk_service = ProviderService(
            api_provider_id=provider.id,
            external_service_id="5002",
            name="Instagram Followers Server 9",
            name_ar="متابعين انستجرام سيرفر 9",
            category="Instagram Followers",
            rate=Decimal("0"),
            rate_usd=Decimal("0"),
            status=ProviderServiceStatus.ACTIVE,
        )
        session.add_all([spare, junk_service])
        await session.flush()

        def _product(sub_id, name, price, cost="0"):
            return Product(
                sub_category_id=sub_id,
                api_provider_id=provider.id,
                name_ar=name,
                price_usd=Decimal(price),
                cost_price_usd=Decimal(cost),
                status=ProductStatus.ACTIVE,
                fulfillment_type=ProductFulfillmentType.API,
            )

        good = _product(followers.id, "متابعون إنستغرام حقيقي", "1.50", "1.00")
        junk_1 = _product(followers.id, "متابعين انستجرام سيرفر 1", "0")
        junk_2 = _product(followers.id, "متابعين انستجرام سيرفر 2", "0.01")
        junk_3 = _product(likes.id, "لايكات تيك توك سيرفر 1", "0")
        junk_4 = _product(likes.id, "TikTok Likes [Server 5]", "0.02")
        # اسمه سيرفر لكن مسعّر فعلاً → يبقى
        priced_server = _product(likes.id, "لايكات تيك توك سيرفر ذهبي", "2.00", "1.20")

        session.add_all([good, junk_1, junk_2, junk_3, junk_4, priced_server])
        await session.commit()

        return {
            "category_id": category.id,
            "insta_id": insta.id,
            "tiktok_id": tiktok.id,
            "followers_id": followers.id,
            "likes_id": likes.id,
            "good_id": good.id,
            "priced_server_id": priced_server.id,
            "spare_service_id": spare.id,
        }


async def test_finds_junk_across_all_apps_and_sections():
    await _seed_smm_with_junk()
    async with async_session_maker() as session:
        found = await JunkProductsService.find_all_smm(session)
        names = sorted(p.name_ar for p in found)

    # 4 وهميين من تطبيقين مختلفين وقسمين فرعيين مختلفين
    assert set(names) == {
        "TikTok Likes [Server 5]",
        "لايكات تيك توك سيرفر 1",
        "متابعين انستجرام سيرفر 1",
        "متابعين انستجرام سيرفر 2",
    }


async def test_clean_deletes_junk_keeps_valid_products():
    await _seed_smm_with_junk()
    async with async_session_maker() as session:
        report = await JunkProductsService.clean(session, replace=False)

    assert report.deleted == 4
    assert report.sections == 2

    async with async_session_maker() as session:
        remaining = list(
            (await session.execute(select(Product))).scalars().all()
        )
    names = {p.name_ar for p in remaining}
    # المنتج السليم والمنتج «سيرفر» المسعّر باقيان
    assert names == {"متابعون إنستغرام حقيقي", "لايكات تيك توك سيرفر ذهبي"}


async def test_clean_replaces_junk_with_priced_service():
    """الاستبدال: يُنشر منتج له سعر صحيح مكان المحذوف."""
    ids = await _seed_smm_with_junk()
    async with async_session_maker() as session:
        report = await JunkProductsService.clean(session, replace=True)

    assert report.deleted == 4
    assert report.replaced >= 1

    async with async_session_maker() as session:
        published = list(
            (
                await session.execute(
                    select(Product).where(
                        Product.provider_service_ref_id == ids["spare_service_id"]
                    )
                )
            ).scalars().all()
        )
    assert len(published) == 1
    replacement = published[0]
    assert replacement.sub_category_id == ids["followers_id"]
    assert replacement.price_usd > JUNK_PRICE_THRESHOLD
    assert replacement.cost_price_usd == Decimal("0.80")
    assert replacement.status == ProductStatus.ACTIVE


async def test_clean_never_publishes_a_junk_service_as_replacement():
    """البديل لا يكون أبداً سطر «سيرفر» صفري آخر."""
    await _seed_smm_with_junk()
    async with async_session_maker() as session:
        await JunkProductsService.clean(session, replace=True)
        remaining = list((await session.execute(select(Product))).scalars().all())

    for product in remaining:
        assert not is_junk_product(product), product.name_ar


async def test_clean_is_idempotent():
    await _seed_smm_with_junk()
    async with async_session_maker() as session:
        first = await JunkProductsService.clean(session, replace=True)
    async with async_session_maker() as session:
        second = await JunkProductsService.clean(session, replace=True)

    assert first.deleted == 4
    assert second.deleted == 0  # لم يبقَ شيء وهمي


async def test_clean_tree_limits_to_one_app():
    """تنظيف تطبيق واحد لا يمسّ التطبيق الآخر."""
    ids = await _seed_smm_with_junk()
    async with async_session_maker() as session:
        report = await JunkProductsService.clean_tree(
            session, ids["insta_id"], replace=False
        )
        left = await JunkProductsService.find_all_smm(session)

    assert report.deleted == 2  # إنستغرام فقط
    assert {p.name_ar for p in left} == {
        "TikTok Likes [Server 5]",
        "لايكات تيك توك سيرفر 1",
    }


async def test_admin_counts_expose_junk_totals():
    ids = await _seed_smm_with_junk()
    async with async_session_maker() as session:
        total = await SmmAdminService.junk_count_all(session)
        insta = await SmmAdminService.junk_count(session, ids["insta_id"])
        followers = await SmmAdminService.junk_count(
            session, ids["followers_id"], include_children=False
        )
    assert total == 4
    assert insta == 2
    assert followers == 2


# ══════════ أزرار اللوحة ══════════


def _data(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_apps_kb_shows_global_cleanup_button_only_when_junk_exists():
    from keyboards.admin_smm_products import smm_apps_kb

    with_junk = smm_apps_kb(3, [], only_with_products=True, has_hidden=False, junk=7)
    clean = smm_apps_kb(3, [], only_with_products=True, has_hidden=False, junk=0)

    assert "smmp:junk_all" in _data(with_junk)
    assert "smmp:junk_all" not in _data(clean)
    texts = [b.text for row in with_junk.inline_keyboard for b in row]
    assert any("7" in text and "سيرفر" in text for text in texts)


def test_sections_and_products_kb_expose_junk_button():
    from keyboards.admin_smm_products import smm_products_kb, smm_sections_kb

    sections = smm_sections_kb(7, 3, [], direct_products=0, junk=2)
    assert "smmp:junk:7" in _data(sections)

    product = Product(
        id=55,
        sub_category_id=11,
        name_ar="متابعين سيرفر 1",
        price_usd=Decimal("0"),
        cost_price_usd=Decimal("0"),
        status=ProductStatus.ACTIVE,
    )
    products = smm_products_kb(
        11, [product], page=0, pages=1, parent_id=7, category_id=3, junk=1
    )
    assert "smmp:junk:11" in _data(products)

    clean = smm_products_kb(
        11, [product], page=0, pages=1, parent_id=7, category_id=3, junk=0
    )
    assert "smmp:junk:11" not in _data(clean)


def test_confirm_junk_kb_offers_replace_and_delete_only():
    from keyboards.admin_smm_products import confirm_clean_junk_kb

    scoped = _data(confirm_clean_junk_kb(11, is_app=False))
    assert "smmp:junk_del:11" in scoped
    assert "smmp:junk_del_only:11" in scoped
    assert "smmp:sec:11:0" in scoped

    global_kb = _data(confirm_clean_junk_kb(None))
    assert "smmp:junk_del:all" in global_kb
    assert "smmp:junk_del_only:all" in global_kb
    assert "smmp:home" in global_kb


# ══════════ مسار المعالجات ══════════


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
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.answers: list[tuple] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))


async def test_handler_global_cleanup_flow_deletes_and_replaces():
    from handlers.admin import smm_products as handler

    ids = await _seed_smm_with_junk()
    async with async_session_maker() as session:
        # شاشة التطبيقات تعرض الزر الشامل
        home = _FakeCallback("admin:smm_products")
        await handler.smm_products_home(home, session)
        assert "smmp:junk_all" in _data(home.message.markups[-1])
        assert "سيرفر" in home.message.texts[-1]

        # التأكيد
        ask = _FakeCallback("smmp:junk_all")
        await handler.smm_clean_junk_all_ask(ask, session)
        assert "smmp:junk_del:all" in _data(ask.message.markups[-1])

        # التنفيذ
        go = _FakeCallback("smmp:junk_del:all")
        await handler.smm_clean_junk_go(go, session)
        assert "تم تنظيف" in go.message.texts[0]

    async with async_session_maker() as session:
        left = await JunkProductsService.find_all_smm(session)
        good = await session.get(Product, ids["good_id"])
        priced = await session.get(Product, ids["priced_server_id"])

    assert left == []
    assert good is not None  # المنتج السليم لم يُمسّ
    assert priced is not None  # «سيرفر ذهبي» المسعّر لم يُمسّ


async def test_handler_scoped_cleanup_only_touches_that_section():
    from handlers.admin import smm_products as handler

    ids = await _seed_smm_with_junk()
    async with async_session_maker() as session:
        ask = _FakeCallback(f"smmp:junk:{ids['followers_id']}")
        await handler.smm_clean_junk_ask(ask, session)
        assert f"smmp:junk_del:{ids['followers_id']}" in _data(ask.message.markups[-1])

        go = _FakeCallback(f"smmp:junk_del_only:{ids['followers_id']}")
        await handler.smm_clean_junk_only(go, session)

    async with async_session_maker() as session:
        left = sorted(p.name_ar for p in await JunkProductsService.find_all_smm(session))

    assert set(left) == {"TikTok Likes [Server 5]", "لايكات تيك توك سيرفر 1"}


async def test_handler_answers_when_nothing_to_clean():
    from handlers.admin import smm_products as handler

    async with async_session_maker() as session:
        callback = _FakeCallback("smmp:junk_all")
        await handler.smm_clean_junk_all_ask(callback, session)

    assert callback.answers
    assert "لا توجد" in callback.answers[-1][0]


async def test_auto_builder_never_publishes_near_zero_server_lines():
    """البناء التلقائي يرفض «Server N» شبه الصفرية حتى لو كان سعرها > 0."""
    from services.smm_sections_service import is_sellable_service

    class _S:
        def __init__(self, name, rate):
            self.name = name
            self.name_ar = ""
            self.rate_usd = Decimal(str(rate))

    assert is_sellable_service(_S("Instagram Followers Real", "0.45")) is True
    assert is_sellable_service(_S("Instagram Followers Server 1", "0")) is False
    assert is_sellable_service(_S("Instagram Followers Server 2", "0.001")) is False

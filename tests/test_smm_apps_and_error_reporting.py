"""اختبار المهمتين: أسماء/إيموجي تطبيقات قسم الرشق، وتقرير الأخطاء.

1) ضبط تطبيقات الرشق العشرة + تصحيح الصفوف الموجودة في قواعد البيانات القديمة.
2) تقرير الخطأ يجب أن يُظهر أعمق الإطارات (سبب الخطأ) لا سلسلة الميدلوير.
"""

from __future__ import annotations

import traceback

import pytest
from sqlalchemy import select
from sqlalchemy.exc import MissingGreenlet

from database.engine import async_session_maker
from database.models import Category, CategoryType, SubCategory
from database.seed import SMM_APPS, init_db, match_smm_app
from middlewares.error_middleware import TRACEBACK_FRAME_LIMIT, _error_hint


async def _smm_subcategories() -> list[SubCategory]:
    async with async_session_maker() as session:
        cat = (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalars().first()
        result = await session.execute(
            select(SubCategory).where(SubCategory.category_id == cat.id)
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_seed_creates_the_ten_smm_apps_with_their_own_emoji():
    subs = await _smm_subcategories()
    by_name = {sub.name_ar: sub for sub in subs}

    assert len(by_name) == len(SMM_APPS) == 10
    for name, emoji, _aliases in SMM_APPS:
        assert name in by_name, f"التطبيق {name} ناقص من الزرع"
        assert by_name[name].emoji == emoji
        # عمود emoji طوله 8: أي إيموجي أطول سينكسر عند الحفظ.
        assert len(emoji) <= 8


@pytest.mark.asyncio
async def test_seed_repairs_legacy_rows_instead_of_duplicating_apps():
    """قاعدة مزروعة قديماً: كل الإيموجي 📈 وتهجئات قديمة."""
    async with async_session_maker() as session:
        cat = (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalars().first()
        # نمسح ما زرعه conftest ونعيد شكل قاعدة البيانات القديمة.
        for sub in (
            await session.execute(
                select(SubCategory).where(SubCategory.category_id == cat.id)
            )
        ).scalars().all():
            await session.delete(sub)
        await session.commit()

        legacy = [
            ("تيك توك", "📈"),
            ("انستغرام", "📈"),      # تهجئة قديمة
            ("يوتيوب", "📈"),
            ("تليجرام", "📈"),       # تهجئة قديمة
            ("فيسبوك", "📈"),
            ("واتس اب", "📈"),       # تهجئة قديمة
            ("سنابشات", "📈"),       # تهجئة قديمة
            ("بوت الأدمن الخاص", "🤖"),  # تطبيق مخصص: يجب ألا يُلمس
        ]
        for name, emoji in legacy:
            session.add(
                SubCategory(
                    category_id=cat.id, name_ar=name, emoji=emoji, is_active=True
                )
            )
        await session.commit()

    # الزرع التصحيحي يعمل عند كل إقلاع، لا في أول تشغيل فقط.
    await init_db()

    subs = await _smm_subcategories()
    by_name = {sub.name_ar: sub for sub in subs}

    # التطبيقات العشرة موجودة، وكل واحد منها مرة واحدة فقط (لا تكرار).
    canonical_names = [name for name, _e, _a in SMM_APPS]
    assert sorted(n for n in by_name if n in canonical_names) == sorted(canonical_names)
    for name, emoji, _aliases in SMM_APPS:
        assert by_name[name].emoji == emoji, f"إيموجي {name} لم يُصحَّح"

    # الإيموجي العام القديم اختفى من كل تطبيقات الرشق.
    assert all(sub.emoji != "📈" for sub in subs)

    # التطبيق المخصص الذي أضافه الأدمن بقي كما هو ولم يُدمج أو يُحذف.
    custom = [s for s in subs if s.name_ar == "بوت الأدمن الخاص"]
    assert len(custom) == 1, "التطبيق المخصص يجب أن يبقى صفاً مستقلاً بلا تعديل"
    assert custom[0].emoji == "🤖"


@pytest.mark.asyncio
async def test_seed_leaves_admin_custom_apps_untouched():
    async with async_session_maker() as session:
        cat = (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalars().first()
        session.add(
            SubCategory(
                category_id=cat.id,
                name_ar="بيجو لايف",
                emoji="🎥",
                description="وصف الأدمن",
                is_active=True,
            )
        )
        await session.commit()

    await init_db()

    subs = await _smm_subcategories()
    custom = next(s for s in subs if s.name_ar == "بيجو لايف")
    assert custom.emoji == "🎥"
    assert custom.description == "وصف الأدمن"


def test_match_smm_app_normalizes_legacy_spellings():
    for legacy, canonical in [
        ("انستغرام", "إنستغرام"),
        ("إنستقرام", "إنستغرام"),
        ("واتس اب", "واتساب"),
        ("سنابشات", "سناب شات"),
        ("تليجرام", "تيليجرام"),
        ("يوتيب", "يوتيوب"),
        ("تيكتوك", "تيك توك"),
        ("twitter", "إكس (تويتر)"),
    ]:
        hit = match_smm_app(legacy)
        assert hit is not None, legacy
        assert hit[1] == canonical

    # أي اسم خارج القائمة يُترك للأدمن.
    assert match_smm_app("ببجي موبايل") is None
    assert match_smm_app(None) is None
    assert match_smm_app("") is None


# ─────────────────── المهمة الثانية: تقرير الخطأ ───────────────────


def _the_real_cause() -> None:
    """مكان الخطأ الفعلي. اسمه مميز حتى يظهر الفرق بين الحد الموجب والسالب."""
    raise MissingGreenlet("greenlet_spawn has not been called")


def _deep_chain(depth: int) -> None:
    """يبني traceback أعمق من حد الإطارات."""
    if depth == 0:
        _the_real_cause()
    _deep_chain(depth - 1)


def test_error_report_keeps_the_deepest_frames_where_the_cause_is():
    try:
        # سلسلة أعمق من حد الإطارات حتى يظهر الفرق بين الحد الموجب والسالب.
        _deep_chain(TRACEBACK_FRAME_LIMIT + 15)
    except MissingGreenlet:
        # format_exc يقرأ sys.exc_info() الجاري، لذلك يجب استدعاؤه داخل except.
        shallow = traceback.format_exc(limit=TRACEBACK_FRAME_LIMIT)
        deep = traceback.format_exc(limit=-TRACEBACK_FRAME_LIMIT)
    else:  # pragma: no cover - السلسلة ترمي دائماً
        raise AssertionError("_deep_chain did not raise")

    # السبب الحقيقي في _the_real_cause، ولا يصل إليه إلا الحد السالب.
    assert "_the_real_cause" in deep
    assert "_the_real_cause" not in shallow

    # الإطار الأخير في التقرير هو الأقرب لمكان الخطأ.
    file_lines = [line for line in deep.splitlines() if line.strip().startswith("File ")]
    assert "_the_real_cause" in file_lines[-1]


def test_error_hint_explains_missing_greenlet():
    hint = _error_hint(MissingGreenlet("greenlet_spawn has not been called"))
    assert hint is not None
    assert "selectinload" in hint

    assert _error_hint(ValueError("عادي")) is None

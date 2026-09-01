"""
الدول وخدمات الأرقام تُدار بالكامل من لوحة الأدمن عبر قاعدة البيانات.
لا يوجد أي دولة أو خدمة مبرمجة مسبقاً بالكود.
"""

from sqlalchemy import select

from database.models import Country, NumberService


async def get_active_countries(session) -> list[Country]:
    """يجلب الدول المفعلة مرتبة حسب sort_order ثم الاسم."""
    result = await session.execute(
        select(Country)
        .where(Country.is_active.is_(True))
        .order_by(Country.sort_order, Country.name_ar)
    )
    return list(result.scalars().all())


async def get_country_by_code(session, code: str) -> Country | None:
    result = await session.execute(select(Country).where(Country.code == code))
    return result.scalar_one_or_none()


async def get_all_countries(session) -> list[Country]:
    """لعرضها بلوحة الأدمن (نشطة وغير نشطة معاً)."""
    result = await session.execute(select(Country).order_by(Country.sort_order, Country.name_ar))
    return list(result.scalars().all())


async def get_active_number_services(
    session,
) -> list[NumberService]:
    """يجلب خدمات الأرقام المفعلة (واتساب، تيليجرام، إلخ)."""
    result = await session.execute(
        select(NumberService)
        .where(NumberService.is_active.is_(True))
        .order_by(NumberService.sort_order, NumberService.id)
    )
    return list(result.scalars().all())


async def get_number_service_by_code(session, code: str) -> NumberService | None:
    result = await session.execute(select(NumberService).where(NumberService.code == code))
    return result.scalar_one_or_none()


async def get_all_number_services(
    session,
) -> list[NumberService]:
    """لعرضها بلوحة الأدمن."""
    result = await session.execute(
        select(NumberService).order_by(NumberService.sort_order, NumberService.id)
    )
    return list(result.scalars().all())

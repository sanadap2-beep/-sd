"""ضبط display_type=PER_UNIT لكل منتجات Hyper Store (السعر بالوحدة الواحدة)."""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("BOT_USERNAME", "x")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ADMIN_NOTIFY_CHAT_ID", "-1")
os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./bot_database.db"))
os.environ.setdefault("INVENTORY_ENCRYPTION_KEY", "dev")
from sqlalchemy import select, update
from database.seed import init_db
from database.engine import async_session_maker
from database.models import ApiProvider, Product, ProductDisplayType

async def main():
    await init_db()
    async with async_session_maker() as session:
        p = (await session.execute(select(ApiProvider).where(ApiProvider.name == "HyperStore"))).scalar_one_or_none()
        if not p:
            print("❌ لا يوجد مزود HyperStore")
            return
        result = await session.execute(
            update(Product).where(Product.api_provider_id == p.id).values(display_type=ProductDisplayType.PER_UNIT)
        )
        await session.commit()
        print(f"✅ تم تحديث {result.rowcount} منتج: display_type = per_unit")

if __name__ == "__main__":
    asyncio.run(main())

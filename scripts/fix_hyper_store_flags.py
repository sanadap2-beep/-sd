"""إعادة حساب flags الحقول المطلوبة لكل منتج من بيانات الخدمة الأم (raw_data)."""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("BOT_TOKEN", "123456:DEV")
os.environ.setdefault("BOT_USERNAME", "hyperstore_bot")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ADMIN_NOTIFY_CHAT_ID", "-1001")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./bot_database.db"),
)
os.environ.setdefault("INVENTORY_ENCRYPTION_KEY", "dev-encryption-key-not-for-prod")

import json as _json

from sqlalchemy import select

from database.seed import init_db  # noqa: E402
from database.engine import async_session_maker  # noqa: E402
from database.models import ApiProvider, Product, ProviderService  # noqa: E402
from services.provider_sync_service import ProviderSyncService  # noqa: E402


def _target_kind(raw_data: str | None) -> tuple[bool, bool]:
    """يرجع (needs_player_id, needs_link) لكل خدمة من حقلها الخام."""
    try:
        raw = _json.loads(raw_data or "{}")
    except Exception:
        return False, False
    fields = raw.get("fields") if isinstance(raw.get("fields"), list) else []
    if not fields:
        try:
            fields = _json.loads(str(raw.get("input_type") or "[]"))
        except Exception:
            fields = []
    target = None
    for f in fields:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").lower()
        if key in ("quantity", "qty") or not f.get("required", True):
            continue
        target = f
        break
    if not target:
        return False, False
    key = str(target.get("key") or "").lower()
    link_hints = ("link", "url", "address", "channel", "post")
    if any(h in key for h in link_hints):
        return False, True
    return True, False


async def main() -> None:
    await init_db()
    from sqlalchemy.orm import selectinload

    async with async_session_maker() as session:
        provider = (
            await session.execute(
                select(ApiProvider).where(ApiProvider.name == "HyperStore")
            )
        ).scalar_one_or_none()
        if provider is None:
            print("❌ مزود HyperStore غير موجود — نفّذ scripts/hyper_store_setup.py أولاً.")
            return

        print("⏳ تحديث خدمات المزود (إعادة سحب وتحديث flags)...")
        result = await ProviderSyncService.sync_provider_services(provider.id)
        print(result.summary())

        products = (
            await session.execute(
                select(Product)
                .where(Product.api_provider_id == provider.id)
                .options(selectinload(Product.provider_service))
            )
        ).scalars().all()
        fixed = 0
        for product in products:
            svc = product.provider_service
            if svc is None:
                continue
            needs_player, needs_link = _target_kind(svc.raw_data)
            changed = False
            if product.requires_player_id != needs_player:
                product.requires_player_id = needs_player
                changed = True
            if product.requires_link != needs_link:
                product.requires_link = needs_link
                changed = True
            if product.requires_quantity != svc.requires_quantity:
                product.requires_quantity = svc.requires_quantity
                changed = True
            if changed:
                fixed += 1
        await session.commit()
        print(f"🔧 تم تصحيح flags لـ {fixed} منتج")
        print(f"   - منتجات تتطلب Player ID/ملاحظة: {sum(1 for p in products if p.requires_player_id)}")
        print(f"   - منتجات تتطلب رابطاً: {sum(1 for p in products if p.requires_link)}")
        print(f"   - مجموع: {len(products)}")


if __name__ == "__main__":
    asyncio.run(main())

"""خدمة السيرفرات/المزودين الديناميكية لخدمات الأرقام.

المفهوم:
- كل ``NumberService`` يمكن أن يحوي أكثر من سيرفر (مزود).
- كل سيرفر مربوط بمزود معين (5sim / HeroSMS / SMS-Activate / SMSHub ...).
- تُستخدم من واجهة المستخدم ليعرض: اختر السيرفر → اختر الدولة → شراء.
- تُستخدم من لوحة الأدمن لإدارة السيرفرات بلا تعديل كود.

القيود (حتى لا يختلط مع المزودين:
- ``provider`` قيمة من ProviderName. إذا غيّر الأدمن وقمت بتعطيله،
  تُتجاهل شاشة السيرفر وتعود للمسار القديم (أرخص مزود تلقائياً).
"""

from __future__ import annotations

from sqlalchemy import select

from database.models import NumberServer, NumberService
from database.models import ProviderName


PROVIDER_LABELS: dict[str, tuple[str, str]] = {
    ProviderName.FIVESIM.value: ("🟢", "5sim"),
    ProviderName.HEROSMS.value: ("🟠", "HeroSMS"),
    ProviderName.SMS_ACTIVATE.value: ("🔵", "SMS-Activate"),
    ProviderName.SMSHUB.value: ("🟣", "SMSHub"),
}


async def server_label(provider_value: str) -> tuple[str, str]:
    """``(emoji, label)`` لمزود معين."""
    emoji, label = PROVIDER_LABELS.get(provider_value, ("🔌", provider_value))
    return emoji, label


class NumberServerService:
    @staticmethod
    async def list_servers(
        session,
        service_id: int,
        active_only: bool = True,
    ) -> list[NumberServer]:
        query = select(NumberServer).where(NumberServer.number_service_id == service_id)
        if active_only:
            query = query.where(NumberServer.is_active.is_(True))
        result = await session.execute(query.order_by(NumberServer.sort_order, NumberServer.id))
        return list(result.scalars().all())

    @staticmethod
    async def get(session, server_id: int) -> NumberServer | None:
        return await session.get(NumberServer, server_id)

    @staticmethod
    async def create(
        session,
        number_service_id: int,
        name_ar: str,
        provider: str,
        emoji: str = "🖥",
        description: str | None = None,
        margin_percent=None,
        sort_order: int = 0,
    ) -> NumberServer:
        server = NumberServer(
            number_service_id=number_service_id,
            name_ar=name_ar,
            provider=provider,
            emoji=emoji or "🖥",
            description=description,
            margin_percent=margin_percent,
            is_active=True,
            sort_order=sort_order,
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        return server

    @staticmethod
    async def update(session, server_id: int, **kwargs) -> NumberServer | None:
        server = await session.get(NumberServer, server_id)
        if server is None:
            return None
        for key, value in kwargs.items():
            if hasattr(server, key):
                setattr(server, key, value)
        await session.commit()
        await session.refresh(server)
        return server

    @staticmethod
    async def delete(session, server_id: int) -> bool:
        server = await session.get(NumberServer, server_id)
        if server is None:
            return False
        await session.delete(server)
        await session.commit()
        return True

    @staticmethod
    async def ensure_defaults(session, service: NumberService) -> list[NumberServer]:
        """إن لم توجد سيرفرات للخدمة، ينشئ سيرفراً لكل مزود فيه كود مضبوط.

        هذا يجعل الميزة تعمل فوراً على الخدمات القديمة (واتساب، تليجرام...)
        ويمكن للأدمن لاحقاً تعديل الأسماء/الأيموجي/الترتيب أو تعطيل أي سيرفر.
        """
        existing = await NumberServerService.list_servers(session, service.id, active_only=False)
        if existing:
            return existing

        provider_codes = (
            (ProviderName.FIVESIM.value, service.fivesim_code),
            (ProviderName.HEROSMS.value, service.herosms_code),
            (ProviderName.SMS_ACTIVATE.value, service.sms_activate_code),
            (ProviderName.SMSHUB.value, service.smshub_code),
        )
        created: list[NumberServer] = []
        index = 1
        for provider_value, code in provider_codes:
            if not code:
                continue
            emoji, label = await server_label(provider_value)
            server = NumberServer(
                number_service_id=service.id,
                provider=provider_value,
                name_ar=label,
                emoji=emoji,
                is_active=True,
                sort_order=index * 10,
            )
            session.add(server)
            created.append(server)
            index += 1
        if created:
            await session.commit()
            for server in created:
                await session.refresh(server)
        return created

    @staticmethod
    def validate_provider(raw: str) -> str | None:
        """يضمن أن المزود من قائمة مزودي الأرقام المعروفة (وإلا None)."""
        try:
            return ProviderName(raw).value
        except ValueError:
            return None

    @staticmethod
    async def active_providers(session) -> list[ProviderName]:
        """كل مزود عليه API Key فعلياً (يُقرأ من ProviderManager)."""
        from providers.manager import provider_manager

        return provider_manager.get_available_providers()

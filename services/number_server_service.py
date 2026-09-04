"""خدمة السيرفرات/المزودين الديناميكية لخدمات الأرقام.

المفهوم:
- كل ``NumberService`` يمكن أن يحوي أكثر من سيرفر (مزود).
- كل سيرفر مربوط بمزود معين (5sim / HeroSMS / SMS-Activate / SMSHub ...).
- تُستخدم من واجهة المستخدم ليعرض: اختر السيرفر → اختر الدولة → شراء.
- تُستخدم من لوحة الأدمن لإدارة السيرفرات بلا تعديل كود.

القيود (حتى لا يختلط مع المزودين:
- ``provider`` قيمة من ProviderName. إذا غيّر الأدمن وقمت بتعطيله،
  تُتجاهل شاشة السيرفر وتعود للمسار القديم (أرخص مزود تلقائياً).

🔒 خصوصية المزود (مهم):
- المستخدم **لا يرى أبداً** اسم المزود (5sim / HeroSMS ...). كل سيرفر يُعرض
  باسم محايد مرقّم: «سيرفر 1»، «سيرفر 2»، «سيرفر 3»... حسب ترتيبه داخل
  خدمته. الأدمن وحده يرى المزود الحقيقي في لوحته.
- 🟢 النقطة الخضراء أمام السيرفر تعني «هذا السيرفر يعمل الآن»، يضبطها
  الأدمن يدوياً من لوحة الأدمن (``is_working``).
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

# الاسم المحايد الذي يراه المستخدم بدل اسم المزود.
PUBLIC_SERVER_PREFIX = "سيرفر"
# إيموجي موحّد لكل السيرفرات في واجهة المستخدم — إيموجي المزود (🟠 لـ HeroSMS
# مثلاً) كان يفضح المزود بشكل غير مباشر، فوحّدناه.
PUBLIC_SERVER_EMOJI = "🖥"
# النقطة الخضراء أمام السيرفر الذي علّمه الأدمن كـ«شغّال».
WORKING_MARK = "🟢"


async def server_label(provider_value: str) -> tuple[str, str]:
    """``(emoji, label)`` لمزود معين — **للوحة الأدمن فقط**.

    لا تُستخدم في واجهة المستخدم إطلاقاً؛ استخدم ``public_server_name``.
    """
    emoji, label = PROVIDER_LABELS.get(provider_value, ("🔌", provider_value))
    return emoji, label


def public_server_name(position: int) -> str:
    """الاسم المحايد الذي يراه المستخدم: «سيرفر 1»، «سيرفر 2»...

    ``position`` رقم ترتيبي يبدأ من 1 داخل الخدمة الواحدة.
    """
    return f"{PUBLIC_SERVER_PREFIX} {position}"


def public_server_label(position: int, is_working: bool = False) -> str:
    """نص السيرفر كما يراه المستخدم، مع 🟢 إن كان معلَّماً كشغّال.

    مثال: ``🟢 🖥 سيرفر 2`` أو ``🖥 سيرفر 1``.
    """
    name = f"{PUBLIC_SERVER_EMOJI} {public_server_name(position)}"
    return f"{WORKING_MARK} {name}" if is_working else name


def public_labels_for(servers: list[NumberServer]) -> dict[int, str]:
    """``{server_id: "سيرفر N"}`` — الترقيم حسب ترتيب السيرفرات المُمرَّرة.

    يُبنى الترقيم من ترتيب القائمة (sort_order ثم id) فيبقى ثابتاً للمستخدم
    ما لم يعد الأدمن ترتيبها.
    """
    return {
        server.id: public_server_name(index)
        for index, server in enumerate(servers, start=1)
    }


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
    async def position_of(session, server: NumberServer) -> int:
        """ترتيب السيرفر داخل خدمته (1 = «سيرفر 1») بين السيرفرات المفعّلة.

        السيرفر المعطّل لا يراه المستخدم، لذا الترقيم المعروض يُحسب على
        المفعّلة فقط. وإن كان السيرفر نفسه معطّلاً نُرجع ترتيبه بين الكل.
        """
        active = await NumberServerService.list_servers(
            session, server.number_service_id, active_only=True
        )
        for index, row in enumerate(active, start=1):
            if row.id == server.id:
                return index
        every = await NumberServerService.list_servers(
            session, server.number_service_id, active_only=False
        )
        for index, row in enumerate(every, start=1):
            if row.id == server.id:
                return index
        return 1

    @staticmethod
    async def public_label(session, server: NumberServer) -> str:
        """نص السيرفر للمستخدم: «🟢 🖥 سيرفر 2» — بلا أي ذكر للمزود."""
        position = await NumberServerService.position_of(session, server)
        return public_server_label(position, bool(getattr(server, "is_working", False)))

    @staticmethod
    async def public_name(session, server: NumberServer) -> str:
        """«سيرفر 2» بلا إيموجي ولا نقطة — للأسطر النصية."""
        position = await NumberServerService.position_of(session, server)
        return public_server_name(position)

    @staticmethod
    async def set_working(
        session, server_id: int, working: bool, *, exclusive: bool = False
    ) -> NumberServer | None:
        """يضبط/يزيل النقطة الخضراء 🟢 عن سيرفر.

        ``exclusive=True`` يزيلها عن باقي سيرفرات نفس الخدمة (سيرفر شغّال
        واحد فقط). الافتراضي يسمح بتعليم أكثر من سيرفر معاً.
        """
        server = await session.get(NumberServer, server_id)
        if server is None:
            return None
        if working and exclusive:
            siblings = await NumberServerService.list_servers(
                session, server.number_service_id, active_only=False
            )
            for row in siblings:
                if row.id != server.id:
                    row.is_working = False
        server.is_working = bool(working)
        await session.commit()
        await session.refresh(server)
        return server

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

        الاسم المحفوظ محايد («سيرفر 1»، «سيرفر 2»...) ولا يذكر المزود، لأن
        المستخدم يرى هذا الاسم مباشرة.
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
            server = NumberServer(
                number_service_id=service.id,
                provider=provider_value,
                name_ar=public_server_name(index),
                emoji=PUBLIC_SERVER_EMOJI,
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
    async def renumber(session, service_id: int) -> int:
        """يعيد تسمية كل سيرفرات الخدمة إلى «سيرفر 1، 2، 3...» بالترتيب.

        يُستخدم لتنظيف القواعد القديمة التي حُفظت فيها أسماء المزودين
        (5sim / HeroSMS ...) فكانت تظهر للمستخدم. يرجع عدد الأسماء المغيّرة.
        """
        servers = await NumberServerService.list_servers(
            session, service_id, active_only=False
        )
        changed = 0
        for index, server in enumerate(servers, start=1):
            wanted = public_server_name(index)
            if server.name_ar != wanted:
                server.name_ar = wanted
                changed += 1
            if server.emoji != PUBLIC_SERVER_EMOJI:
                server.emoji = PUBLIC_SERVER_EMOJI
                changed += 1
        if changed:
            await session.commit()
        return changed

    @staticmethod
    async def renumber_all(session) -> int:
        """يعيد ترقيم سيرفرات كل خدمات الأرقام. يرجع عدد الخدمات المتأثرة."""
        service_ids = list(
            (
                await session.execute(
                    select(NumberServer.number_service_id).distinct()
                )
            ).scalars().all()
        )
        touched = 0
        for service_id in service_ids:
            if await NumberServerService.renumber(session, int(service_id)):
                touched += 1
        return touched

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

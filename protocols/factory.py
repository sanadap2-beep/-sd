"""
مصنع البروتوكولات (Protocol Factory).

يستخدم Factory Pattern لإنشاء instance من البروتوكول المناسب
حسب نوع المزود المطلوب.

مثال:
    from protocols.factory import ProtocolFactory
    from database.models import ApiProtocolType

    protocol = ProtocolFactory.create(
        protocol_type=ApiProtocolType.SMM_V2,
        api_url="https://jumbosmm.com/api/v2",
        api_key="xxx",
    )
    balance = await protocol.get_balance()
"""

import logging

from database.models import ApiProtocolType, ApiProvider
from protocols.base import BaseProtocol, ProtocolError
from protocols.generic_json import (
    CustomJsonProtocol,
    GamesGenericProtocol,
)
from protocols.smm_v2 import SmmV2Protocol

logger = logging.getLogger(__name__)


class ProtocolFactory:
    """
    مصنع البروتوكولات.
    ينشئ instance من البروتوكول المناسب حسب النوع.
    """

    _protocols: dict[ApiProtocolType, type[BaseProtocol]] = {
        ApiProtocolType.SMM_V2: SmmV2Protocol,
        ApiProtocolType.GAMES_GENERIC: GamesGenericProtocol,
        ApiProtocolType.CUSTOM: CustomJsonProtocol,
    }

    @classmethod
    def register(
        cls,
        protocol_type: ApiProtocolType,
        protocol_class: type[BaseProtocol],
    ) -> None:
        """
        يسجل بروتوكول جديد في المصنع.
        يُستخدم لإضافة بروتوكولات مستقبلية بسهولة.
        """
        cls._protocols[protocol_type] = protocol_class
        logger.info(f"تم تسجيل بروتوكول: {protocol_type.value} -> {protocol_class.__name__}")

    @classmethod
    def create(
        cls,
        protocol_type: ApiProtocolType,
        api_url: str,
        api_key: str,
        custom_config: dict | None = None,
    ) -> BaseProtocol:
        """
        ينشئ instance من البروتوكول المطلوب.

        Raises:
            ProtocolError: إذا كان النوع غير مدعوم.
        """
        protocol_class = cls._protocols.get(protocol_type)
        if not protocol_class:
            available = ", ".join(p.value for p in cls._protocols.keys())
            raise ProtocolError(
                f"البروتوكول '{protocol_type.value}' غير مدعوم. المتاح: {available}"
            )

        return protocol_class(
            api_url=api_url,
            api_key=api_key,
            custom_config=custom_config,
        )

    @classmethod
    def create_from_provider(cls, provider: ApiProvider) -> BaseProtocol:
        """
        ينشئ instance من مزود محفوظ في قاعدة البيانات.
        طريقة مختصرة تُستخدم كثيراً.
        """
        custom_config = None
        if provider.custom_config:
            try:
                import json

                custom_config = json.loads(provider.custom_config)
            except Exception as e:
                logger.warning(f"فشل تحليل custom_config للمزود {provider.id}: {e}")

        return cls.create(
            protocol_type=provider.protocol_type,
            api_url=provider.api_url,
            api_key=provider.api_key,
            custom_config=custom_config,
        )

    @classmethod
    def get_supported_protocols(
        cls,
    ) -> list[ApiProtocolType]:
        """يرجع قائمة البروتوكولات المدعومة حالياً."""
        return list(cls._protocols.keys())

    @classmethod
    def is_supported(cls, protocol_type: ApiProtocolType) -> bool:
        """يتحقق إذا كان البروتوكول مدعوماً."""
        return protocol_type in cls._protocols

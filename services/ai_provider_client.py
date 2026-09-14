"""
عميل مزود NanoGPT (API متوافق مع OpenAI) لأقسام الذكاء الاصطناعي.

كل قسم (برمجة/دردشة/مستقبلي) يمر عبر هذا العميل، والفرق الوحيد بينهم
اسم النموذج (model) المحفوظ في القسم.

مصدر المفتاح (الأولوية):
1) لوحة الأدمن: ai_provider_api_key (settings).
2) ملف البيئة: NANOGPT_API_KEY.

مصدر العنوان (الأولوية):
1) لوحة الأدمن: ai_provider_base_url.
2) ملف البيئة: NANOGPT_BASE_URL.
3) عنوان NanoGPT الافتراضي.

لا يُخصم رصيد المستخدم إلا عند نجاح الاستدعاء؛ عند أي فشل يرفع
AiProviderError فيرجع الأدمن/الخدمة المبلغ للمستخدم.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

from config import settings
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://nano-gpt.com/api/v1"


class AiProviderError(Exception):
    """فشل الاتصال بالمزود أو رداً غير صالح — لا يُخصم رصيد المستخدم."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class AiCompletion:
    text: str
    usage: dict | None = None
    model: str | None = None


async def get_provider_config() -> tuple[str, str]:
    """يعيد (base_url, api_key) مع تفضيل قيم لوحة الأدمن."""
    base_url = (
        (await SettingsService.get("ai_provider_base_url") or "").strip()
        or settings.NANOGPT_BASE_URL
        or DEFAULT_BASE_URL
    )
    api_key = (
        (await SettingsService.get("ai_provider_api_key") or "").strip()
        or settings.NANOGPT_API_KEY
    )
    return base_url.rstrip("/"), api_key


async def configured() -> bool:
    """هل يوجد مفتاح مزود صالح؟ (لإظهار تنبيه للوحة الأدمن)."""
    _base, key = await get_provider_config()
    return bool(key)


async def chat_completion(
    messages: list[dict],
    model: str,
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout_seconds: int = 120,
) -> AiCompletion:
    """
    استدعاء chat/completions. يرفع AiProviderError عند أي فشل
    (لا مفتاح / شبكة / كود خطأ / رد فارغ) حتى يقرر المستدعي
    الاسترجاع وإظهار الخطأ.
    """
    base_url, api_key = await get_provider_config()
    if not api_key:
        raise AiProviderError(
            "لم يُضبط مفتاح المزود بعد. ضع NANOGPT_API_KEY في ملف البيئة "
            "أو عدّله من لوحة الأدمن (إدارة الذكاء الاصطناعي ← 🔌 المزود)."
        )

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as http:
            async with http.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    body = (await response.text())[:300]
                    logger.warning("مزود AI أرجع %s: %s", response.status, body)
                    raise AiProviderError(
                        f"مزود الذكاء الاصطناعي أرجع كود {response.status}.",
                        response.status,
                    )
                data = await response.json()
    except AiProviderError:
        raise
    except asyncio.TimeoutError as exc:
        raise AiProviderError("انتهت مهلة انتظار الرد من المزود.") from exc
    except (aiohttp.ClientError, ValueError) as exc:
        raise AiProviderError(f"تعذّر الاتصال بمزود الذكاء الاصطناعي: {exc}") from exc

    text: str | None = None
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        pass
    if not text or not str(text).strip():
        raise AiProviderError("الرد من المزود فارغ.")

    return AiCompletion(
        text=str(text).strip(),
        usage=data.get("usage"),
        model=data.get("model") or model,
    )

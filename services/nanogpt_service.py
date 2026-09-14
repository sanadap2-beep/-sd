"""
عميل NanoGPT — المزود الموحد لأقسام الذكاء الاصطناعي.

NanoGPT يوفّر واجهة متوافقة مع OpenAI على:
    POST {base_url}/chat/completions      (Bearer API key)
والاستجابة تتضمن التكلفة الفعلية بالدولار لكل طلب (حقل cost داخل usage
أو أعلى الاستجابة)، لذلك نحسب ربح الأدمن من تكلفة حقيقية وليس تقدير أعمى.

الإعدادات كلها من جدول settings (لوحة الأدمن):
    nanogpt_api_key     — مفتاح API
    nanogpt_base_url    — افتراضياً https://nano-gpt.com/api/v1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import aiohttp

from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://nano-gpt.com/api/v1"

SETTING_API_KEY = "nanogpt_api_key"
SETTING_BASE_URL = "nanogpt_base_url"


class NanoGPTError(Exception):
    """خطأ في استدعاء NanoGPT — الرسالة صالحة للعرض على المستخدم."""


@dataclass
class CompletionResult:
    text: str
    provider_cost: Decimal | None = None      # التكلفة الفعلية بالدولار إن وُجدت
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    raw: dict = field(default_factory=dict, repr=False)


async def configured() -> bool:
    """هل المفتاح مضبوط؟ الأقسام لا تُعرض للمستخدمين قبل ذلك."""
    key = await SettingsService.get(SETTING_API_KEY, "")
    return bool(key and key.strip())


async def get_base_url() -> str:
    url = await SettingsService.get(SETTING_BASE_URL, "") or DEFAULT_BASE_URL
    return url.rstrip("/")


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _extract_cost(data: dict) -> Decimal | None:
    """التكلفة الفعلية: usage.cost ← cost ← usage.nanoCost ← nanoCost."""
    usage = data.get("usage") or {}
    for source in (usage, data):
        for field_name in ("cost", "nanoCost", "nano_cost", "total_cost"):
            if field_name in source:
                cost = _to_decimal(source.get(field_name))
                if cost is not None:
                    return cost
    return None


def _extract_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class NanoGPTService:
    """استدعاء موحّد لكل أقسام الذكاء الاصطناعي."""

    @staticmethod
    async def complete(
        model: str,
        messages: list[dict],
        max_tokens: int = 4000,
        temperature: float = 0.7,
        timeout_seconds: int = 180,
    ) -> CompletionResult:
        """
        يرسل المحادثة كاملة ويعيد النص + التكلفة الفعلية.

        يرفع NanoGPTError عند فشل الاتصال أو رد غير صالح، والمستدعي
        يترجم الخطأ لرسالة مناسبة للمستخدم (بدون خصم).
        """
        api_key = ((await SettingsService.get(SETTING_API_KEY, "")) or "").strip()
        if not api_key:
            raise NanoGPTError("مفتاح NanoGPT غير مضبوط. راجع الإدارة.")

        base_url = await get_base_url()
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.post(
                    f"{base_url}/chat/completions", json=payload, headers=headers
                ) as response:
                    if response.status != 200:
                        body = (await response.text())[:300]
                        logger.warning("NanoGPT رجع %s: %s", response.status, body)
                        if response.status == 401:
                            raise NanoGPTError("مفتاح NanoGPT غير صالح (401).")
                        if response.status == 402:
                            raise NanoGPTError("رصيد حساب NanoGPT غير كافٍ (402).")
                        if response.status == 429:
                            raise NanoGPTError("ضغط على المزود، جرّب بعد لحظات (429).")
                        raise NanoGPTError(f"المزود رجع خطأ ({response.status}).")
                    data = await response.json(content_type=None)
        except NanoGPTError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("فشل استدعاء NanoGPT: %s", exc)
            raise NanoGPTError("تعذّر الاتصال بمزود الذكاء الاصطناعي. جرّب مجدداً.") from exc

        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise NanoGPTError("رد غير متوقع من المزود.") from exc
        if not text.strip():
            raise NanoGPTError("الموديل رجع رداً فارغاً. جرّب صياغة طلبك بشكل آخر.")

        usage = data.get("usage") or {}
        return CompletionResult(
            text=text,
            provider_cost=_extract_cost(data),
            input_tokens=_extract_int(usage.get("prompt_tokens")),
            output_tokens=_extract_int(usage.get("completion_tokens")),
            model=str(data.get("model") or model),
            raw=data,
        )

    @staticmethod
    async def test_key() -> tuple[bool, str]:
        """اختبار سريع من لوحة الأدمن: طلب صغير جداً للتحقق من المفتاح."""
        try:
            result = await NanoGPTService.complete(
                model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": "قل: جاهز"}],
                max_tokens=10,
                temperature=0,
                timeout_seconds=30,
            )
        except NanoGPTError as exc:
            return False, str(exc)
        cost = f"${result.provider_cost}" if result.provider_cost is not None else "بدون معلومة"
        return True, f"المفتاح يعمل ✅ — رد الموديل: {result.text[:60]} | تكلفة الطلب: {cost}"

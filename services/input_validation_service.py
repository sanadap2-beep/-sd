"""Single validation layer for all untrusted user/provider inputs."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse


class InputValidationError(ValueError):
    pass


class InputValidationService:
    @staticmethod
    def message_text(message) -> str:
        """نص رسالة آمن: "" إن كانت صورة/ستيكر/بلا نص (يمنع None.strip())."""
        text = getattr(message, "text", None)
        if not isinstance(text, str):
            return ""
        return text.strip()

    @staticmethod
    def text(value: str | None, *, min_length: int = 1, max_length: int = 2000) -> str:
        normalized = (value or "").strip()
        if not min_length <= len(normalized) <= max_length:
            raise InputValidationError("النص بطول غير صالح.")
        return normalized

    @staticmethod
    def money(
        value: str | Decimal | int | float | None,
        *,
        minimum: Decimal = Decimal("0"),
        maximum: Decimal | None = None,
        allow_zero: bool = False,
    ) -> Decimal:
        try:
            amount = Decimal(str(value).strip())
        except (AttributeError, InvalidOperation, ValueError):
            raise InputValidationError("القيمة المالية غير صالحة.") from None
        if not amount.is_finite():
            raise InputValidationError("القيمة المالية غير صالحة.")
        if amount == 0 and allow_zero:
            return amount
        if amount < minimum or (amount == 0 and minimum <= 0):
            raise InputValidationError("القيمة أقل من الحد المسموح.")
        if maximum is not None and amount > maximum:
            raise InputValidationError("القيمة تتجاوز الحد الأقصى.")
        return amount

    @staticmethod
    def positive_money(value) -> Decimal:
        return InputValidationService.money(value, minimum=Decimal("0"))

    @staticmethod
    def integer(
        value: str | int | None, *, minimum: int | None = None, maximum: int | None = None
    ) -> int:
        try:
            parsed = int(str(value).strip())
        except (AttributeError, TypeError, ValueError):
            raise InputValidationError("الرقم غير صالح.") from None
        if minimum is not None and parsed < minimum:
            raise InputValidationError("الرقم أصغر من الحد المسموح.")
        if maximum is not None and parsed > maximum:
            raise InputValidationError("الرقم أكبر من الحد المسموح.")
        return parsed

    @staticmethod
    def telegram_id(value: str | int | None) -> int:
        return InputValidationService.integer(value, minimum=1)

    @staticmethod
    def url(value: str | None, *, allow_private: bool = False, allowed_schemes=("https",)) -> str:
        candidate = InputValidationService.text(value, min_length=8, max_length=500)
        parsed = urlparse(candidate)
        if parsed.scheme not in allowed_schemes or not parsed.netloc:
            raise InputValidationError("الرابط غير صالح (يُسمح بـ HTTPS فقط).")
        if not allow_private:
            try:
                from services.ssrf_guard import validate_url

                return validate_url(candidate, allow_http=("http" in allowed_schemes))
            except Exception as exc:
                raise InputValidationError(f"الرابط مرفوض أمنياً: {exc}") from exc
        return candidate

    @staticmethod
    def code(value: str | None, *, max_length: int = 255) -> str:
        candidate = InputValidationService.text(value, min_length=2, max_length=max_length)
        if not re.fullmatch(r"[A-Za-z0-9_:.\-]+", candidate):
            raise InputValidationError("الكود يحتوي رموزاً غير مسموحة.")
        return candidate

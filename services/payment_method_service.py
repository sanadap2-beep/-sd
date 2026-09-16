"""Shared availability checks for payment methods.

A payment method has two independent gates:

* the administrator's database flag, and
* the minimum runtime configuration required to safely receive money.

Keeping this check in one service prevents the user menu, replay protection,
and the administrator diagnostics page from disagreeing about why a method is
hidden. Secrets are never returned in diagnostic messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import settings
from services.settings_service import SettingsService


PAYMENT_METHOD_SETTINGS = {
    "shamcash_manual": "payment_shamcash_manual_enabled",
    "stars": "payment_stars_enabled",
    "usdt_manual": "payment_usdt_manual_enabled",
    "shamcash_auto": "payment_shamcash_auto_enabled",
    "usdt_auto": "payment_usdt_auto_enabled",
    "mobile_credit": "payment_mobile_credit_enabled",
    "other": "payment_other_enabled",
}

SETTING_TO_PAYMENT_METHOD = {
    setting_key: method
    for method, setting_key in PAYMENT_METHOD_SETTINGS.items()
}


@dataclass(frozen=True)
class PaymentMethodDiagnostic:
    """Safe, non-secret explanation of a payment method's effective state."""

    method: str
    setting_key: str | None
    flag_enabled: bool
    configured: bool
    enabled: bool
    reason: str


def _non_empty(*values: object) -> bool:
    """Return whether at least one value is a non-blank string."""
    return any(str(value or "").strip() for value in values)


async def _configuration_status(method: str) -> tuple[bool, str]:
    """Check provider configuration without ever returning a secret value."""
    if method == "shamcash_manual":
        address = await SettingsService.get("shamcash_manual_address", "")

        if _non_empty(address, settings.SHAMCASH_MANUAL_ADDRESS):
            return True, "عنوان المحفظة مضبوط"

        return False, "عنوان محفظة شام كاش اليدوي غير مضبوط"

    if method == "stars":
        return True, "لا يحتاج إعداداً خارجياً"

    if method == "usdt_manual":
        addresses = (
            await SettingsService.get("usdt_trc20_address", ""),
            await SettingsService.get("usdt_erc20_address", ""),
            await SettingsService.get("usdt_bep20_address", ""),
            settings.USDT_TRC20_ADDRESS,
            settings.USDT_ERC20_ADDRESS,
            settings.USDT_BEP20_ADDRESS,
        )

        if _non_empty(*addresses):
            return True, "عنوان محفظة USDT مضبوط"

        return False, "لا يوجد عنوان محفظة USDT مضبوط"

    if method == "shamcash_auto":
        missing = []

        if not str(settings.SAM_API_KEY or "").strip():
            missing.append("SAM_API_KEY")

        if not str(settings.SAM_API_WALLET_ADDRESS or "").strip():
            missing.append("SAM_API_WALLET_ADDRESS")

        if missing:
            return False, "الإعدادات الناقصة: " + " و ".join(missing)

        return True, "بيانات Sam API وعنوان المحفظة مضبوطتان"

    if method == "usdt_auto":
        if str(settings.PLISIO_SECRET_KEY or "").strip():
            return True, "مفتاح Plisio مضبوط"

        return False, "مفتاح PLISIO_SECRET_KEY غير مضبوط"

    if method == "other":
        return True, "لا يحتاج إعداداً خارجياً"

    if method == "mobile_credit":
        return True, "لا يحتاج إعداداً خارجياً"

    return False, "طريقة دفع غير معروفة"


async def diagnose_payment_method(method: str) -> PaymentMethodDiagnostic:
    """Return the effective state and a safe reason for one payment method."""
    setting_key = PAYMENT_METHOD_SETTINGS.get(method)
    configured, configuration_reason = await _configuration_status(method)

    if setting_key is None:
        return PaymentMethodDiagnostic(
            method=method,
            setting_key=None,
            flag_enabled=False,
            configured=False,
            enabled=False,
            reason="طريقة دفع غير معروفة",
        )

    flag_enabled = await SettingsService.get_bool(setting_key, False)

    if not flag_enabled:
        reason = "موقوف من لوحة الأدمن"

        if not configured:
            reason += "؛ " + configuration_reason

    elif not configured:
        reason = "مخفي: " + configuration_reason

    else:
        reason = "جاهز ومظهر للمستخدمين"

    return PaymentMethodDiagnostic(
        method=method,
        setting_key=setting_key,
        flag_enabled=flag_enabled,
        configured=configured,
        enabled=flag_enabled and configured,
        reason=reason,
    )


async def payment_method_enabled(method: str) -> bool:
    """Return whether a method may be shown and used right now."""
    return (await diagnose_payment_method(method)).enabled


async def payment_method_diagnostics(
    methods: tuple[str, ...] | None = None,
) -> dict[str, PaymentMethodDiagnostic]:
    """Return safe diagnostics for all requested user-facing methods."""
    methods = methods or tuple(PAYMENT_METHOD_SETTINGS)

    return {
        method: await diagnose_payment_method(method)
        for method in methods
    }
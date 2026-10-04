"""Reusable authenticated encryption for secrets stored at rest."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from config import settings


class EncryptionError(Exception):
    pass


class EncryptionService:
    @staticmethod
    def _fernet() -> Fernet:
        key = settings.INVENTORY_ENCRYPTION_KEY.strip()
        if not key:
            raise EncryptionError("INVENTORY_ENCRYPTION_KEY is not configured")
        try:
            return Fernet(key.encode())
        except (TypeError, ValueError) as exc:
            raise EncryptionError("Invalid encryption key") from exc

    @classmethod
    def encrypt(cls, value: str) -> str:
        if not value:
            raise EncryptionError("Cannot encrypt an empty value")
        return cls._fernet().encrypt(value.encode()).decode()

    @classmethod
    def decrypt(cls, value: str) -> str:
        try:
            return cls._fernet().decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise EncryptionError("Unable to decrypt value") from exc

    @classmethod
    def is_configured(cls) -> bool:
        try:
            cls._fernet()
            return True
        except EncryptionError:
            return False

    # ── مفاتيح المزودين (ترحيل تدريجي من plaintext) ──
    @classmethod
    def store_provider_key(cls, provider) -> None:
        """يشفر provider.api_key إلى api_key_encrypted ويمسح النص الصريح إن أمكن.

        إن لم يكن المفتاح العام مُهيأً يُبقي النص الصريح مؤقتاً (توافق)،
        لكن caller يجب أن يحذر الأدمن من ضبط INVENTORY_ENCRYPTION_KEY.
        """
        raw = getattr(provider, "api_key", "") or ""
        if not raw or raw.startswith("enc:"):
            return
        if not cls.is_configured():
            return
        try:
            enc = cls.encrypt(raw)
            provider.api_key_encrypted = enc
            provider.api_key = f"enc:{enc[:12]}…"
        except EncryptionError:
            return

    @classmethod
    def reveal_provider_key(cls, provider) -> str:
        """يرجع المفتاح الخام للمزود للاستدعاءات الخارجية فقط — لا تعرضه أبداً."""
        enc = getattr(provider, "api_key_encrypted", None)
        if enc:
            try:
                return cls.decrypt(enc)
            except EncryptionError:
                pass
        raw = getattr(provider, "api_key", "") or ""
        if raw.startswith("enc:"):
            return ""
        return raw

    @classmethod
    def mask_provider_key(cls, provider) -> str:
        raw = cls.reveal_provider_key(provider)
        if not raw:
            return "—"
        if len(raw) <= 8:
            return "***"
        return f"{raw[:4]}***{raw[-2:]}"

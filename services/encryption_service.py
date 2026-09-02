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

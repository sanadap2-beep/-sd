"""Optional error monitoring; disabled when SENTRY_DSN is empty."""

from __future__ import annotations

import logging

from config import settings

logger = logging.getLogger(__name__)
_initialized = False


def init_observability() -> bool:
    global _initialized
    if _initialized or not settings.SENTRY_DSN:
        return _initialized
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=0.05,
            send_default_pii=False,
        )
        _initialized = True
        logger.info("Sentry error monitoring enabled")
    except ImportError:
        logger.warning("Sentry DSN configured but sentry-sdk is not installed")
    return _initialized

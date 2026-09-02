"""Favorite product alerts with stock-aware notifications."""

from __future__ import annotations

from services.watch_service import WatchService


class FavoritesStockService:
    """Public product-watch API kept separate from generic catalog logic."""

    toggle = staticmethod(WatchService.toggle)
    list_user_watches = staticmethod(WatchService.list_user_watches)
    check_all = staticmethod(WatchService.check_all)

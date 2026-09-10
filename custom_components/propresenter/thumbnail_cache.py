"""Bounded, shared thumbnail cache for one ProPresenter config entry."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import time
from typing import Awaitable, Callable


ThumbnailKey = tuple[str, str, int, int]


@dataclass
class _CacheEntry:
    """One cached thumbnail and its expiry time."""

    expires_at: float
    data: bytes


class ThumbnailCache:
    """TTL/LRU cache with per-entry concurrency and request deduplication."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 600,
        max_items: int = 128,
        max_bytes: int = 32 * 1024 * 1024,
        max_concurrent: int = 4,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._entries: OrderedDict[ThumbnailKey, _CacheEntry] = OrderedDict()
        self._inflight: dict[ThumbnailKey, asyncio.Task[bytes | None]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._current_identity: tuple[str, str] | None = None
        self._total_bytes = 0

    @property
    def item_count(self) -> int:
        """Return the number of live cached items."""
        self._purge_expired()
        return len(self._entries)

    @property
    def total_bytes(self) -> int:
        """Return the total bytes held by live cached items."""
        self._purge_expired()
        return self._total_bytes

    @property
    def inflight_count(self) -> int:
        """Return the number of deduplicated fetches in progress."""
        return len(self._inflight)

    def set_current_identity(self, presentation_uuid: str, revision: str) -> None:
        """Select the only UUID/revision that may be retained or populated."""
        self._current_identity = (presentation_uuid, revision)
        for key in list(self._entries):
            if key[:2] != self._current_identity:
                self._remove(key)

    def clear(self) -> None:
        """Clear cached bytes without cancelling active requests."""
        for key in list(self._entries):
            self._remove(key)

    def clear_current_identity(self) -> None:
        """Forget the UUID/revision allowed to populate late responses."""
        self._current_identity = None

    async def get_or_fetch(
        self,
        key: ThumbnailKey,
        fetcher: Callable[[], Awaitable[bytes | None]],
    ) -> bytes | None:
        """Return a cached thumbnail or share one bounded fetch."""
        self._purge_expired()
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            return cached.data

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_and_cache(key, fetcher))
            self._inflight[key] = task

        # A cancelled HTTP view must not cancel the shared request used by
        # another viewer; the request itself remains deduplicated.
        return await asyncio.shield(task)

    async def _fetch_and_cache(
        self,
        key: ThumbnailKey,
        fetcher: Callable[[], Awaitable[bytes | None]],
    ) -> bytes | None:
        try:
            async with self._semaphore:
                data = await fetcher()
            if data and self._key_is_current(key):
                self._put(key, data)
            return data
        finally:
            self._inflight.pop(key, None)

    def _key_is_current(self, key: ThumbnailKey) -> bool:
        return self._current_identity is not None and key[:2] == self._current_identity

    def _put(self, key: ThumbnailKey, data: bytes) -> None:
        if len(data) > self.max_bytes:
            return
        self._remove(key)
        self._entries[key] = _CacheEntry(time.monotonic() + self.ttl_seconds, data)
        self._total_bytes += len(data)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_items or self._total_bytes > self.max_bytes:
            self._remove(next(iter(self._entries)))

    def _purge_expired(self) -> None:
        now = time.monotonic()
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                self._remove(key)

    def _remove(self, key: ThumbnailKey) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total_bytes -= len(entry.data)

"""Caching layers.

Two kinds:

* :class:`TTLCache` - in-memory, for anything that changes (MMR, parties).
* :class:`MatchCache` - on disk, for match details. A finished match is
  immutable, so caching it forever removes the single largest source of
  repeat traffic: ten players in a lobby very often share recent games.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable

from . import paths


class TTLCache:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0

    def peek(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires < time.monotonic():
            self._entries.pop(key, None)
            return None
        return value

    def put(self, key: str, value: Any, ttl: float) -> None:
        self._entries[key] = (time.monotonic() + ttl, value)

    async def get_or_fetch(
        self, key: str, ttl: float, factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        cached = self.peek(key)
        if cached is not None:
            self.hits += 1
            return cached
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another waiter may have populated it while we queued.
            cached = self.peek(key)
            if cached is not None:
                self.hits += 1
                return cached
            self.misses += 1
            value = await factory()
            if value is not None:
                self.put(key, value, ttl)
            return value

    def invalidate_prefix(self, prefix: str) -> None:
        for key in [k for k in self._entries if k.startswith(prefix)]:
            self._entries.pop(key, None)

    def stats(self) -> dict:
        return {"entries": len(self._entries), "hits": self.hits, "misses": self.misses}


#: Bump when the stored summary gains a field. Older entries are then treated
#: as misses and re-fetched once, rather than silently serving partial data.
SUMMARY_VERSION = 2


class MatchCache:
    """Disk cache for immutable match details."""

    def __init__(self) -> None:
        paths.ensure_data_dirs()
        self._dir = paths.MATCH_CACHE_DIR
        self._memory: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _path(self, match_id: str):
        safe = "".join(ch for ch in match_id if ch.isalnum() or ch in "-_")
        return self._dir / f"{safe}.json"

    def peek(self, match_id: str) -> dict | None:
        cached = self._memory.get(match_id)
        if cached is not None:
            return cached
        path = self._path(match_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("v") != SUMMARY_VERSION:
            return None  # written by an older build; fetch it again
        self._memory[match_id] = data
        return data

    def put(self, match_id: str, summary: dict) -> None:
        summary = {**summary, "v": SUMMARY_VERSION}
        self._memory[match_id] = summary
        try:
            self._path(match_id).write_text(
                json.dumps(summary, separators=(",", ":")), encoding="utf-8"
            )
        except OSError:
            pass

    def lock(self, match_id: str) -> asyncio.Lock:
        return self._locks.setdefault(match_id, asyncio.Lock())

    def count(self) -> int:
        try:
            return sum(1 for _ in self._dir.glob("*.json"))
        except OSError:
            return 0

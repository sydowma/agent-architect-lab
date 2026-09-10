"""Buggy In-Memory Cache Module (Target for Week 2 Benchmark Refactoring).

Contains 4 fatal engineering flaws:
1. Concurrency unsafe (no threading lock, race condition during concurrent read/write).
2. Memory leak: passive expiration only (keys never accessed again remain forever).
3. Shallow copy: mutable objects can be modified externally, corrupting cached values.
4. Invalid / negative TTL: negative or zero TTL mishandling.
"""

from __future__ import annotations

import time
from typing import Any, Optional


class InMemoryCache:
    """A simplistic in-memory cache with intentional concurrency and memory bugs."""

    def __init__(self):
        # Flaw 1: Raw dictionary without threading.Lock or RLock
        self._store: dict[str, tuple[Any, Optional[float]]] = {}

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store key-value with optional TTL in seconds."""
        expire_at = (time.time() + ttl) if ttl is not None else None
        # Flaw 2: Shallow storage of mutable values (dict, list)
        self._store[key] = (value, expire_at)

    def get(self, key: str) -> Optional[Any]:
        """Retrieve value by key. Passive expiration check."""
        if key not in self._store:
            return None

        value, expire_at = self._store[key]
        # Passive check
        if expire_at is not None and time.time() > expire_at:
            # Flaw 3: Mutating store during read without lock
            del self._store[key]
            return None

        # Flaw 4: Returns raw reference to mutable value
        return value

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def size(self) -> int:
        """Returns total keys currently stored in the cache dictionary."""
        return len(self._store)

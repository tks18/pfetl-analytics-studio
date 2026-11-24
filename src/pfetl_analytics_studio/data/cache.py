"""Bounded thread-safe in-memory query cache keyed strictly by profile and PFETL run.

Owns
----
- `CacheKey` and deterministic `build_cache_key` serializer.
- Thread-safe LRU `QueryCache` ensuring run-level invalidation across PFETL executions.

Does not own
------------
- Persistent disk caching or database connections (forbidden by README §20).
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any

from pfetl_analytics_studio.semantic.filters import FilterSet
from pfetl_analytics_studio.semantic.periods import PeriodContext


@dataclass(frozen=True)
class CacheKey:
    """Strongly scoped in-memory cache lookup identifier."""

    profile_id: str
    run_id: str
    query_name: str
    spec_hash: str

    def as_tuple(self) -> tuple[str, str, str, str]:
        """Return raw hashable tuple key."""
        return (self.profile_id, self.run_id, self.query_name, self.spec_hash)


@dataclass(frozen=True)
class CacheStats:
    """Operational telemetry snapshot for a `QueryCache` instance."""

    hits: int
    misses: int
    evictions: int
    entries: int


def build_cache_key(
    profile_id: str,
    run_id: str,
    query_name: str,
    period: PeriodContext | None = None,
    filters: FilterSet | None = None,
    extra: Mapping[str, Any] | None = None,
) -> CacheKey:
    """Construct a deterministic `CacheKey` encoding profile, run ID, and semantic params.

    Parameters
    ----------
    profile_id : str
        Deterministic active profile identifier.
    run_id : str
        Active or latest successful PFETL run identifier (invalidates across runs).
    query_name : str
        Named analytical query identifier.
    period : PeriodContext | None, optional
        Resolved period context contributing start/end/comparison bounds.
    filters : FilterSet | None, optional
        Normalized domain filter set.
    extra : Mapping[str, Any] | None, optional
        Additional JSON-serializable query parameters (e.g., sort order, limit).

    Returns
    -------
    CacheKey
        Canonical hashable cache key.
    """
    period_str = period.cache_key() if period is not None else "period:none"
    filter_str = filters.cache_key() if filters is not None else "filters:none"
    extra_str = (
        json.dumps(dict(extra), sort_keys=True, separators=(",", ":"), default=str)
        if extra
        else "extra:none"
    )
    composite = f"{period_str}||{filter_str}||{extra_str}"
    spec_hash = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:32]
    return CacheKey(
        profile_id=profile_id,
        run_id=run_id,
        query_name=query_name,
        spec_hash=spec_hash,
    )


class QueryCache:
    """Thread-safe bounded LRU in-memory cache preventing cross-run stale reads."""

    def __init__(self, max_entries: int = 256) -> None:
        """Initialize bounded LRU cache.

        Parameters
        ----------
        max_entries : int, optional
            Maximum cached entries retained simultaneously, by default 256.

        Raises
        ------
        ValueError
            If `max_entries` is less than 1.
        """
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}.")
        self.max_entries = max_entries
        self._lock = Lock()
        self._store: OrderedDict[CacheKey, Any] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: CacheKey) -> Any:
        """Fetch cached value for `key`, or `None` on miss."""
        with self._lock:
            if key not in self._store:
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return self._store[key]

    def set(self, key: CacheKey, value: Any) -> None:
        """Insert or update `key` in cache, evicting oldest entries beyond `max_entries`."""
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = value
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)
                self._evictions += 1

    def get_or_compute(self, key: CacheKey, factory: Callable[[], Any]) -> Any:
        """Return cached entry if present; otherwise invoke `factory()` and cache result.

        Note that `factory()` runs outside the internal lock so nested/concurrent cache
        lookups cannot deadlock.
        """
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self._hits += 1
                return self._store[key]
            self._misses += 1

        computed = factory()
        self.set(key, computed)
        return computed

    def invalidate_run(self, profile_id: str, run_id: str) -> int:
        """Evict all cached entries matching `(profile_id, run_id)`."""
        with self._lock:
            targets = [k for k in self._store if k.profile_id == profile_id and k.run_id == run_id]
            for key in targets:
                del self._store[key]
            return len(targets)

    def invalidate_profile(self, profile_id: str) -> int:
        """Evict every cached entry belonging to `profile_id`."""
        with self._lock:
            targets = [k for k in self._store if k.profile_id == profile_id]
            for key in targets:
                del self._store[key]
            return len(targets)

    def clear(self) -> None:
        """Remove all entries from cache."""
        with self._lock:
            self._store.clear()

    def stats(self) -> CacheStats:
        """Return hit, miss, eviction, and current entry counts."""
        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                entries=len(self._store),
            )

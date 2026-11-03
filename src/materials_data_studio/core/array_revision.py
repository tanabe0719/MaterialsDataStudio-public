"""Bounded weak-identity cache for owned, fully read-only scientific arrays."""
from __future__ import annotations

import weakref
from collections import OrderedDict
from threading import RLock
from typing import Any

import numpy as np

_CACHE_LOCK = RLock()

def immutable_array(array: np.ndarray) -> bool:
    current = array
    while isinstance(current, np.ndarray):
        if current.flags.writeable:
            return False
        current = current.base
    return current is None or isinstance(current, bytes) or (
        isinstance(current, memoryview) and current.readonly and isinstance(current.obj, bytes)
    )


class ArrayRevisionCache:
    """Mutable arrays always recompute; readonly views require readonly bases."""
    instances: weakref.WeakSet = weakref.WeakSet()
    total_limit = 256 * 1024**2

    def __init__(self, limit: int = 32, maximum_bytes: int = 64 * 1024**2):
        self.limit = limit
        self.maximum_bytes, self.retained_bytes = maximum_bytes, 0
        self.entries: OrderedDict[Any, tuple[tuple[Any, ...], Any, int]] = OrderedDict()
        self.hits = self.misses = 0
        with _CACHE_LOCK:
            self.instances.add(self)

    def clear(self):
        with _CACHE_LOCK:
            self.entries.clear()
            self.retained_bytes = 0

    def get_or_create(self, arrays, condition, create):
        arrays = tuple(arrays)
        if not all(immutable_array(a) for a in arrays):
            with _CACHE_LOCK:
                self.misses += 1
            return create()
        key = (tuple(id(a) for a in arrays), condition)
        with _CACHE_LOCK:
            cached = self.entries.get(key)
            if cached is not None and all(ref() is a for ref, a in zip(cached[0], arrays, strict=True)):
                self.entries.move_to_end(key)
                self.hits += 1
                return cached[1]
            self.misses += 1
        # Numerical work may release the GIL; never serialize scientific jobs
        # behind the cache bookkeeping lock. A duplicate cold calculation is safe.
        value = create()
        from materials_data_studio.core.memory import object_sizes
        sizes = object_sizes(value)
        # A result retaining its own input would keep the weak key alive. Such
        # identity/view results are cheap and must not pin closed documents.
        if any(id(array) in sizes for array in arrays):
            return value
        size = sum(sizes.values())
        if size > self.maximum_bytes:
            return value
        owner = weakref.ref(self)
        def expired(reference):
            cache = owner()
            with _CACHE_LOCK:
                if cache is not None and key in cache.entries and any(item is reference for item in cache.entries[key][0]):
                    cache.retained_bytes -= cache.entries.pop(key)[2]
        with _CACHE_LOCK:
            if key in self.entries:
                self.retained_bytes -= self.entries.pop(key)[2]
            self.entries[key] = (tuple(weakref.ref(a, expired) for a in arrays), value, size)
            self.retained_bytes += size
            while len(self.entries) > self.limit or self.retained_bytes > self.maximum_bytes:
                self.retained_bytes -= self.entries.popitem(last=False)[1][2]
            others = sorted((cache for cache in self.instances if cache is not self), key=lambda cache: cache.retained_bytes, reverse=True)
            total = sum(cache.retained_bytes for cache in self.instances)
            for cache in others:
                if total <= self.total_limit:
                    break
                total -= cache.retained_bytes
                cache.clear()
        return value

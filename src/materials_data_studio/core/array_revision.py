"""Bounded weak-identity cache for owned, fully read-only scientific arrays."""
from __future__ import annotations

import weakref
from collections import OrderedDict
from typing import Any

import numpy as np


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
    def __init__(self, limit: int = 32, maximum_bytes: int = 64 * 1024**2):
        self.limit = limit
        self.maximum_bytes, self.retained_bytes = maximum_bytes, 0
        self.entries: OrderedDict[Any, tuple[tuple[Any, ...], Any, int]] = OrderedDict()

    def get_or_create(self, arrays, condition, create):
        if not all(immutable_array(a) for a in arrays):
            return create()
        key = (tuple(id(a) for a in arrays), condition)
        cached = self.entries.get(key)
        if cached is not None and all(ref() is a for ref, a in zip(cached[0], arrays, strict=True)):
            self.entries.move_to_end(key)
            return cached[1]
        value = create()
        from materials_data_studio.core.memory import object_sizes
        size = sum(object_sizes(value).values())
        if size > self.maximum_bytes:
            return value
        if key in self.entries:
            self.retained_bytes -= self.entries.pop(key)[2]
        self.entries[key] = (tuple(weakref.ref(a) for a in arrays), value, size)
        self.retained_bytes += size
        while len(self.entries) > self.limit or self.retained_bytes > self.maximum_bytes:
            self.retained_bytes -= self.entries.popitem(last=False)[1][2]
        return value

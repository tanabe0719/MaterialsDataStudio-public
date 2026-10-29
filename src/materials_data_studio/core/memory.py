"""Identity-deduplicated retained-memory accounting for scientific history."""

from __future__ import annotations

import sys
from dataclasses import fields, is_dataclass
from typing import Any

import numpy as np


def object_sizes(value: Any) -> dict[int, int]:
    """Count Python containers, original bytes and shared array storage once.

    This is a conservative object budget, not a claim about process RSS. Never
    traverse array elements or Qt objects; immutable shared buffers stay shared.
    """
    sizes: dict[int, int] = {}
    pending = [value]
    while pending:
        item = pending.pop()
        identity = id(item)
        if identity in sizes or item is None:
            continue
        sizes[identity] = sys.getsizeof(item)
        if isinstance(item, np.ndarray):
            if isinstance(item.base, np.ndarray) or item.base is not None:
                pending.append(item.base)
            elif sizes[identity] < item.nbytes:
                sizes[identity] += item.nbytes
        elif isinstance(item, memoryview):
            pending.append(item.obj)
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (tuple, list, set, frozenset)):
            pending.extend(item)
        elif is_dataclass(item) and not isinstance(item, type):
            pending.extend(getattr(item, field.name) for field in fields(item))
    return sizes


def retained_bytes(current: Any, history: Any) -> int:
    """Storage referenced only by history, excluding the live scientific state."""
    live = object_sizes(current)
    return sum(size for identity, size in object_sizes(history).items() if identity not in live)

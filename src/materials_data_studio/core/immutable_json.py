"""JSON-compatible immutable revisions shared by history and worker snapshots."""
from __future__ import annotations

import hashlib
import json
from typing import Any, SupportsIndex


def _immutable(*_args: Any, **_kwargs: Any) -> Any:
    raise TypeError("Immutable scientific revision; replace it with a new revision")


class FrozenDict(dict):
    _retained_size: int
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __deepcopy__(self, _memo):
        return self

    @property
    def content_digest(self) -> str:
        if not hasattr(self, "_digest"):
            self._digest = _digest(self)
        return self._digest


class FrozenList(list):
    _retained_size: int
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _immutable

    def __deepcopy__(self, _memo):
        return self

    @property
    def content_digest(self) -> str:
        if not hasattr(self, "_digest"):
            self._digest = _digest(self)
        return self._digest


class HistoryLog(FrozenList):
    """A JSON-compatible persistent append chain, shared across graph revisions."""
    def __init__(self, previous, entry):
        super().__init__()
        self.previous, self.entry = freeze(previous), freeze(entry)
        self._length = len(previous) + 1

    def __len__(self):
        return self._length

    def __iter__(self):
        entries = []
        current = self
        while isinstance(current, HistoryLog):
            entries.append(current.entry)
            current = current.previous
        yield from current
        yield from reversed(entries)

    def __getitem__(self, index):
        if index == -1 or index == self._length - 1:
            return self.entry
        return list(self)[index]

    def __eq__(self, other):
        return self is other or (isinstance(other, (list, tuple)) and list(self) == list(other))

    def __ne__(self, other):
        return not self == other

    def __contains__(self, item):
        return any(entry == item for entry in self)

    def __reversed__(self):
        return reversed(list(self))

    def copy(self):
        return list(self)

    def count(self, item):
        return list(self).count(item)

    def index(self, item, start: SupportsIndex = 0, stop: SupportsIndex | None = None):
        values = list(self)
        return values.index(item, start, len(values) if stop is None else stop)

    def __add__(self, other):
        return list(self) + other

    def __mul__(self, count):
        return list(self) * count

    __rmul__ = __mul__


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True).encode()).hexdigest()


def freeze(value: Any) -> Any:
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, dict):
        return FrozenDict((key, freeze(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return FrozenList(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def prepare_revision(value: Any) -> Any:
    """Finish expensive immutable bookkeeping alongside scientific worker work."""
    result = freeze(value)
    if isinstance(result, (FrozenDict, FrozenList)):
        from materials_data_studio.core.memory import object_sizes
        _ = result.content_digest
        object_sizes(result)
    return result


def fingerprint(value: Any) -> Any:
    """Replace immutable payloads by a content digest only at history boundaries."""
    if isinstance(value, (FrozenDict, FrozenList)):
        return {"immutable_revision": value.content_digest}
    if isinstance(value, dict):
        return {key: fingerprint(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [fingerprint(item) for item in value]
    return value

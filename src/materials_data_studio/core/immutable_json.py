"""JSON-compatible immutable revisions shared by history and worker snapshots."""
from __future__ import annotations

import hashlib
import json
from typing import Any


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


def fingerprint(value: Any) -> Any:
    """Replace immutable payloads by a content digest only at history boundaries."""
    if isinstance(value, (FrozenDict, FrozenList)):
        return {"immutable_revision": value.content_digest}
    if isinstance(value, dict):
        return {key: fingerprint(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [fingerprint(item) for item in value]
    return value

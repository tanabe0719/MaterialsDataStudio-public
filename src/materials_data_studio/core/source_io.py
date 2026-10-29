"""Read a source once so parsing, provenance and original bytes agree."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class MemorySource(io.BytesIO):
    def __init__(self, path: Path, data: bytes) -> None:
        super().__init__(data)
        self.path, self.data = path, data
        self.name, self.suffix = path.name, path.suffix

    def read_bytes(self) -> bytes:
        return self.data

    def read_text(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self.data.decode(encoding, errors)

    def open(self, mode: str = "r", encoding: str | None = None, errors: str | None = None) -> Any:
        if mode not in {"r", "rb"}:
            raise ValueError("Import sources are read-only")
        stream = io.BytesIO(self.data)
        return (
            stream
            if mode == "rb"
            else io.TextIOWrapper(stream, encoding=encoding or "utf-8", errors=errors or "strict")
        )

    def stat(self) -> SimpleNamespace:
        return SimpleNamespace(st_size=len(self.data))


@dataclass(frozen=True)
class ImportedSource:
    result: Any
    data: bytes
    sha256: str


def import_source(path: Path, parser: Callable[..., Any], **kwargs: Any) -> ImportedSource:
    if path.stat().st_size > 512 * 1024**2:
        raise ValueError("Source exceeds the 512 MiB import safety limit")
    data = path.read_bytes()
    with MemorySource(path, data) as source:
        result = parser(source, **kwargs)
    return ImportedSource(result, data, hashlib.sha256(data).hexdigest())

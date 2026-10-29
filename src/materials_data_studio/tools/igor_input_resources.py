"""Bounded, explicit GUI-selected input snapshots for isolated Igor conversions."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event

MAX_INPUT_BYTES = 64 * 1024**2
MAX_SNAPSHOT_BYTES = 144 * 1024**2


def write_input_resources(path: Path, resources: list[dict[str, str]]) -> None:
    """Write only the broker-created per-run sidecar, never document metadata."""
    from materials_data_studio.tools.igor_virtual_io import validate_resources

    validated = validate_resources(resources)
    data = json.dumps({"version": 1, "resources": validated}, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Prepared input snapshot exceeds the 144 MiB encoded limit")
    path.write_bytes(data)


def read_input_resources(path: Path) -> list[dict[str, str]]:
    """Trusted worker bootstrap: read a bounded, fixed-name input sidecar."""
    from materials_data_studio.tools.igor_virtual_io import validate_resources

    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError("Prepared input snapshot exceeds the 144 MiB encoded limit")
    with path.open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Prepared input snapshot exceeds the 144 MiB encoded limit")
    payload = json.loads(data)
    if not isinstance(payload, dict) or set(payload) != {"version", "resources"} or type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("Unsupported prepared input snapshot schema")
    return validate_resources(payload["resources"])


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp932", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Input text encoding could not be decoded")


def read_input_files(paths: list[Path], existing: list[dict[str, str]], *,
                     cancelled: Event | None = None) -> list[dict[str, str]]:
    """Never follow includes, scan directories, or grant worker filesystem access."""
    from materials_data_studio.tools.igor_virtual_io import validate_resources

    resources = {item["path"].casefold(): dict(item) for item in validate_resources(existing)}
    total = sum(len(item["text"].encode("utf-8")) for item in resources.values())
    if total > MAX_INPUT_BYTES:
        raise ValueError("Prepared inputs exceed the 64 MiB UTF-8 limit")
    for path in paths:
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("Input reading stopped")
        absolute = str(path.absolute())
        if not path.name or any(char in path.name + absolute for char in "\x00\r\n"):
            raise ValueError("Input names must not contain control separators")
        if len(path.name) > 1024 or len(absolute) > 4096:
            raise ValueError("Input file name or path is too long")
        previous = resources.get(absolute.casefold())
        if previous is None and len(resources) >= 1000:
            raise ValueError("Prepared inputs are limited to 1000 text files")
        available = MAX_INPUT_BYTES - total + (
            len(previous["text"].encode("utf-8")) if previous else 0
        )
        if not path.is_file():
            raise ValueError("Select an existing regular input text file")
        if path.stat().st_size > available:
            raise ValueError("Prepared inputs exceed the 64 MiB UTF-8 limit")
        raw = bytearray()
        with path.open("rb") as stream:
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise InterruptedError("Input reading stopped")
                chunk = stream.read(min(1024**2, available + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > available:
                    raise ValueError("Prepared inputs exceed the 64 MiB UTF-8 limit")
        text = _decode(bytes(raw))  # bytes decoding deliberately preserves CR/LF.
        encoded_size = len(text.encode("utf-8"))
        if encoded_size > available:
            raise ValueError("Prepared inputs exceed the 64 MiB UTF-8 limit")
        if previous:
            total -= len(previous["text"].encode("utf-8"))
        resources[absolute.casefold()] = {"name": path.name, "path": absolute, "text": text}
        total += encoded_size
    return validate_resources(list(resources.values()))

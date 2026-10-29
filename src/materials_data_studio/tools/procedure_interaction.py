"""Bounded, per-run UI rendezvous; no sandbox filesystem/network capability.

Only trusted bootstrap opens the two fixed streams. User Python receives the
Igor facade, not stream handles. The host validates requests before displaying
dialogs; only text explicitly selected by the user is returned to the worker.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, BinaryIO

VERSION = 1
MAX_REQUESTS = 128
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 144 * 1024**2
MAX_RESPONSE_TOTAL = 160 * 1024**2
INTERACTION_TIMEOUT = 300.0
REQUEST_NAME = "interaction_requests.jsonl"
RESPONSE_NAME = "interaction_responses.jsonl"


def _text(value: Any, limit: int = 4096) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError("Invalid interaction text")
    return value


def _keys(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise ValueError("Invalid interaction schema")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Interaction numbers must be finite")
    return float(value)


def validate_request(value: Any) -> dict[str, Any]:
    request = _keys(value, {"version", "sequence", "kind", "payload"})
    if type(request["version"]) is not int or request["version"] != VERSION or type(request["sequence"]) is not int or not 1 <= request["sequence"] <= MAX_REQUESTS:
        raise ValueError("Unsupported interaction version/sequence")
    kind, payload = request["kind"], request["payload"]
    if kind in {"file_open", "directory"}:
        _keys(payload, {"title"}, {"filter", "default_name"})
        _text(payload["title"])
        _text(payload.get("filter", ""))
        default = _text(payload.get("default_name", ""), 1024)
        if any(c in default for c in "/\\:\r\n"):
            raise ValueError("Only a base filename, not a path, may be suggested")
    elif kind == "prompt":
        _keys(payload, {"title", "fields"}, {"help"})
        _text(payload["title"])
        _text(payload.get("help", ""), 65536)
        fields = payload["fields"]
        if not isinstance(fields, list) or not 1 <= len(fields) <= 32:
            raise ValueError("Interaction prompts require 1–32 fields")
        names = set()
        for field in fields:
            _keys(field, {"name", "label", "type", "value"}, {"choices"})
            name = _text(field["name"], 256)
            if not name or name in names:
                raise ValueError("Interaction prompt fields require unique names")
            names.add(name)
            _text(field["label"])
            if field["type"] == "number":
                _number(field["value"])
                if "choices" in field:
                    raise ValueError("Numeric prompts cannot contain choices")
            elif field["type"] == "text":
                _text(field["value"], 65536)
                if "choices" in field:
                    raise ValueError("Text prompts cannot contain choices")
            elif field["type"] == "choice":
                choices = field.get("choices")
                if not isinstance(choices, list) or not 1 <= len(choices) <= 256:
                    raise ValueError("Choice prompts require bounded choices")
                for choice in choices:
                    _text(choice)
                if type(field["value"]) is not int or not 0 <= field["value"] < len(choices):
                    raise ValueError("Invalid selected prompt choice")
            else:
                raise ValueError("Unsupported interaction field type")
    elif kind == "message":
        _keys(payload, {"title", "text", "buttons"})
        _text(payload["title"])
        _text(payload["text"], 65536)
        if not isinstance(payload["buttons"], list) or not 1 <= len(payload["buttons"]) <= 8:
            raise ValueError("Interaction messages require 1–8 buttons")
        for button in payload["buttons"]:
            _text(button, 256)
    else:
        raise ValueError("Unsupported interaction kind")
    return request


def validate_response(value: Any, request: dict[str, Any]) -> dict[str, Any]:
    response = _keys(value, {"version", "sequence", "status", "payload"})
    if type(response["version"]) is not int or response["version"] != VERSION or type(response["sequence"]) is not int or response["sequence"] != request["sequence"]:
        raise ValueError("Interaction reply is stale or has an unsupported version")
    status, payload = response["status"], response["payload"]
    if status == "cancelled":
        _keys(payload, set())
    elif status == "error":
        _keys(payload, {"message"})
        _text(payload["message"])
    elif status != "ok":
        raise ValueError("Unsupported interaction response status")
    elif request["kind"] in {"file_open", "directory"}:
        from materials_data_studio.tools.igor_virtual_io import validate_resources
        _keys(payload, {"resources", "selected"})
        validate_resources(payload["resources"])
        _text(payload["selected"], 4096)
    elif request["kind"] == "prompt":
        _keys(payload, {"values"})
        fields = request["payload"]["fields"]
        values = _keys(payload["values"], {field["name"] for field in fields})
        for field in fields:
            value = values[field["name"]]
            if field["type"] == "number":
                _number(value)
            elif field["type"] == "text":
                _text(value, 65536)
            elif type(value) is not int or not 0 <= value < len(field["choices"]):
                raise ValueError("Invalid selected prompt choice")
    elif request["kind"] == "message":
        _keys(payload, {"button"})
        if type(payload["button"]) is not int or not 0 <= payload["button"] < len(request["payload"]["buttons"]):
            raise ValueError("Invalid interaction message button")
    return response


def _encode(value: dict[str, Any], maximum: int) -> bytes:
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(data) > maximum:
        raise ValueError("Interaction frame exceeds its encoded size limit")
    return data


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate interaction JSON key")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> Any:
    raise ValueError("Nonfinite interaction JSON constant")


def _write_all(stream: BinaryIO, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = stream.write(remaining)
        if written is None or written <= 0:
            raise OSError("Could not write a complete interaction frame")
        remaining = remaining[written:]


class FrameReader:
    """Incremental framing: never re-read a stream or accept partial JSON."""
    def __init__(self, stream: BinaryIO, maximum: int, total: int) -> None:
        self.stream, self.maximum, self.total = stream, maximum, total
        self.pending = bytearray()
        self.count = 0

    def poll(self) -> Any | None:
        while True:
            newline = self.pending.find(b"\n")
            if newline >= 0:
                if newline + 1 > self.maximum:
                    raise ValueError("Interaction frame exceeds its size limit")
                line = bytes(self.pending[:newline])
                del self.pending[:newline + 1]
                return json.loads(line, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            part = self.stream.read(min(1024**2, self.maximum + 1 - len(self.pending)))
            if not part:
                return None
            self.pending.extend(part)
            self.count += len(part)
            if ((len(self.pending) > self.maximum and b"\n" not in self.pending)
                    or self.count > self.total):
                raise ValueError("Interaction stream exceeds its size limit")


def prepare_interaction(root: Path) -> None:
    """Broker-only preparation, before granting read-only input capability."""
    response = root / "input" / RESPONSE_NAME
    if not response.exists():
        response.touch()


class WorkerInteraction:
    """Trusted pre-opened handles; callbacks are private to the Igor runtime."""
    def __init__(self, root: Path, *, timeout: float = INTERACTION_TIMEOUT) -> None:
        self._requests = (root / "output" / REQUEST_NAME).open("wb", buffering=0)
        self._responses = (root / "input" / RESPONSE_NAME).open("rb", buffering=0)
        self._reader = FrameReader(self._responses, MAX_RESPONSE_BYTES, MAX_RESPONSE_TOTAL)
        self._sequence = 0
        self._timeout = timeout

    def request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        request = validate_request({"version": VERSION, "sequence": self._sequence, "kind": kind, "payload": payload})
        _write_all(self._requests, _encode(request, MAX_REQUEST_BYTES))
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            response = self._reader.poll()
            if response is not None:
                response = validate_response(response, request)
                if response["status"] == "error":
                    raise RuntimeError("Interactive Igor request failed: " + response["payload"]["message"])
                return {"status": response["status"], **response["payload"]}
            time.sleep(.02)
        raise TimeoutError("Interactive Igor request timed out; document changes discarded")

    def close(self) -> None:
        self._requests.close()
        self._responses.close()


class ProcedureInteractionSession:
    """GUI-side session; callers display dialogs only after poll validates."""
    def __init__(self, root: Path) -> None:
        prepare_interaction(root)
        self.root = root
        self._requests: BinaryIO | None = None
        self._reader: FrameReader | None = None
        self._responses = (root / "input" / RESPONSE_NAME).open("ab", buffering=0)
        self._sequence = 0
        self._pending: dict[str, Any] | None = None
        self._response_total = 0
        self._closed = False

    def poll(self) -> dict[str, Any] | None:
        if self._closed or self._pending is not None:
            return None
        if self._reader is None:
            path = self.root / "output" / REQUEST_NAME
            if not path.exists():
                return None
            self._requests = path.open("rb", buffering=0)
            self._reader = FrameReader(self._requests, MAX_REQUEST_BYTES, MAX_REQUEST_BYTES * MAX_REQUESTS)
        value = self._reader.poll()
        if value is None:
            return None
        request = validate_request(value)
        if request["sequence"] != self._sequence + 1:
            raise ValueError("Interaction request sequence is not strictly increasing")
        self._sequence += 1
        self._pending = request
        return request

    def respond(self, request: dict[str, Any], response: dict[str, Any]) -> None:
        if self._closed or request is not self._pending:
            raise ValueError("Interaction session closed or reply is stale")
        reply = {"version": VERSION, "sequence": request["sequence"], **response}
        data = _encode(validate_response(reply, request), MAX_RESPONSE_BYTES)
        if self._response_total + len(data) > MAX_RESPONSE_TOTAL:
            raise ValueError("Interaction input total exceeds its encoded size limit")
        _write_all(self._responses, data)
        self._response_total += len(data)
        self._pending = None

    def close(self) -> None:
        self._closed = True
        if self._requests is not None:
            self._requests.close()
        self._responses.close()


class BrokerInteractionClock:
    """Only validated unanswered requests pause the computation wall clock."""
    def __init__(self, root: Path) -> None:
        self.session = ProcedureInteractionSession(root)
        self.responses = (root / "input" / RESPONSE_NAME).open("rb", buffering=0)
        self.reader = FrameReader(self.responses, MAX_RESPONSE_BYTES, MAX_RESPONSE_TOTAL)
        self.pending: dict[str, Any] | None = None
        self.wait_started = 0.0
        self.waited = 0.0

    def poll(self, now: float) -> float:
        if self.pending is None:
            self.pending = self.session.poll()
            if self.pending is not None:
                self.wait_started = now
        if self.pending is not None:
            reply = self.reader.poll()
            if reply is not None:
                validate_response(reply, self.pending)
                self.waited += now - self.wait_started
                self.pending = None
                self.session._pending = None
            elif now - self.wait_started > INTERACTION_TIMEOUT:
                raise TimeoutError("Interactive Igor dialog exceeded its 5-minute wait limit")
        return self.waited + (now - self.wait_started if self.pending is not None else 0.0)

    def close(self) -> None:
        self.responses.close()
        self.session.close()

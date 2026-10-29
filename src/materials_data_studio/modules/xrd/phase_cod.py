"""Optional selected-CIF HTTPS access; phase search never queries COD online."""
from __future__ import annotations

import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from materials_data_studio.modules.xrd.phase_models import (
    MAX_BYTES,
    Candidate,
    SearchQuery,
)
from materials_data_studio.modules.xrd.phase_store import PhaseStore, timestamp

BASE = "https://www.crystallography.net/cod/"
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


class _CodRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {"www.crystallography.net", "crystallography.net"}:
            raise ValueError("COD redirected outside its HTTPS origin.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, stop: threading.Event) -> bytes:
    """Two bounded attempts with cancellation between reads and request pacing."""
    global _LAST_REQUEST
    for attempt in range(2):
        if stop.is_set():
            raise InterruptedError("COD request stopped")
        try:
            with _REQUEST_LOCK:
                if stop.wait(max(0, 0.5 - (time.monotonic() - _LAST_REQUEST))):
                    raise InterruptedError("COD request stopped")
                _LAST_REQUEST = time.monotonic()
            request = Request(url, headers={"User-Agent": "MaterialsDataStudio/phase-search (bounded read-only client)", "Accept-Encoding": "identity"})
            with build_opener(_CodRedirect()).open(request, timeout=15) as response:
                if int(response.headers.get("Content-Length", 0)) > MAX_BYTES:
                    raise ValueError("COD response exceeds 4 MiB; narrow the search.")
                data = bytearray()
                started = time.monotonic()
                while True:
                    if stop.is_set() or time.monotonic() - started > 30:
                        raise InterruptedError("COD request stopped or exceeded 30 seconds")
                    chunk = response.read(min(65536, MAX_BYTES + 1 - len(data)))
                    if not chunk:
                        return bytes(data)
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("COD response exceeds 4 MiB; narrow the search.")
        except (OSError, HTTPError):
            if attempt or stop.wait(0.5):
                raise
    raise RuntimeError("COD request failed")


class CODProvider:
    def __init__(self, store: PhaseStore, transport=fetch):
        self.store, self.transport = store, transport

    def search(self, query: SearchQuery, stop: threading.Event, report=lambda message: None, *, cached_only=False) -> list[Candidate]:
        """Migration diagnostic for old callers; composition HTTP search is retired."""
        if stop.is_set():
            raise InterruptedError("Search stopped")
        raise ValueError("COD Online metadata search was replaced by the offline COD Inorganic reference database. Open Reference Databases to install it.")

    def cif(self, candidate: Candidate, stop: threading.Event) -> str:
        if not re.fullmatch(r"\d{7}", candidate.identity):
            raise ValueError("Invalid COD ID.")
        cached = self.store.read(f"metadata/cif-{candidate.identity}.json", {})
        if cached.get("hash") and (not candidate.revision or candidate.revision == cached.get("revision")):
            try:
                text = self.store.cif(cached["hash"])
            except ValueError:
                pass  # A damaged disposable cache is replaced by a bounded GET.
            else:
                candidate.content_hash, candidate.retrieved_at = cached["hash"], cached["retrieved_at"]
                return text
        text = self.transport(BASE + candidate.identity + ".cif", stop).decode("utf-8-sig")
        from materials_data_studio.modules.xrd.cif_backend import validate_cif_text
        validate_cif_text(text)
        candidate.content_hash = self.store.put_cif(text)
        candidate.retrieved_at = timestamp()
        self.store.write(f"metadata/cif-{candidate.identity}.json", {"hash": candidate.content_hash, "retrieved_at": candidate.retrieved_at, "revision": candidate.revision})
        return text

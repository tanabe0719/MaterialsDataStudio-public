"""Cache only the selected committed Delta objects, with bounded range requests."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import shutil
import urllib.parse
import urllib.request
from pathlib import Path


def cache_object(url: str, path: Path, *, chunk_size=8 * 1024**2, workers=8):
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as response:
        size = int(response.headers["Content-Length"])
    if not 0 < size <= 8 * 1024**3:
        raise ValueError("Selected source object is empty or exceeds the 8 GiB limit")
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if path.is_file() and path.stat().st_size == size and checksum_path.is_file():
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if checksum == checksum_path.read_text(encoding="ascii").strip():
            return checksum
        raise ValueError("Cached MP source checksum changed; use a new cache directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(path.parent).free < size + 64 * 1024**2:
        raise ValueError("Insufficient free space for the selected MP source cache")
    temporary = path.with_suffix(path.suffix + ".partial")
    def fetch(start):
        end = min(size, start + chunk_size) - 1
        request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}":
                        raise ValueError("MP source server did not honor the bounded byte range")
                    data = response.read(end - start + 2)
                    if len(data) != end - start + 1:
                        raise ValueError("Incomplete MP source byte range")
                    return start, data
            except (OSError, ValueError):
                if attempt == 2:
                    raise
        raise RuntimeError("Source range unavailable")
    with temporary.open("wb") as output:
        output.truncate(size)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            pending = []
            def write(future):
                start, data = future.result()
                output.seek(start)
                output.write(data)
            for start in range(0, size, chunk_size):
                pending.append(pool.submit(fetch, start))
                if len(pending) == workers:
                    write(pending.pop(0))
            for future in pending:
                write(future)
    with temporary.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    temporary.replace(path)
    checksum_path.write_text(checksum, encoding="ascii")
    return checksum


def cache_snapshot(table_uri: str, files: list[str], folder: Path):
    """No bucket listing/download: consume only Delta's selected active inventory."""
    base = folder.resolve()
    checksums = {}
    if not table_uri.startswith("s3://materialsproject-build/"):
        raise ValueError("Anonymous MP cache supports only the official build bucket")
    table_uri = table_uri.rstrip("/")
    for uri in files:
        if not uri.startswith(table_uri + "/"):
            raise ValueError("Delta file is outside its selected source collection")
        relative = urllib.parse.unquote(uri[len(table_uri) + 1:])
        path = (base / relative).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Unsafe source cache path")
        key = uri.removeprefix("s3://materialsproject-build/")
        url = "https://materialsproject-build.s3.amazonaws.com/" + urllib.parse.quote(key, safe="/=")
        print("Caching selected committed source: " + uri, flush=True)
        checksums[relative] = cache_object(url, path)
    (base / "source-checksums.json").write_text(json.dumps(checksums, indent=2), encoding="utf-8")
    return checksums

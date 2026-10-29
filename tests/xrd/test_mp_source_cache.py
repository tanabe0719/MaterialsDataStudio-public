"""Bounded public-object cache transport without live HTTP in CI."""
from __future__ import annotations

import io
import urllib.request

import pytest
from scripts.mp_source_cache import cache_object, cache_snapshot


def test_bounded_source_ranges_resume_and_corruption(monkeypatch, tmp_path):
    payload = b"synthetic public parquet placeholder" * 8
    requests = []
    class Response(io.BytesIO):
        def __init__(self, data, headers, status):
            super().__init__(data)
            self.headers, self.status = headers, status
    def open_request(request, **_kwargs):
        requests.append(request)
        if request.get_method() == "HEAD":
            return Response(b"", {"Content-Length": str(len(payload))}, 200)
        start, end = map(int, request.get_header("Range").removeprefix("bytes=").split("-"))
        return Response(payload[start:end+1], {"Content-Range": f"bytes {start}-{end}/{len(payload)}"}, 206)
    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    path = tmp_path / "committed.parquet"
    checksum = cache_object("https://materialsproject-build.s3.amazonaws.com/fixture", path, chunk_size=16, workers=2)
    assert path.read_bytes() == payload and len(checksum) == 64
    assert len([r for r in requests if r.get_method() == "GET"]) > 1
    requests.clear()
    assert cache_object("https://materialsproject-build.s3.amazonaws.com/fixture", path) == checksum
    assert len(requests) == 1 and requests[0].get_method() == "HEAD"
    path.write_bytes(b"X" + payload[1:])
    with pytest.raises(ValueError, match="checksum"):
        cache_object("https://materialsproject-build.s3.amazonaws.com/fixture", path)
    with pytest.raises(ValueError, match="official"):
        cache_snapshot("s3://other-bucket/table", [], tmp_path)
    with pytest.raises(ValueError, match="Unsafe"):
        cache_snapshot("s3://materialsproject-build/collections/summary", ["s3://materialsproject-build/collections/summary/../escape.parquet"], tmp_path)

"""Fetch checksum-pinned upstream source archives, without executing their contents."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_manifest() -> list[dict]:
    records = json.loads((ROOT / "packaging/corresponding_sources.json").read_text(encoding="utf-8"))["sources"]
    if len({r["name"] for r in records}) != len(records):
        raise ValueError("Duplicate source archive")
    for record in records:
        if Path(record["name"]).name != record["name"] or not record["url"].startswith(("https://download.qt.io/", "https://files.pythonhosted.org/")):
            raise ValueError("Invalid upstream source identity")
    return records


def fetch(record: dict) -> Path:
    folder = ROOT / "release_assets/sources"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / record["name"]
    if path.is_file() and digest(path) == record["sha256"]:
        return path
    partial = path.with_suffix(path.suffix + ".partial")
    request = urllib.request.Request(record["url"], headers={"User-Agent": "MDS-corresponding-source/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as stream:
            shutil.copyfileobj(response, stream, 4 * 1024**2)
        if digest(partial) != record["sha256"]:
            raise ValueError(f"Source checksum mismatch: {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    print(f"Verified source: {path.name}", flush=True)
    return path


def prepare(*, bundle: bool = False) -> Path | None:
    records = source_manifest()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(fetch, records))
    if not bundle:
        return None
    version = json.loads((ROOT / "packaging/publication.json").read_text(encoding="utf-8"))["version"]
    destination = ROOT / f"release_assets/MaterialsDataStudio-v{version}-CorrespondingSources.zip"
    temporary = destination.with_suffix(".zip.partial")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in paths:
            archive.write(path, "sources/" + path.name)
        archive.write(ROOT / "packaging/corresponding_sources.json", "source-manifest.json")
        for name in ("building_and_licenses.md", "building_and_licenses_ja.md", "reference_databases.md", "reference_databases_ja.md"):
            archive.write(ROOT / "docs" / name, "docs/" + name)
        archive.writestr("README.md", "# Corresponding source / 対応ソース\n\n[English](docs/building_and_licenses.md) | [日本語](docs/building_and_licenses_ja.md)\n\nOriginal upstream archives and SHA-256 identities are in sources/ and source-manifest.json. / 公式ソースとhashはsources/とsource-manifest.jsonに収録しています。\n")
        archive.writestr("docs/index.md", "# Source documentation / ソース案内\n\n[English](building_and_licenses.md) | [日本語](building_and_licenses_ja.md)\n\n[Reference data](reference_databases.md) | [参照データ](reference_databases_ja.md)\n")
    temporary.replace(destination)
    print(f"Corresponding sources: {destination.name}", flush=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", action="store_true")
    prepare(bundle=parser.parse_args().bundle)

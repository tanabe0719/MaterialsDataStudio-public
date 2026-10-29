"""Restore only checksum-pinned reference release assets from this repository."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def matches(path: Path, item: dict) -> bool:
    if not path.is_file() or path.stat().st_size != item["size"]:
        return False
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() == item["digest"]


def main() -> None:
    repository = json.loads((ROOT / "packaging/publication.json").read_text(encoding="utf-8"))["repository"]
    items = json.loads((ROOT / "packaging/reference_assets.json").read_text(encoding="utf-8"))
    folder = ROOT / "release_assets"
    folder.mkdir(exist_ok=True)
    for item in items:
        if Path(item["name"]).name != item["name"]:
            raise ValueError("Unsafe reference asset name")
        path = folder / item["name"]
        if matches(path, item):
            continue
        metadata = json.loads(subprocess.check_output(["gh", "api", f"repos/{repository}/releases/latest"], text=True))
        found = next((a for a in metadata["assets"] if all(a.get(k) == item[k] for k in ("name", "size", "digest"))), None)
        if found is None:
            raise ValueError("No checksum-matched reference asset; prepare it locally: " + item["name"])
        if path.exists():
            raise ValueError("Preserve mismatched local reference asset before restoring")
        subprocess.run(["gh", "release", "download", metadata["tag_name"], "--repo", repository, "--pattern", item["name"], "--dir", str(folder)], check=True)
        if not matches(path, item):
            raise ValueError("Reference download failed checksum verification")
    print(f"Verified {len(items)} reference assets")


if __name__ == "__main__":
    main()

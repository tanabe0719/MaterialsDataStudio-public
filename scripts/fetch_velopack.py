"""Restore a pinned official Velopack CLI locally, without a global tool install."""
from __future__ import annotations

import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "packaging/velopack.json").read_text(encoding="utf-8"))
    version = config["toolVersion"]
    destination = root / ".tools" / f"velopack-{version}"
    destination.mkdir(parents=True, exist_ok=True)
    package = destination / f"vpk.{version}.nupkg"
    if not package.is_file():
        url = f"https://github.com/velopack/velopack/releases/download/{version}/vpk.{version}.nupkg"
        temporary = package.with_suffix(".download")
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(package)
    with package.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != config["toolSha256"]:
        raise ValueError("The Velopack CLI package does not match its pinned SHA-256")
    with zipfile.ZipFile(package) as archive:
        for name in archive.namelist():
            if not (destination / name).resolve().is_relative_to(destination.resolve()):
                raise ValueError("Unsafe Velopack archive path")
        archive.extractall(destination)
    print(destination / "tools/net8.0/any/vpk.dll")


if __name__ == "__main__":
    main()

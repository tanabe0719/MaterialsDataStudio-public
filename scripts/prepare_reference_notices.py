"""Repackage reference ZIP notices while verifying unchanged database payloads."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = ("COD-Inorganic-2026-07.zip", "MaterialsProject-2026-09-28-part1-of2.zip", "MaterialsProject-2026-09-28-part2-of2.zip")


def repackage(source: Path, destination: Path, english: str, japanese: str) -> dict:
    if destination.resolve() == source.resolve():
        raise ValueError("Keep the original reference archive unchanged")
    if destination.exists():
        raise FileExistsError(destination)
    records = []
    temporary = destination.with_suffix(".zip.partial")
    try:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as output:
            for entry in original.infolist():
                if entry.is_dir() or entry.filename in {"NOTICE.txt", "NOTICE_JA.txt"}:
                    continue
                digest = hashlib.sha256()
                with original.open(entry) as inp, output.open(entry.filename, "w", force_zip64=True) as out:
                    while chunk := inp.read(4 * 1024**2):
                        digest.update(chunk)
                        out.write(chunk)
                records.append({"member": entry.filename, "bytes": entry.file_size, "sha256": digest.hexdigest()})
            output.writestr("NOTICE.txt", english)
            output.writestr("NOTICE_JA.txt", japanese)
        with zipfile.ZipFile(temporary) as output:
            for record in records:
                digest = hashlib.sha256()
                with output.open(record["member"]) as stream:
                    while chunk := stream.read(4 * 1024**2):
                        digest.update(chunk)
                if digest.hexdigest() != record["sha256"]:
                    raise ValueError("Reference payload changed")
        if temporary.stat().st_size >= 2 * 1024**3:
            raise ValueError("Reference archive exceeds the GitHub per-asset limit")
        temporary.replace(destination)
        with destination.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        Path(str(destination) + ".sha256").write_text(checksum + "  " + destination.name + "\n", encoding="ascii")
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Repackaged and verified: {destination.name}", flush=True)
    return {"name": destination.name, "size": destination.stat().st_size, "digest": "sha256:" + checksum, "unchanged_payloads": records}


def prepare(source_folder: Path, destination_folder: Path) -> None:
    destination_folder.mkdir(parents=True, exist_ok=True)
    english = (ROOT / "docs/reference_databases.md").read_text(encoding="utf-8")
    japanese = (ROOT / "docs/reference_databases_ja.md").read_text(encoding="utf-8")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda name: repackage(source_folder / name, destination_folder / name, english, japanese), ARCHIVES))
    shutil.copyfile(ROOT / "docs/reference_databases.md", destination_folder / "REFERENCE_DATABASE_NOTICES.txt")
    shutil.copyfile(ROOT / "docs/reference_databases_ja.md", destination_folder / "REFERENCE_DATABASE_NOTICES_ja.txt")
    (destination_folder / "reference-payload-verification.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("Original archives retained; all payload hashes verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    prepare(args.source, args.destination)

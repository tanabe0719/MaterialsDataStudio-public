"""Build two independent ZIP parts of one lossless MP SQLite distribution."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from materials_data_studio.modules.xrd.mp_archive import SPLIT_FORMAT
from materials_data_studio.modules.xrd.mp_database import NOTICES, file_hash
from materials_data_studio.modules.xrd.reference_index import readonly


def package_split(source: Path, folder: Path) -> list[Path]:
    with readonly(source) as connection:
        manifest = json.loads(connection.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
    if manifest.get("format") not in {"mds-mp-reference-1", "mds-mp-reference-2"} or manifest.get("building"):
        raise ValueError("Select a completed MP index")
    size = source.stat().st_size
    lengths = [(size + 1) // 2, size // 2]
    full_hash = hashlib.sha256()
    parts = []
    with source.open("rb") as stream:
        for index, length in enumerate(lengths, 1):
            digest, remaining = hashlib.sha256(), length
            while remaining:
                chunk = stream.read(min(8 * 1024**2, remaining))
                if not chunk:
                    raise ValueError("Source changed while packaging")
                digest.update(chunk)
                full_hash.update(chunk)
                remaining -= len(chunk)
            parts.append(dict(archive=f"MaterialsProject-{manifest['snapshot']}-part{index}-of2.zip", member=f"database.part{index}", bytes=length, sha256=digest.hexdigest()))
    distribution = dict(distribution_format=SPLIT_FORMAT, database_sha256=full_hash.hexdigest(), database_bytes=size, parts=parts,
                        snapshot=manifest["snapshot"], format=manifest["format"], license=manifest["license"], included=manifest["included"],
                        instructions="Download both ZIP files into one folder. In MDS Reference Databases, Import MP Index and select either ZIP. Keep both ZIPs unchanged; manual extraction is unnecessary.")
    folder.mkdir(parents=True, exist_ok=True)
    outputs = [folder / p["archive"] for p in parts]
    if any(p.exists() or p.with_suffix(".zip.partial").exists() for p in outputs):
        raise ValueError("Output already exists; preserve existing archives")
    with source.open("rb") as stream:
        for part, output in zip(parts, outputs, strict=True):
            temporary = output.with_suffix(".zip.partial")
            try:
                with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zipped:
                    zipped.writestr("manifest.json", json.dumps(distribution, indent=2))
                    zipped.writestr("NOTICE.txt", NOTICES + "\nLicense: https://creativecommons.org/licenses/by/4.0/\n")
                    with zipped.open(part["member"], "w", force_zip64=True) as target:
                        remaining = part["bytes"]
                        while remaining:
                            chunk = stream.read(min(8 * 1024**2, remaining))
                            if not chunk:
                                raise ValueError("Source changed while packaging")
                            target.write(chunk)
                            remaining -= len(chunk)
                digest = hashlib.sha256()
                with zipfile.ZipFile(temporary) as zipped, zipped.open(part["member"]) as restored:
                    while chunk := restored.read(8 * 1024**2):
                        digest.update(chunk)
                if digest.hexdigest() != part["sha256"] or temporary.stat().st_size >= 2 * 1024**3:
                    raise ValueError("Part failed round-trip hash or GitHub asset size limit")
                temporary.replace(output)
                output.with_suffix(".zip.sha256").write_text(file_hash(output, Event()) + "  " + output.name + "\n", encoding="ascii")
                print(str(output), output.stat().st_size, flush=True)
            finally:
                temporary.unlink(missing_ok=True)
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    package_split(args.source, args.output)

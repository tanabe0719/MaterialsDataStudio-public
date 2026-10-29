"""Create one verified distribution ZIP without splitting or uploading a database."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from materials_data_studio.modules.xrd.mp_database import NOTICES, file_hash
from materials_data_studio.modules.xrd.reference_index import readonly


def package(source: Path, output: Path) -> dict:
    if output.exists() or source.resolve() == output.resolve():
        raise ValueError("Choose a new archive; original databases and archives are preserved")
    with readonly(source) as connection:
        manifest = json.loads(connection.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        if manifest.get("format") not in {"mds-mp-reference-1", "mds-mp-reference-2"} or manifest.get("building"):
            raise ValueError("Select a completed MP index")
    checksum = file_hash(source, Event())
    info = {"snapshot": manifest["snapshot"], "format": manifest["format"], "included": manifest["included"],
            "database_sha256": checksum, "database_bytes": source.stat().st_size, "license": manifest["license"],
            "source": manifest.get("source_dataset", {}), "instructions": "Extract all files, then import the SQLite file in Reference Databases. No network is needed after installation."}
    filename = "MaterialsProject-" + str(manifest["snapshot"]) + ".sqlite"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    if temporary.exists():
        raise ValueError("An incomplete archive already exists; choose a new output")
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            archive.write(source, filename)
            archive.writestr(filename + ".sha256", checksum)
            archive.writestr("manifest.json", json.dumps(info, indent=2))
            archive.writestr("NOTICE.txt", NOTICES + "\nLicense: https://creativecommons.org/licenses/by/4.0/\n")
        restored = hashlib.sha256()
        with zipfile.ZipFile(temporary) as archive, archive.open(filename) as stream:
            while chunk := stream.read(8 * 1024**2):
                restored.update(chunk)
        if restored.hexdigest() != checksum:
            raise ValueError("Archive round-trip checksum differs from the database")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    info.update(archive_bytes=output.stat().st_size, archive_sha256=file_hash(output, Event()), archive=str(output))
    output.with_suffix(output.suffix + ".sha256").write_text(info["archive_sha256"], encoding="ascii")
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.output), indent=2), flush=True)

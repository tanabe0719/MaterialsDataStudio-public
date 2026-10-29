"""Stream verified MP distributions into a local database before registration."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from threading import Event

from materials_data_studio.modules.xrd.reference_index import check_stop

SPLIT_FORMAT = "mds-mp-split-1"
MAX_DATABASE_BYTES = 16 * 1024**3


def _manifest(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as zipped:
        entry = zipped.getinfo("manifest.json")
        if entry.file_size > 1024**2:
            raise ValueError("MP archive manifest is too large")
        return json.loads(zipped.read(entry))


def install_archive(manager, archive: Path, stop: Event, report=lambda message: None) -> dict:
    """Accept either part (both alongside it), or the older complete ZIP."""
    manifest = _manifest(archive)
    checksum = manifest.get("database_sha256", "")
    size = manifest.get("database_bytes", 0)
    if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum) or not isinstance(size, int) or not 0 < size <= MAX_DATABASE_BYTES:
        raise ValueError("Invalid MP archive identity or size")
    parts = manifest.get("parts") if manifest.get("distribution_format") == SPLIT_FORMAT else None
    if parts is not None:
        if not isinstance(parts, list) or len(parts) != 2:
            raise ValueError("MP split distribution requires exactly two parts")
        names = [part["archive"] for part in parts]
        if len(set(names)) != 2 or any(not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name or ":" in name or not name.endswith(".zip") for name in names):
            raise ValueError("Invalid MP part filename")
        if archive.name not in names or sum(part["bytes"] for part in parts) != size or any(not isinstance(p["bytes"], int) or p["bytes"] <= 0 for p in parts):
            raise ValueError("MP parts do not match database size")
        for name in names:
            path = archive.parent / name
            if not path.is_file():
                raise ValueError("Download both MP ZIP parts into the same folder: " + name)
            if _manifest(path) != manifest:
                raise ValueError("MP ZIP parts are from different distributions")
    else:
        with zipfile.ZipFile(archive) as zipped:
            names = [i.filename for i in zipped.infolist() if i.filename.endswith(".sqlite")]
            if len(names) != 1:
                raise ValueError("MP ZIP must contain one database")
        parts = [{"archive": archive.name, "member": names[0], "bytes": size, "sha256": checksum}]
    folder = manager.store.root / "reference_databases"
    folder.mkdir(exist_ok=True)
    if shutil.disk_usage(folder).free < size + 100 * 1024**2:
        raise ValueError("Not enough free space to install the MP database")
    with tempfile.TemporaryDirectory(dir=folder, prefix="mp-import-") as temporary:
        target = Path(temporary) / "MaterialsProject.sqlite"
        full_hash = hashlib.sha256()
        with target.open("xb") as output:
            for index, part in enumerate(parts, 1):
                report(f"Installing Materials Project part {index}/{len(parts)}")
                part_hash, written = hashlib.sha256(), 0
                with zipfile.ZipFile(archive.parent / part["archive"]) as zipped:
                    matches = [i for i in zipped.infolist() if i.filename == part["member"]]
                    if len(matches) != 1 or matches[0].file_size != part["bytes"] or matches[0].flag_bits & 1:
                        raise ValueError("Invalid MP ZIP part size or entry")
                    with zipped.open(matches[0]) as source:
                        while chunk := source.read(8 * 1024**2):
                            check_stop(stop)
                            written += len(chunk)
                            if written > part["bytes"]:
                                raise ValueError("MP part exceeds its declared size")
                            full_hash.update(chunk)
                            part_hash.update(chunk)
                            output.write(chunk)
                if written != part["bytes"] or part_hash.hexdigest() != part["sha256"]:
                    raise ValueError("MP ZIP part SHA-256 mismatch")
        if full_hash.hexdigest() != checksum:
            raise ValueError("MP database SHA-256 mismatch")
        check_stop(stop)
        destination = folder / ("mp-" + checksum + ".sqlite")
        # Content-addressed paths never overwrite registered external databases.
        if destination.exists():
            from materials_data_studio.modules.xrd.mp_database import file_hash
            if file_hash(destination, stop) != checksum:
                raise ValueError("Existing installed MP database failed its checksum; choose another data directory")
        else:
            target.replace(destination)
        return manager.register(destination, stop, report)

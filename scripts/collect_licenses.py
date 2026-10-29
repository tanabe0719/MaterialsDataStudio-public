"""Collect installed-distribution and upstream component notices for the frozen app."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from scripts.prepare_corresponding_sources import ROOT, digest, source_manifest

LEGAL = re.compile(r"(?:^|[._ -])(?:licen[cs]e(?:ref|s)?|copying|copyright|notice|authors)(?:[._ -]|$)", re.I)


def legal_name(name: str) -> bool:
    path = PurePosixPath(name)
    return path.suffix.lower() not in {".py", ".c", ".cpp", ".h", ".hpp"} and bool(LEGAL.search(path.name))


def source_notices(archive: Path):
    def allowed(name: str) -> bool:
        relative = PurePosixPath(name)
        return (not relative.is_absolute() and ".." not in relative.parts
                and (legal_name(name) or any(p.lower() in {"licenses", "license"} for p in relative.parts)))
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                if not entry.is_dir() and allowed(entry.filename):
                    yield entry.filename, source.read(entry)
    else:
        with tarfile.open(archive) as source:
            for member in source:
                if member.isfile() and allowed(member.name):
                    stream = source.extractfile(member)
                    if stream is not None:
                        with stream:
                            yield member.name, stream.read()


def collect(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    for record in source_manifest():
        projects = ("PySide6-Essentials",) if record["project"] in {"qtbase", "qtsvg"} else record["project"].split("+")
        for project in projects:
            if metadata.version(project) != record["version"]:
                raise ValueError(f"Corresponding-source version differs from build environment: {project}")
    inventory = []
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name, version = distribution.metadata["Name"], distribution.version
        component = re.sub(r"[^a-z0-9_.-]", "-", name.lower()) + "-" + version
        notices = []
        for entry in distribution.files or ():
            relative = PurePosixPath(str(entry).replace("\\", "/"))
            if ".." in relative.parts or not legal_name(str(relative)):
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            target = destination / "python" / component / str(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            notices.append(target.relative_to(destination).as_posix())
        inventory.append({"component": name, "version": version,
                          "license_expression": distribution.metadata.get("License-Expression"),
                          "license_metadata": distribution.metadata.get("License"),
                          "notices": notices})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError("CPython license was not found")
    (destination / "cpython").mkdir(exist_ok=True)
    shutil.copyfile(python_license, destination / "cpython/LICENSE.txt")
    qt_notices = 0
    other_notices = 0
    for record in source_manifest():
        qt_component = record["project"] in {"qtbase", "qtsvg", "pyside6-essentials+shiboken6"}
        archive = ROOT / "release_assets/sources" / record["name"]
        if not archive.is_file() or digest(archive) != record["sha256"]:
            raise ValueError("Run scripts/prepare_corresponding_sources.py before packaging")
        for name, contents in source_notices(archive):
            target = destination / ("qt-upstream" if qt_component else "upstream") / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
            if qt_component:
                qt_notices += 1
            else:
                other_notices += 1
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / name, destination / name)
    for name in ("building_and_licenses.md", "building_and_licenses_ja.md"):
        shutil.copyfile(ROOT / "docs" / name, destination / name)
    shutil.copyfile(ROOT / "packaging/corresponding_sources.json", destination / "corresponding-sources.json")
    result = {"format": 1, "note": "Build environment inventory; includes notices for build tools not necessarily shipped at runtime.",
              "python": sys.version.split()[0], "packages": inventory, "qt_upstream_notices": qt_notices,
              "other_upstream_notices": other_notices}
    (destination / "inventory.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    checksums = []
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(destination).as_posix())
    (destination / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".test-tmp/legal")
    result = collect(parser.parse_args().output)
    print(f"Collected {len(result['packages'])} package entries and {result['qt_upstream_notices']} upstream Qt notices")

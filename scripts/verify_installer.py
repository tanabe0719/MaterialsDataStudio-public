"""Verify the self-contained Setup payload against the frozen build and source."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import marshal
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from packaging.version import Version
from PyInstaller.archive.readers import CArchiveReader
from verify_local_package import comparable

from materials_data_studio.version import __version__


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "packaging/velopack.json").read_text(encoding="utf-8"))
    frozen = root / f"dist_v{__version__.replace('.', '')}/MaterialsDataStudio"
    executable = frozen / config["mainExe"]
    reader = CArchiveReader(str(executable))
    archive = reader.open_embedded_archive("PYZ.pyz")
    security_versions = {}
    for name, minimum in {"pillow": "13.0.0", "certifi": "2026.10.23", "urllib3": "2.8.0", "setuptools": "84.2.1"}.items():
        actual = importlib.metadata.version(name)
        assert Version(actual) >= Version(minimum), (name, actual)
        security_versions[name] = actual
    # Check the shipped implementation, not just updated metadata/lockfiles.
    assert "PIL.Image" in archive.toc and "PIL._version" in archive.toc
    for package, module in (("pillow", "PIL._version"), ("pillow", "PIL.Image"),
                            ("certifi", "certifi.core"), ("urllib3", "urllib3.response")):
        if module in archive.toc:
            source = Path(importlib.metadata.distribution(package).locate_file(module.replace(".", "/") + ".py"))
            assert comparable(compile(source.read_text(encoding="utf-8"), str(source), "exec", dont_inherit=True)) == comparable(archive.extract(module)), module
    pillow = importlib.metadata.distribution("pillow")
    verified_imaging = False
    for entry in pillow.files or ():
        if str(entry).endswith(".pyd"):
            original = Path(pillow.locate_file(entry))
            shipped = list((frozen / "_internal").rglob(original.name))
            if shipped:
                assert all(p.read_bytes() == original.read_bytes() for p in shipped), original.name
                verified_imaging |= original.name.startswith("_imaging.")
    assert verified_imaging, "Missing Pillow imaging extension"
    certificate_bundle = frozen / "_internal/certifi/cacert.pem"
    if certificate_bundle.is_file():
        assert certificate_bundle.read_bytes() == Path(importlib.metadata.distribution("certifi").locate_file("certifi/cacert.pem")).read_bytes()
    modules = 0
    for source in (root / "src/materials_data_studio").rglob("*.py"):
        module = source.relative_to(root / "src").with_suffix("").as_posix().replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]
        embedded = marshal.loads(reader.extract("__main__")) if module.endswith(".__main__") else archive.extract(module)
        assert comparable(compile(source.read_text(encoding="utf-8"), str(source), "exec", dont_inherit=True)) == comparable(embedded), module
        modules += 1
    setup = root / f"release_assets/MaterialsDataStudio-v{__version__}-Setup.exe"
    with setup.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert Path(str(setup) + ".sha256").read_text().split()[0] == digest
    with zipfile.ZipFile(setup) as bundle:
        names = bundle.namelist()
        metadata = {item.tag.split("}")[-1]: item.text for item in ET.fromstring(bundle.read("lib/app/sq.version")).iter()}
        assert metadata["id"] == config["packId"]
        assert metadata["version"] == __version__
        assert metadata["rid"] == config["runtime"]
        assert metadata["channel"] == config["channel"]
        assert metadata["mainExe"] == config["mainExe"]
        assert not metadata.get("runtimeDependencies")
        # Velopack adds its execution stub, metadata and updater; all existing
        # application files must remain byte-identical to the validated build.
        for path in frozen.rglob("*"):
            if path.is_file():
                name = "lib/app/" + path.relative_to(frozen).as_posix()
                assert bundle.read(name) == path.read_bytes(), name
        assert any("velopack" in name and name.endswith(".pyd") for name in names)
        assert bundle.read("lib/app/_internal/resources/licenses/velopack/LICENSE") == (root / "resources/licenses/velopack/LICENSE").read_bytes()
        assert bundle.read("lib/app/_internal/LICENSE") == (root / "LICENSE").read_bytes()
        legal_prefix = "lib/app/_internal/licenses/"
        inventory = json.loads(bundle.read(legal_prefix + "inventory.json"))
        records = {re.sub(r"[-_.]+", "-", p["component"].lower()): p for p in inventory["packages"]}
        assert all(records[name]["version"] == version for name, version in security_versions.items())
        for required in ("pyside6-essentials", "shiboken6", "igor2", "gemmi", "certifi", "orjson", "tqdm", "numpy", "scipy", "pandas", "matplotlib", "pyqtgraph", "pillow", "fonttools", "spglib"):
            assert required in records and records[required]["notices"], required
            assert all(legal_prefix + p in names for p in records[required]["notices"]), required
        for line in bundle.read(legal_prefix + "SHA256SUMS").decode("utf-8").splitlines():
            checksum, relative = line.split("  ", 1)
            assert hashlib.sha256(bundle.read(legal_prefix + relative)).hexdigest() == checksum, relative
        assert inventory["qt_upstream_notices"] > 0
        assert any("qt-upstream/" in p and "LGPL" in p for p in names)
        assert any(p.endswith("/LICENSE_LAST_RESORT_FONT") for p in names)
        assert any(p.endswith("/LICENSE_BAKOMA") for p in names)
        assert bundle.read(legal_prefix + "cpython/LICENSE.txt")
        assert json.loads(bundle.read(legal_prefix + "corresponding-sources.json"))["sources"]
        images: set[str] = set()
        for filename in ("user_manual.md", "user_manual_en.md", "graph_viewer_manual.md", "graph_viewer_manual_en.md"):
            source = root / "docs" / filename
            assert bundle.read("lib/app/_internal/docs/" + filename) == source.read_bytes()
            images.update(re.findall(r"!\[[^\]]*\]\((screenshots/[^)]+)\)", source.read_text(encoding="utf-8")))
        packaged = {name.split("/docs/")[-1] for name in names if "/docs/screenshots/" in name and name.endswith(".png")}
        assert packaged == images
        assert not any(re.search(r"(?:LAPTOP|DESKTOP)-[A-Z0-9]+", name) or "/sample/" in name or "/from_user/" in name for name in names)
        assert not any(Path(name).suffix.lower() in {".pxp", ".mdsg", ".mdss", ".h5xp", ".pfx", ".key"} for name in names)
        assert not any(name.lower().endswith((".sq", ".sq.info", ".sq.infostat", ".sq.search")) for name in names)
        assert not any(name.lower().endswith(".sqlite") and not name.endswith("/pymatgen/symmetry/symm_data_magnetic.sqlite") for name in names)
        for name in names:
            if name.lower().endswith(".pem"):
                assert name.endswith("/certifi/cacert.pem") and b"PRIVATE KEY" not in bundle.read(name)
        assert bundle.testzip() is None
    report = {"version": __version__, "compiled_modules_match_source": modules,
              "manual_images": len(images), "frozen_payload_matches_installer": True,
              "crc": "passed", "sha256": digest, "installer_bytes": setup.stat().st_size,
              "pack_id": config["packId"], "full_offline_payload": True,
              "security_dependency_versions": security_versions}
    (root / ".test-tmp").mkdir(exist_ok=True)
    (root / ".test-tmp/installer_artifact.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

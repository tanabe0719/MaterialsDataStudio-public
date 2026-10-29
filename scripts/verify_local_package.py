"""Verify a local versioned ZIP against source and referenced manual resources."""

from __future__ import annotations

import hashlib
import marshal
import re
import types
import zipfile
from pathlib import Path
from typing import Any

from PyInstaller.archive.readers import CArchiveReader

from materials_data_studio.version import __version__


def comparable(value: Any) -> Any:
    if isinstance(value, types.CodeType):
        return (
            value.co_code, value.co_names, value.co_varnames, value.co_argcount,
            value.co_kwonlyargcount, value.co_flags,
            tuple(comparable(item) for item in value.co_consts),
        )
    if isinstance(value, tuple):
        return tuple(comparable(item) for item in value)
    if isinstance(value, frozenset):
        return tuple(sorted(map(repr, value)))
    return value


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    tag = "v" + __version__.replace(".", "")
    executable = root / f"dist_{tag}/MaterialsDataStudio/MaterialsDataStudio.exe"
    reader = CArchiveReader(str(executable))
    archive = reader.open_embedded_archive("PYZ.pyz")
    modules = 0
    for source in (root / "src/materials_data_studio").rglob("*.py"):
        module = source.relative_to(root / "src").with_suffix("").as_posix().replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]
        if module.endswith(".__main__"):
            embedded = marshal.loads(reader.extract("__main__"))
        else:
            assert module in archive.toc, module
            embedded = archive.extract(module)
        assert comparable(compile(source.read_text(encoding="utf-8"), str(source), "exec", dont_inherit=True)) == comparable(embedded), module
        modules += 1
    package = root / f"release_assets/MaterialsDataStudio-v{__version__}-windows-x64.zip"
    with zipfile.ZipFile(package) as distribution:
        names = [name.replace("\\", "/") for name in distribution.namelist()]
        assert not any("/sample/" in name or "/from_user/" in name or "/tmp/" in name for name in names)
        assert not any(Path(name).suffix.lower() in {".pxp", ".mdsg", ".mdss", ".h5xp", ".pfx", ".key"} for name in names)
        assert not any(name.lower().endswith((".sq", ".sq.info", ".sq.infostat", ".sq.search")) for name in names)
        assert not any(name.lower().endswith(".sqlite") and not name.endswith("/pymatgen/symmetry/symm_data_magnetic.sqlite") for name in names)
        for name in names:
            if name.lower().endswith(".pem"):
                assert name.endswith("/certifi/cacert.pem") and b"PRIVATE KEY" not in distribution.read(name)
        screenshots: set[str] = set()
        for filename in ("user_manual.md", "user_manual_en.md", "graph_viewer_manual.md", "graph_viewer_manual_en.md"):
            source = root / "docs" / filename
            name = next(name for name in names if name.endswith("/docs/" + filename))
            assert distribution.read(name) == source.read_bytes(), filename
            screenshots.update(re.findall(r"!\[[^\]]*\]\((screenshots/[^)]+)\)", source.read_text(encoding="utf-8")))
        for screenshot in screenshots:
            name = next(name for name in names if name.endswith("/docs/" + screenshot))
            assert distribution.read(name) == (root / "docs" / screenshot).read_bytes(), screenshot
        packaged = {name.split("/docs/")[-1] for name in names if "/docs/screenshots/" in name and name.endswith(".png")}
        assert packaged == screenshots
        assert not any(re.search(r"(?:LAPTOP|DESKTOP)-[A-Z0-9]+", name) for name in names)
        assert distribution.testzip() is None
    with package.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    assert checksum == Path(str(package) + ".sha256").read_text().split()[0]
    print(f"Verified {modules} compiled modules; {len(screenshots)} manual images; ZIP CRC and SHA-256 match.")
    print(f"ZIP bytes: {package.stat().st_size}; SHA-256: {checksum}")


if __name__ == "__main__":
    main()

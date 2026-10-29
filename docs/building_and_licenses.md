# Licenses, corresponding source and rebuilding

[日本語](building_and_licenses_ja.md) | [User documentation](index.md)

MDS application code is MIT licensed; see `LICENSE`. Third-party libraries and
reference datasets retain their own licenses. This notice does not relicense them.

## Included libraries and your rights

The Windows installer includes Qt / PySide6 Essentials / shiboken6 (LGPLv3
option for the shipped modules), igor2 (LGPL-3.0-or-later), Gemmi and certifi
(MPL-2.0), orjson (MPL-2.0 AND (Apache-2.0 OR MIT)), and tqdm (MPL-2.0 AND MIT),
alongside other BSD, MIT, PSF and compatible components. The distribution includes
`_internal/licenses/inventory.json`, component copyright/license texts, CPython's
license, Qt's upstream third-party notices and source identities. The inventory
also includes build-tool notices for completeness; not every listed tool is shipped.

No MDS terms prohibit modification or replacement of LGPL libraries, or reverse
engineering needed to debug such modifications. You may obtain, modify, rebuild,
recombine and run the covered components under their original licenses. MDS
uses no integrity restriction that intentionally prevents a modified library from running.

## Obtain the exact corresponding source

On the [release page](https://github.com/tanabe0719/MaterialsDataStudio-public/releases/latest),
download `MaterialsDataStudio-v<version>-CorrespondingSources.zip` and
`MaterialsDataStudio-v<version>-ApplicationSource.zip` for the installer you use.
The former contains unchanged upstream source archives, including Qt Base,
Qt SVG, PySide/shiboken, igor2 and MPL-covered components. The latter contains
MDS source and build scripts. `source-manifest.json` / `packaging/corresponding_sources.json`
provide exact versions, official retrieval URLs and SHA-256 hashes. Qt third-party
code is retained in its source tree. No modifications to those upstream libraries
are made by MDS. Notices included in upstream source may describe modules/examples
that are not in the runtime application.

The release's `SHA256SUMS` verifies both archives. Source downloads are provided
with the binaries, not behind a paid support request. This repository is temporarily
private during publication preparation; access to its private releases follows
GitHub permissions. Do not distribute the prepared binary to recipients who cannot
obtain its corresponding source.

## Rebuild MDS or replace a library

Use Windows 11 x64 (Windows 10 x64 also supported), Python 3.12, `uv`, and .NET 8
SDK for Velopack packaging. The installed application itself needs neither Python
nor .NET. Extract ApplicationSource to a writable folder.

```powershell
uv sync --frozen --extra dev
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -Package
```

The script creates `.build-envs/v<digits>`, downloads and checks the pinned source
archives, collects notices from that environment, builds the app, and validates
it before creating Setup and a full Velopack package. Optional network-free
packaging is possible by placing the verified source archives in `release_assets/sources/`
and restoring the locked build environment beforehand. Pinned build dependencies
are in `uv.lock`; PyInstaller's bootloader exception applies to its generated app.

To use a modified igor2, edit its extracted `.py` sources and install that source
into the build environment using `uv pip install --python <build-python> --no-deps
--reinstall <modified-source-folder>`. For compiled libraries, build the modified
source into a compatible wheel using the upstream instructions, then install that
wheel into the same environment. Official Qt/PySide build guidance:
[Qt from source](https://doc.qt.io/qt-6/build-sources.html),
[Qt for Python](https://doc.qt.io/qtforpython-6/building_from_source/index.html).

After modifying a dependency, invoke that environment's Python directly (avoid
another `uv sync`, which would restore locked originals):

```powershell
$BuildPython = ".build-envs/v0148/Scripts/python.exe"
& $BuildPython -m scripts.collect_licenses
& $BuildPython -m PyInstaller --noconfirm --clean --workpath build_modified --distpath dist_modified packaging/materials_data_studio.spec
powershell -ExecutionPolicy Bypass -File packaging/build_installer.ps1 -PackDirectory dist_modified/MaterialsDataStudio -Version 0.14.8 -PythonExecutable $BuildPython
```

The first command must be run from the source root. Update the source manifest and
notices if redistributing modified dependencies, preserving their license duties.
You can also test ABI-compatible Qt DLL replacements in a writable copy of the
application's version directory, with MDS closed, and run its main executable.
Keep an original copy. A modified distribution is your build, not an official MDS
signed release; signing must not remove your LGPL modification rights.

## Reference data and attribution

COD / MP data are optional separate imports. They are not covered by the MDS MIT
license. See [reference database licenses](reference_databases.md), each archive's
NOTICE and the original providers' terms. MDS is an independent application and
is not endorsed by WaveMetrics, Quantum Design, COD, QualX or Materials Project.

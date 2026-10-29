# Windows installer distribution

MDS uses a self-contained **Velopack Setup.exe**, installed per user on Windows
10/11 x64. MSI is unnecessary for this per-user application. A newer Setup.exe
upgrades the existing installation. Save work and close MDS before upgrading.
There is no online update source, in-app updater, or startup auto-apply action.
Python, .NET and administrator rights are not required on the target PC.
Settings, sessions and external reference databases remain outside the application
payload. Legacy ZIP users install once with Setup; old ZIP folders are not removed.

## Build

Use native Windows x64 with Python/uv and the **.NET 8 runtime** on the build host:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -Package
```

This runs locked-environment tests, lint, typing, PyInstaller and frozen smoke tests,
then creates `release_assets/MaterialsDataStudio-v<version>-Setup.exe` and `.sha256`.
`-PortableZip` optionally creates a local legacy ZIP. No client framework download
is configured. Signing remains on hold; `-RequireSignature` rejects unsigned installers.

`packaging/velopack.json` pins the official CLI and its SHA-256. The build restores
it through `scripts/fetch_velopack.py`; no global tool installation or .NET SDK is
required. The runtime SDK is locked in `uv.lock`. Installer hooks run before Qt.
Document types register on normal GUI startup; uninstall removes only handlers
owned by that exact executable and preserves foreign handlers/default selections.

**Keep `packId` (`MaterialsDataStudio`), channel and runtime stable between versions.**
Increase versions and update the source version, Windows metadata and lock file
together. Never reuse a published version. Internal nupkg/feed files remain build
artifacts. Setup.exe, its checksum and the COD/MP reference ZIPs (including
checksums and data notices) are published together. Reference files are pinned
by name, size and SHA-256 in `packaging/reference_assets.json`.

## Verify and publish

```powershell
.venv/Scripts/python.exe scripts/validate_installer_lifecycle.py
.venv/Scripts/python.exe scripts/verify_installer.py
git tag -a v<version> -m "Materials Data Studio v<version>"
git push --atomic origin main v<version>
```

The lifecycle test uses a unique application ID and workspace directory with the
actual frozen payload in two package versions. It checks installation, offline
upgrade, same-version rerun, obsolete-file removal, retained state, installed-app
smoke tests and uninstall. Existing MDS registry values must stay unchanged.
It does not exercise an older released binary or interactive setup.

Version-tag pushes build and publish the latest GitHub Release. If Actions cannot
run, complete the same local checks and publish from the tag's exact checkout:

```powershell
./packaging/publish_installer.ps1
```

Publishing creates a draft, verifies uploaded sizes and SHA-256 digests through
GitHub's API, carries forward all pinned reference assets from the previous
release, then marks the new version latest. Only after the complete asset set
and installer checksum have been verified are older published releases removed.
Git source tags and unfinished drafts are retained. This is the default policy
unless the user requests otherwise; use `-KeepPreviousReleases` to retain releases.
Database files are reused from a verified local cache or downloaded from an older
release before it is removed. Failure leaves older releases intact.
If publication stops with an uploaded draft, `-ResumeDraft` verifies that draft's
complete asset set before publication; it never replaces a published release.
The bilingual notes template is `packaging/release_notes.md`. Update its database
filenames together with the pinned manifest when publishing a newer database.
The build runner needs sufficient disk space for the application and approximately
3.2 GB of reference downloads, plus headroom. Client support includes Windows 11 x64.

Official references: [Python integration](https://docs.velopack.io/getting-started/python),
[installer](https://docs.velopack.io/packaging/installer),
[hooks](https://docs.velopack.io/integrating/hooks).

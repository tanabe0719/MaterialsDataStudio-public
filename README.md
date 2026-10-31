# Materials Data Studio

**English** | [日本語](README_ja.md) · [Documentation](docs/index.md) · [Latest release](https://github.com/tanabe0719/MaterialsDataStudio-public/releases/latest)

A Windows desktop application for importing, comparing and analyzing XRD, MPMS
and PPMS measurements, and preparing publication figures in an independent Graph Editor.

**Current version: 0.15.1 · Windows 11 x64 and Windows 10 x64.**

Version 0.15.1 adds multi-phase Search–Match comparison, parent-linked markers
and reflection ticks, configurable reflection labels and display-only X/Y offsets
in Graph Editor. Zoom/style changes are more responsive, and reflection labels
are preserved when sending plots to Graph Editor or Igor Pro.

> This repository starts from an audited snapshot without the earlier development history.

## Install or upgrade

1. Open **[Releases → latest](https://github.com/tanabe0719/MaterialsDataStudio-public/releases/latest)** and download `MaterialsDataStudio-v<version>-Setup.exe`.
2. Close MDS, run Setup, and open **Materials Data Studio** from the Start menu.
3. To upgrade an installer-managed copy, run a newer Setup. Saved sessions, settings and external databases are retained. Python and .NET are not required on the target PC.

The prepared installer is currently **unsigned**. Compare its SHA-256 with the
release's `SHA256SUMS`. GitHub **Source code** archives and `.nupkg` files are not
the installer. Legacy portable-ZIP users should install once with Setup; the old
portable folder is not removed automatically. [Installation details](docs/installation.md).

On installed Windows copies, MDS checks for updates at startup and shows release notes when a newer version is available. Choose **Download update**, then **Restart and update**; unsaved sessions and graphs are confirmed before applying. **Later** leaves the current version running. Disable automatic checks using **Do not check for updates at startup**, and restore them through **Help → Check for updates at startup**. **Help → Check for updates…** always remains available in both the main window and Graph Editor. Downloads and installation require confirmation; startup never silently applies a downloaded package.

Versions before 0.15.0 do not have this feature: install the latest version once with Setup. Source/portable copies also use Setup. Settings, saved files and reference databases are retained; MDS does not send measurement data when checking for updates.

## Workspaces

| Workspace | Main tasks |
|---|---|
| XRD | Text/XY/DAT/RASX/CIF import, Cu Kα1/2 simulation and fitting, arPLS background, lattice adjustment, whole-pattern/Le Bail fitting, offline phase search |
| MPMS | ZFC/FC and field branches, independent visibility/style/analysis, M–T/M–H and Curie–Weiss fitting |
| PPMS | Heat capacity, low-temperature and Debye–Einstein fitting, transport/VRH, Bridge/branch organization and custom plots |
| Graph Editor | Waves/tables, XY/images and multiple axes, annotations/drawing, fitting/statistics, PXP/H5XP import, H5XP export and Send to Igor |
| Shared tools | Non-destructive sessions, Undo/Redo, Japanese/English UI and manuals, light/dark themes, data and vector/raster figure export |

MDS supports documented subsets of Igor-compatible operations; it is an independent
application, not an Igor Pro replacement or an officially endorsed product.

## Optional reference databases

Search–Match can combine **COD + MP + user CIF** and rank candidates by MDS Match
Score. No bulk reference database is inside Setup or the Git repository.

- **COD:** download `COD-Inorganic-2026-07.zip`, then use **XRD → Open Phase Search–Match → Reference Databases… → Install / Import ZIP…**.
- **MP:** the local-index reader remains available, but MDS does not currently distribute an MP database. Public redistribution is deferred pending the pre-redistribution contact described in the MP Terms. Use only a lawfully obtained index.

See [database installation and licenses](docs/reference_databases.md). Candidate
scores do not establish phase identity; verify results against your measurements.

## Manuals

| Guide | English | 日本語 |
|---|---|---|
| Main application | [User manual](docs/user_manual_en.md) | [ユーザーマニュアル](docs/user_manual.md) |
| Graph Editor | [Graph Editor manual](docs/graph_viewer_manual_en.md) | [Graph Editorマニュアル](docs/graph_viewer_manual.md) |
| Installation | [Setup and upgrades](docs/installation.md) | [導入・更新](docs/installation_ja.md) |
| Libraries and source | [Licenses / rebuilding](docs/building_and_licenses.md) | [ライセンス・再ビルド](docs/building_and_licenses_ja.md) |

The four application manuals are also available offline through **Help**. Their
screenshots use synthetic demonstration data. [Documentation index](docs/index.md).

## Source, licenses and contribution

MDS application code is **MIT**; dependencies and databases retain their own terms.
Each release provides corresponding library sources, application source, notices
and checksums. See [third-party notices](THIRD_PARTY_NOTICES.md).

For a source checkout: `uv sync --frozen --extra dev`, then `uv run materials-data-studio`.
Scientific results must be checked for units, assumptions, fit intervals and residuals.

Before any contribution or push, read [publication rules / 公開用pushルール](docs/maintainers/publication_policy.md).
[Security reporting](SECURITY.md) · [Maintainer release/update design](docs/maintainers/releases.md).

# Install, upgrade and verify

[日本語](installation_ja.md) | [Documentation](index.md)

## Requirements

Windows 11 x64 or Windows 10 x64. The installer is per user; Python, a Python
environment, Igor Pro and .NET do not need to be installed to run MDS. Sending
files to Igor requires your own licensed Igor installation.

## Installation and upgrades

1. Download `MaterialsDataStudio-v<version>-Setup.exe` from the [latest release](https://github.com/tanabe0719/MaterialsDataStudio-public/releases/latest).
2. Save sessions and close MDS. Run Setup, then launch MDS from the Start menu.
3. For later versions, run the newer Setup. Installer-managed installations are
   upgraded with the same package identity. Settings, saved sessions and optional
   reference databases remain separate from application files.

The current installer is unsigned. Verify its hash against
`SHA256SUMS`; a hash alone is not a verified publisher signature. Do not treat a
Windows security prompt as proof of identity. Older portable ZIP folders are not
removed by Setup. Save your work before switching between installations.

## Pick the right asset

| Asset | Purpose |
|---|---|
| `...Setup.exe` | Install or manually upgrade MDS |
| `COD-....zip` | Optional COD reference database |
| MP database | Not distributed; use only a lawfully obtained local index |
| `...Manuals.zip` | Optional standalone offline HTML manuals, English and Japanese |
| `...ApplicationSource.zip` / `...CorrespondingSources.zip` | MDS source and third-party corresponding source |
| `SHA256SUMS` | SHA-256 checksums for release assets |
| `...full.nupkg` / `releases.win.json` | Maintainer/updater files; do not run them as installers |

For example, `Get-FileHash -Algorithm SHA256 .\MaterialsDataStudio-v0.15.1-Setup.exe`.
Compare the result with the matching line in `SHA256SUMS`.

## In-app updates

On installed Windows copies, MDS checks for updates at startup and shows release notes when a newer version is available. Choose **Download update**, then **Restart and update**; unsaved sessions and graphs are confirmed before applying. **Later** leaves the current version running. Disable automatic checks using **Do not check for updates at startup**, and restore them through **Help → Check for updates at startup**. **Help → Check for updates…** always remains available in both the main window and Graph Editor. Downloads and installation require confirmation; startup never silently applies a downloaded package.

Versions before 0.15.0 do not have this feature: install the latest version once with Setup. Source/portable copies also use Setup. Settings, saved files and reference databases are retained; MDS does not send measurement data when checking for updates.
[Install optional databases](reference_databases.md).

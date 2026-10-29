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
`PUBLIC-SHA256SUMS` (v0.14.7); a hash alone is not a verified publisher signature. Do not treat a
Windows security prompt as proof of identity. Older portable ZIP folders are not
removed by Setup. Save your work before switching between installations.

## Pick the right asset

| Asset | Purpose |
|---|---|
| `...Setup.exe` | Install or manually upgrade MDS |
| `COD-....zip` | Optional COD reference database |
| Both `MaterialsProject-...part1/2...zip` | Optional MP reference database; import with MDS |
| `...Manuals.zip` | Optional standalone offline HTML manuals, English and Japanese |
| `...ApplicationSource.zip` / `...CorrespondingSources.zip` | MDS source and third-party corresponding source |
| `PUBLIC-SHA256SUMS` (v0.14.7) | SHA-256 checksums for release assets |
| `...full.nupkg` / `releases.win.json` | Maintainer/updater files; do not run them as installers |

For example, `Get-FileHash -Algorithm SHA256 .\MaterialsDataStudio-v0.14.7-Setup.exe`.
Compare the result with the matching line in `PUBLIC-SHA256SUMS` (v0.14.7).

Online/in-app update acquisition is disabled. The release layout is prepared
for a future updater, with a full package available for direct upgrades.
[Install optional databases](reference_databases.md).

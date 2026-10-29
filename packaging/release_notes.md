# Materials Data Studio v{{VERSION}}

[English README](https://github.com/{{REPOSITORY}}/blob/main/README.md) · [日本語README](https://github.com/{{REPOSITORY}}/blob/main/README_ja.md)

## English

**Windows 11 x64 / Windows 10 x64 · Unsigned installer.** Close MDS and run the newer Setup to upgrade. Settings, saved sessions and external databases are retained. Python and .NET are not required. Online/in-app update acquisition remains disabled.

**[Download Setup](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Setup.exe)** · **[Download COD database](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/COD-Inorganic-2026-07.zip)** · [Offline manuals](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Manuals.zip) · [SHA-256 checksums](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/{{CHECKSUMS}})

### Security maintenance and installation

- Updates Pillow to 13.0.0, certifi to 2026.10.23, urllib3 to 2.8.0 and setuptools to 84.2.1, addressing the 46 dependency alerts reported for the previous lockfile. Setup is rebuilt with updated dependencies, notices and matching sources.
- Includes two reviewed synthetic H5XP test fixtures omitted by the original ignore rules. Publication checks now reject missing/untracked or altered fixtures. Analysis features and UI layout are retained.
- Setup is **unsigned**: compare its SHA-256 with `{{CHECKSUMS}}`. A checksum verifies content, not publisher identity. Source code ZIPs and `.nupkg` are not installers. Legacy portable users should install once with Setup.
- COD is optional: import its ZIP through **XRD → Open Phase Search–Match → Reference Databases… → Install / Import ZIP…**. COD data are unchanged; retain CC BY 4.0 attribution.
- **MP database ZIPs are not distributed**, pending the pre-redistribution contact described in MP Terms. Lawfully obtained local MP indexes remain supported. This notice supersedes older MP download instructions.

### Other assets

| Asset | Purpose |
|---|---|
| `...ApplicationSource.zip` | Matching MDS source, build scripts and synthetic test fixtures |
| `...CorrespondingSources.zip` | Exact upstream sources, including LGPL/MPL components |
| `...Manuals.zip` | Offline English/Japanese application and Graph Editor manuals and guides |
| `...full.nupkg`, `releases.win.json` | Complete Velopack package/feed; online acquisition remains disabled |
| `REFERENCE_DATABASE_NOTICES*.txt` | Database sources, licenses and attribution |
| `release-manifest.json`, `{{CHECKSUMS}}` | Asset inventory and SHA-256 verification |
| Versioned v0.14.7 source ZIPs | Preserved application/corresponding source and synthetic fixture supplement |

MDS application code is MIT; dependencies and data retain their own licenses. MDS Match Score does not prove phase identity. [Installation details](https://github.com/{{REPOSITORY}}/blob/main/docs/installation.md).

## 日本語

**Windows 11 x64 / Windows 10 x64対応・未署名インストーラ。** MDSを終了して新しいSetupを実行すると更新できます。設定・保存したセッション・外部DBは保持します。Pythonと.NETは不要です。オンライン・アプリ内での更新取得は無効のままです。

**[Setupをダウンロード](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Setup.exe)** · **[CODのDBをダウンロード](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/COD-Inorganic-2026-07.zip)** · [オフラインマニュアル](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Manuals.zip) · [SHA-256一覧](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/{{CHECKSUMS}})

### セキュリティ保守と導入

- Pillow 13.0.0、certifi 2026.10.23、urllib3 2.8.0、setuptools 84.2.1へ更新し、旧lockfileに対して報告された46件の依存関係の警告に対応します。更新した依存・告知・対応ソースを用いてSetupを再作成しています。
- Gitの除外設定で漏れた、確認済みの合成H5XP試験ファイル2点を収録しました。欠落・追跡漏れ・変更を公開用検査で検出します。解析機能とUIの配置は維持します。
- Setupは**未署名**です。`{{CHECKSUMS}}` とSHA-256を照合してください。hashは内容の照合用で、発行者の本人確認ではありません。Source code ZIPと `.nupkg` は通常のインストーラではありません。旧portable利用者はSetupで一度導入してください。
- CODは任意です。ZIPを **XRD → Open Phase Search–Match → Reference Databases… → Install / Import ZIP…** から導入します。DB本体は変更せず、CC BY 4.0の出典表示を保持しています。
- **MPのDB ZIPは配布しません。** 規約にある再配布前の連絡を行うまで保留します。適法に入手したローカルMP索引の読込機能は維持します。旧MPダウンロード案内より、この説明を優先してください。

### その他のAssets

| ファイル | 用途 |
|---|---|
| `...ApplicationSource.zip` | 対応するMDSソース、ビルド手順、合成テストファイル |
| `...CorrespondingSources.zip` | LGPL/MPL対象を含む、版を固定した公式ソース |
| `...Manuals.zip` | 日英のアプリ・Graph Editorマニュアルとガイド |
| `...full.nupkg`, `releases.win.json` | Velopackのfull更新パッケージとfeed。オンライン取得は無効 |
| `REFERENCE_DATABASE_NOTICES*.txt` | DBの出典・ライセンス・帰属表示 |
| `release-manifest.json`, `{{CHECKSUMS}}` | 配布一覧とSHA-256 |
| v0.14.7の版付きソースZIP | 旧版のMDS・対応ソースと合成fixture補足の保持 |

MDS自作部分はMITで、外部依存とデータには個別条件が適用されます。MDS Match Scoreだけで相の存在を確定しないでください。[詳しい導入手順](https://github.com/{{REPOSITORY}}/blob/main/docs/installation_ja.md)。

# Materials Data Studio v{{VERSION}}

[English README](https://github.com/{{REPOSITORY}}/blob/main/README.md) · [日本語README](https://github.com/{{REPOSITORY}}/blob/main/README_ja.md)

## English

**Windows 11 x64 / Windows 10 x64.** This audited release carries forward the current 0.14.7 application and bilingual manuals without the old development history. The installer is unsigned; verify its SHA-256 before use. Online update acquisition remains disabled.

### Install or upgrade

Download **[Download Setup](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Setup.exe)**, save your work, close MDS and run Setup. Python and .NET are not required. A newer installer upgrades an installer-managed copy while retaining settings, sessions and external databases. Legacy portable users should install once with Setup. GitHub Source code and `.nupkg` files are not the installer.

### Optional databases

- COD: import [COD database ZIP](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/COD-Inorganic-2026-07.zip) using **Reference Databases → Install / Import ZIP**.
- MP database downloads are withdrawn pending the pre-redistribution contact required by MP Terms. The application still supports lawfully obtained local MP indexes. Older bundled manual instructions about downloading MP ZIPs are superseded by this notice.
- The database payloads are unchanged; bilingual source/license notices are included. The current MP Terms have been reviewed; MP database redistribution is deferred.

### Other assets

| Asset | Purpose |
|---|---|
| [`{{CHECKSUMS}}`](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/{{CHECKSUMS}}) | Verify every listed asset |
| [Manuals ZIP](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Manuals.zip) | Offline English/Japanese manuals; use this page for current DB availability |
| `...ApplicationSource.zip` | Audited MDS source and build scripts |
| `...CorrespondingSources.zip` | Version-pinned upstream sources, including LGPL/MPL components |
| `...full.nupkg`, `releases.win.json` | Prepared full-package update feed; acquisition is not enabled |
| `REFERENCE_DATABASE_NOTICES*.txt` | Database sources, licenses and attribution |
| `reference-payload-verification.json` | Evidence that reference payloads were preserved |

The existing XRD/MPMS/PPMS/Graph Editor features are retained, including View-menu layout controls, range-limited PPMS fits, Custom Plot manual branches and themed Search–Match. This baseline adds complete collected library notices, accessible source/build guidance and immutable publication safeguards. MDS Match Score is not proof of phase identity.

## 日本語

**Windows 11 x64 / Windows 10 x64対応。** 以前の開発履歴を含まない、監査済みのReleaseです。現行0.14.7の機能と日英マニュアルを引き継いでいます。インストーラは未署名のためSHA-256を照合してください。オンライン更新の取得は未実装です。

### インストール・更新

**[Download Setup](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Setup.exe)** を取得し、作業を保存してMDSを終了してから実行します。Pythonと.NETは不要です。インストーラ導入済み環境は新しい版のSetupで更新でき、設定・セッション・外部DBを保持します。旧portable利用者はSetupで一度導入してください。GitHubのSource codeと `.nupkg` は通常のインストーラではありません。

### 任意のDB

- COD：[COD database ZIP](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/COD-Inorganic-2026-07.zip) を **Reference Databases → Install / Import ZIP** で導入します。
- MP：規約が求める再配布前の連絡が未実施のため、DBのダウンロードを取り下げています。適法に入手したローカルMP索引の読込機能は維持しています。同梱の旧マニュアルにあるMPのダウンロード案内より、この説明を優先してください。
- DB本体は変更せず、出典とライセンスの日英告知を追加しています。MP現行規約は確認済みで、MPのDB再配布は保留しています。

### その他のAssets

| ファイル | 用途 |
|---|---|
| [`{{CHECKSUMS}}`](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/{{CHECKSUMS}}) | 各ファイルのhash照合 |
| [マニュアルZIP](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Manuals.zip) | 日英オフラインマニュアル。DB配布状況はこのページを優先 |
| `...ApplicationSource.zip` | 監査したMDSソースとビルド手順 |
| `...CorrespondingSources.zip` | LGPL/MPL対象を含む、版を固定した公式ソース |
| `...full.nupkg`, `releases.win.json` | 将来の更新用fullパッケージとfeed。取得機能は無効 |
| `REFERENCE_DATABASE_NOTICES*.txt` | DBの出典・条件・帰属表示 |
| `reference-payload-verification.json` | DB本体の保持を確認した記録 |

XRD・MPMS・PPMS・Graph Editorの既存機能、Viewメニュー、範囲内fit、Custom Plotの手動ブランチ、Search–Matchの配色を維持しています。ライブラリ告知の収集、対応ソース・再ビルド案内、上書きを禁止する公開用ルールを追加しました。MDS Match Scoreだけで相の存在を確定しないでください。

## Distribution inventory / 配布ファイル一覧

MP database ZIPs and their checksums were withdrawn before public visibility at the owner’s request. The original private-preparation inventory/checksum files were retired; `public-release-manifest.json` and `{{CHECKSUMS}}` describe the remaining downloads. Setup, the full update package, original matching source archives, and the v{{VERSION}} tag are unchanged.

所有者の指示によりpublic化前にMPのDB ZIP・個別hashを撤去し、それらを含む旧一覧を終了しました。現在の一覧は `public-release-manifest.json` と `{{CHECKSUMS}}` です。Setup・full更新パッケージ・対応するソースZIP・v{{VERSION}}のtagは変更していません。

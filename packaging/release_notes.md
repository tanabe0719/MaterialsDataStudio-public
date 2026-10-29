# Materials Data Studio v{{VERSION}}

[English README](https://github.com/{{REPOSITORY}}/blob/main/README.md) · [日本語README](https://github.com/{{REPOSITORY}}/blob/main/README_ja.md)

## English

**Windows 11 x64 / Windows 10 x64.** This private publication-preparation release carries forward the current 0.14.7 application and bilingual manuals without the old development history. The installer is unsigned; verify its SHA-256 before use. Online update acquisition remains disabled.

### Install or upgrade

Download **`MaterialsDataStudio-v{{VERSION}}-Setup.exe`**, save your work, close MDS and run Setup. Python and .NET are not required. A newer installer upgrades an installer-managed copy while retaining settings, sessions and external databases. Legacy portable users should install once with Setup. GitHub Source code and `.nupkg` files are not the installer.

### Optional databases

- COD: import `COD-Inorganic-2026-07.zip` using **Reference Databases → Install / Import ZIP**.
- MP: save both `MaterialsProject-2026-09-28-part1-of2.zip` and `part2-of2.zip` together, keep their names, and select either using **Import MP Index**. Do not join/extract manually.
- The database payloads are unchanged; bilingual source/license notices are included. MP's final current-Terms review is pending before public visibility.

### Other assets

| Asset | Purpose |
|---|---|
| `SHA256SUMS` | Verify every listed asset |
| `...Manuals.zip` | Four offline HTML manuals in English/Japanese |
| `...ApplicationSource.zip` | Audited MDS source and build scripts |
| `...CorrespondingSources.zip` | Version-pinned upstream sources, including LGPL/MPL components |
| `...full.nupkg`, `releases.win.json` | Prepared full-package update feed; acquisition is not enabled |
| `REFERENCE_DATABASE_NOTICES*.txt` | Database sources, licenses and attribution |
| `reference-payload-verification.json` | Evidence that reference payloads were preserved |

The existing XRD/MPMS/PPMS/Graph Editor features are retained, including View-menu layout controls, range-limited PPMS fits, Custom Plot manual branches and themed Search–Match. This baseline adds complete collected library notices, accessible source/build guidance and immutable publication safeguards. MDS Match Score is not proof of phase identity.

## 日本語

**Windows 11 x64 / Windows 10 x64対応。** 以前の開発履歴を含まない、新しいprivate公開準備用Releaseです。現行0.14.7の機能と日英マニュアルを引き継いでいます。インストーラは未署名のためSHA-256を照合してください。オンライン更新の取得は未実装です。

### インストール・更新

**`MaterialsDataStudio-v{{VERSION}}-Setup.exe`** を取得し、作業を保存してMDSを終了してから実行します。Pythonと.NETは不要です。インストーラ導入済み環境は新しい版のSetupで更新でき、設定・セッション・外部DBを保持します。旧portable利用者はSetupで一度導入してください。GitHubのSource codeと `.nupkg` は通常のインストーラではありません。

### 任意のDB

- COD：`COD-Inorganic-2026-07.zip` を **Reference Databases → Install / Import ZIP** で導入します。
- MP：`MaterialsProject-2026-09-28-part1-of2.zip` と `part2-of2.zip` を同じフォルダへ保存し、名前を変えず **Import MP Index** でどちらかを選びます。手動の結合・展開は不要です。
- DB本体は変更せず、出典とライセンスの日英告知を追加しています。public化前のMP現行規約の最終照合は未完了です。

### その他のAssets

| ファイル | 用途 |
|---|---|
| `SHA256SUMS` | 各ファイルのhash照合 |
| `...Manuals.zip` | 日英4つのオフラインHTMLマニュアル |
| `...ApplicationSource.zip` | 監査したMDSソースとビルド手順 |
| `...CorrespondingSources.zip` | LGPL/MPL対象を含む、版を固定した公式ソース |
| `...full.nupkg`, `releases.win.json` | 将来の更新用fullパッケージとfeed。取得機能は無効 |
| `REFERENCE_DATABASE_NOTICES*.txt` | DBの出典・条件・帰属表示 |
| `reference-payload-verification.json` | DB本体の保持を確認した記録 |

XRD・MPMS・PPMS・Graph Editorの既存機能、Viewメニュー、範囲内fit、Custom Plotの手動ブランチ、Search–Matchの配色を維持しています。ライブラリ告知の収集、対応ソース・再ビルド案内、上書きを禁止する公開用ルールを追加しました。MDS Match Scoreだけで相の存在を確定しないでください。

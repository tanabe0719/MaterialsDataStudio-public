# Materials Data Studio

[English](README.md) | **日本語** · [文書一覧](docs/index.md) · [最新版](https://github.com/tanabe0719/MaterialsDataStudio-public/releases/latest)

XRD・MPMS・PPMSの測定データを読み込み、比較・解析し、独立したGraph Editorで発表・論文用の図を作るWindowsアプリです。

**現行版：0.14.7 · Windows 11 x64 / Windows 10 x64対応。**

> 監査済みの現行版を起点とし、以前の開発履歴は含みません。

## インストール・更新

1. **[Releasesの最新版](https://github.com/tanabe0719/MaterialsDataStudio-public/releases/latest)**から `MaterialsDataStudio-v<version>-Setup.exe` を取得します。
2. MDSを終了してSetupを実行し、スタートメニューの **Materials Data Studio** を開きます。
3. インストーラ導入済みの環境では、新しい版のSetupで更新できます。保存済みセッション・設定・外部DBは保持されます。実行PCにPythonや.NETは不要です。

現在のインストーラは**未署名**です。Releaseの `PUBLIC-SHA256SUMS` (v0.14.7) でSHA-256を照合できます。
GitHubの **Source code** と `.nupkg` は通常のインストーラではありません。
従来のportable ZIP利用者は、Setupで一度インストールしてください。古いportableフォルダは自動削除しません。
[詳しい導入手順](docs/installation_ja.md)。

**オンライン更新の取得は未実装です。** 将来の対応に備え、ReleaseにはVelopackのfullパッケージとfeedも収録します。現在の更新はSetupで行います。

## 主な作業画面

| 画面 | できること |
|---|---|
| XRD | text/XY/DAT/RASX/CIF読込、Cu Kα1/2シミュレーションとfit、arPLS背景、格子調整、全パターン・Le Bail fit、ローカル相検索 |
| MPMS | ZFC/FC・磁場別ブランチ、ブランチ別の表示・スタイル・解析、M–T/M–H、Curie–Weiss fit |
| PPMS | 比熱、低温・Debye–Einstein fit、輸送・VRH、Bridge／ブランチ整理、Custom Plot |
| Graph Editor | wave・table、XY・画像・複数軸、注記・描画、fit・統計、PXP/H5XP読込、H5XP出力とSend to Igor |
| 共通機能 | 元データを保持するセッション、Undo/Redo、日英UI・マニュアル、ライト／ダーク、データとベクター／画像出力 |

Igor互換操作は文書化した範囲に対応します。MDSは独立したアプリであり、Igor Proの代替を保証する製品や公認製品ではありません。

## 任意の参照データベース

Search–Matchは **COD + MP + User CIF** をまとめて検索し、MDS Match Score順に表示できます。
SetupとGitリポジトリに大容量の参照DB本体は含みません。

- **COD**：`COD-Inorganic-2026-07.zip` を取得し、**XRD → Open Phase Search–Match → Reference Databases… → Install / Import ZIP…** で読み込みます。
- **MP**：ローカル索引の読込機能は維持していますが、MDSからMPのDBは配布しません。規約にある再配布前の連絡を行うまで一般配布を保留します。適法に入手した索引のみ使用してください。

[DBの導入とライセンス](docs/reference_databases_ja.md)を確認してください。スコアだけで相の存在を確定せず、測定結果と照合してください。

## マニュアル

| ガイド | 日本語 | English |
|---|---|---|
| メインアプリ | [ユーザーマニュアル](docs/user_manual.md) | [User manual](docs/user_manual_en.md) |
| Graph Editor | [Graph Editorマニュアル](docs/graph_viewer_manual.md) | [Graph Editor manual](docs/graph_viewer_manual_en.md) |
| 導入・更新 | [インストール手順](docs/installation_ja.md) | [Setup and upgrades](docs/installation.md) |
| ライブラリとソース | [ライセンス・再ビルド](docs/building_and_licenses_ja.md) | [Licenses / rebuilding](docs/building_and_licenses.md) |

アプリの4つのマニュアルは **Help** からオフラインでも開けます。掲載画像は合成データを使用しています。[文書一覧](docs/index.md)。

## ソース・ライセンス・開発参加

MDS自作部分は **MIT** です。外部ライブラリとDBには、それぞれの条件が適用されます。
Releaseには対応ライブラリソース、MDSソース、告知とチェックサムを用意します。[第三者ライセンスの案内](THIRD_PARTY_NOTICES.md)。

ソースから起動する場合：`uv sync --frozen --extra dev` の後、`uv run materials-data-studio`。
解析結果は単位・前提・fit範囲・残差を確認してください。

変更・pushの前に [公開用pushルール / Publication rules](docs/maintainers/publication_policy.md)を確認してください。
[脆弱性の連絡](SECURITY.md) · [開発者向けRelease・更新設計](docs/maintainers/releases.md)。

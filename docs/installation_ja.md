# インストール・更新・検証

[English](installation.md) | [文書一覧](index.md)

## 動作環境

Windows 11 x64 / Windows 10 x64。ユーザー単位でインストールします。
アプリの実行にPython環境、Igor Pro、.NETは不要です。Send to Igorを使用する場合は、利用者自身のライセンス済みIgorが必要です。

## 導入と更新

1. [最新版Release](https://github.com/tanabe0719/MaterialsDataStudio-public/releases/latest)から `MaterialsDataStudio-v<version>-Setup.exe` を取得します。
2. セッションを保存してMDSを終了し、Setupを実行します。スタートメニューからMDSを起動します。
3. 次の版へは新しいSetupを実行して更新します。パッケージの識別子は維持され、インストーラ導入済みの環境を更新できます。
   設定・セッション・任意の参照DBはアプリ本体と分離して保持します。

現在のインストーラは未署名です。`SHA256SUMS` でファイルのSHA-256を照合できますが、hashだけで発行者の署名を確認できるわけではありません。
Windowsの警告を発行者の証明として扱わないでください。従来のportable ZIPフォルダはSetupで自動削除しません。
インストール先を切り替える前に作業を保存してください。

## Assetsの選び方

| ファイル | 用途 |
|---|---|
| `...Setup.exe` | MDSの導入・手動更新 |
| `COD-....zip` | 任意のCOD参照DB |
| `MaterialsProject-...part1/2...zip` の両方 | 任意のMP参照DB。MDS内でインポート |
| `...Manuals.zip` | 日英のオフラインHTMLマニュアル |
| `...ApplicationSource.zip` / `...CorrespondingSources.zip` | MDS本体と外部ライブラリの対応ソース |
| `SHA256SUMS` | ReleaseファイルのSHA-256一覧 |
| `...full.nupkg` / `releases.win.json` | 開発者・将来のupdater用。通常の導入には使いません |

例：`Get-FileHash -Algorithm SHA256 .\MaterialsDataStudio-v0.14.8-Setup.exe`。
表示された値を `SHA256SUMS` の該当行と比較します。

オンライン・アプリ内の更新取得は未実装です。将来の対応に備え、Releaseにはfull更新パッケージを配置します。
[任意のDBの導入](reference_databases_ja.md)。

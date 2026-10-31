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
| MPのDB | 配布は保留中。適法に入手したローカル索引のみ利用 |
| `...Manuals.zip` | 日英のオフラインHTMLマニュアル |
| `...ApplicationSource.zip` / `...CorrespondingSources.zip` | MDS本体と外部ライブラリの対応ソース |
| `SHA256SUMS` | ReleaseファイルのSHA-256一覧 |
| `...full.nupkg` / `releases.win.json` | 開発者・将来のupdater用。通常の導入には使いません |

例：`Get-FileHash -Algorithm SHA256 .\MaterialsDataStudio-v0.15.1-Setup.exe`。
表示された値を `SHA256SUMS` の該当行と比較します。

## ソフト内での更新

Windowsのインストーラ導入版では、起動時に更新を確認し、新しい版があればリリースノートと選択肢を表示します。**更新をダウンロード** の後、**再起動して更新** を選びます。適用前に未保存のセッション・グラフを確認し、**後で** を選べば現在の版を使い続けられます。**今後は起動時にアップデートを確認しない** で自動確認を止め、**Help → 起動時にアップデートを確認** で再度有効にできます。メイン画面・Graph Editorの **Help → アップデートを確認…** は無効化後も利用できます。ダウンロードと適用には確認が必要で、起動時に黙って更新を適用しません。

0.15.0より前の版にはこの機能がないため、最初の導入には最新版のSetupを使用してください。ソース版・portable版もSetupで導入します。設定・保存ファイル・参照DBは保持され、更新確認で測定データを送信しません。
[任意のDBの導入](reference_databases_ja.md)。

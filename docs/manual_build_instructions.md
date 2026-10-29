# ユーザーマニュアル再生成手順

この手順はマニュアル用の合成データ、実UIスクリーンショット、HTML、PDFを再生成する。
アプリケーション本体のソースや設定は変更しない。コマンドはリポジトリ直下から実行する。

## 前提

- Windows 上で Materials Data Studio の開発環境が構築済みであること。
- Python 環境にプロジェクト本体と development dependencies が導入済みであること。
- PDF生成には Microsoft Edge または Google Chrome が必要。
- 撮影中はマウス・キーボード操作をせず、完了まで待つこと。
- 実ユーザー設定を避ける場合は `MDS_SETTINGS_DIRECTORY` と
  `MDS_RECOVERY_DIRECTORY` を撮影専用の作業ディレクトリに設定する。

リポジトリ同梱の `uv` を使う場合は、必要に応じてキャッシュをリポジトリ内へ設定する。

```powershell
$env:UV_CACHE_DIR = (Resolve-Path ".uv-cache")
```

以下の例で `uv` が PATH にない場合は、`uv` を `.\.tools\bin\uv.exe` に置き換える。

## 1. 合成デモデータ

```powershell
uv run python scripts/generate_manual_demo_data.py
```

出力先は `docs/demo_data`。過去にアップロードされた測定ファイルは参照しない。
生成物は匿名の合成データで、物理標準や性能評価には使用できない。

## 2. スクリーンショット

```powershell
uv run python scripts/capture_manual_screenshots.py
```

Windows ではスクリプトが Qt の `windows` platform plugin を選び、日本語・英語フォントを
通常の画面と同じように描画する。Linuxなど画面のない環境では `offscreen` を用いる。
出力先は `docs/screenshots`、固定画面サイズは原則 1440 × 900 px。

Preferences、セッション／データ出力ダイアログ、アプリ内マニュアル、
File／Tools／Helpメニュー、およびTools画面だけを
撮り直す場合:

```powershell
uv run python scripts/capture_manual_screenshots.py --dialogs-only
```

XRD の全体表示画像 `01`、`02`、`03`、`05` だけを撮り直す場合:

```powershell
uv run python scripts/capture_manual_screenshots.py --xrd-overview-only
```

このモードは保存直前に x・y 軸の `View All` 相当処理を実行し、撮影中のウィンドウへの
マウス入力を無効化する。

撮影内容とファイル名の対応は `docs/screenshot_plan.md` を参照する。スクリプトは実際の
parser、widget、fit処理を使い、空画面ではなく計算済みの状態を撮影する。
v0.9.7の設定カテゴリは対象カードに合わせて自動選択し、非同期計算の完了を待つ。

## 3. 印刷用HTML

v0.10.5の実行時dialogは`python scripts/capture_command_manual.py --igor-dialogs-only`で
72・74・75・76を、`--igor-file-only`で74だけを更新できます。常に合成入力/メッセージを使い、
研究用IPFの内容やパス、測定値は含めません。

```powershell
uv run python scripts/build_manual.py
```

`docs/user_manual.md` と PNG を読み込み、画像を埋め込んだ自己完結HTML
`dist_manual/Materials_Data_Studio_User_Manual.html` を生成する。変換器は標準ライブラリのみを
使用し、アプリの依存関係を増やさない。

現在のスクリプトは英語版 `user_manual_en.md` からも独立したHTMLを生成する。
v0.10.6以降はメイン用2言語に加え、`graph_viewer_manual.md` と
`graph_viewer_manual_en.md` から詳細なViewerガイドを生成する。ViewerのHelpから開く。
`build_manual.py` と `build_manual_pdfs.ps1` は4冊を一括生成し、`check_manual.py` は
4冊の参照画像とUI用語をそれぞれの役割に応じて検証する。
Viewer用PDFだけを更新する場合は `scripts/build_manual_pdfs.ps1 -Manual graph`、
メイン用だけなら `-Manual main` を指定できる。省略時は従来どおり4冊を生成する。
PXP Viewerの追加画面（34:画像、35:マーカー/ラベル、36:オフセット/注記）は、研究データを使わず
`scripts/capture_pxp_manual.py` の合成配列で撮影する。WindowsではQtの`windows` platformを
用い、Fusion/lightテーマで撮影する（`offscreen`ではUIフォントが欠ける環境がある）。

## 4. PDF

Google Chrome の例（このリポジトリの完成版PDFで検証済み）:

```powershell
$browser = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$html = (Resolve-Path "dist_manual\Materials_Data_Studio_User_Manual.html").Path
$pdf = Join-Path (Resolve-Path "dist_manual").Path "Materials_Data_Studio_User_Manual.pdf"
& $browser --headless=new --disable-gpu --no-pdf-header-footer --run-all-compositor-stages-before-draw --virtual-time-budget=10000 "--print-to-pdf=$pdf" "file:///$($html -replace '\\','/')"
```

Chrome が別の場所にある場合は `$browser` を変更する。Microsoft Edge でも同じ
`--headless=new`、`--no-pdf-header-footer`、`--print-to-pdf` オプションを使用できる。
生成後、PDFを開いて最低限次を目視確認する。

1. 表紙タイトルと日本語が文字化けしていない。
2. スクリーンショットが欠けず、縦横比が正しい。
3. 見出しだけがページ末尾へ孤立していない。
4. 表が用紙幅からはみ出していない。
5. 最終ページまで本文が存在する。

## 5. 自動検証

PDFを生成した後に実行する。

```powershell
uv run python scripts/check_manual.py
uv run pytest
uv run ruff check .
uv run pyright
```

`check_manual.py` は参照画像、PNG寸法、撮影計画、主要GUIラベル、個人パスやプレースホルダー、
HTML/PDFの存在と基本形式を確認する。画像内の見切れ、物理的な説明の妥当性、ページ組版は
目視確認も必要である。

## 更新時のチェックリスト

v0.9.5のCommand/Procedure、独立表示、DAT読取、専用Help、GUIコマンド記録の画面57-62は
`python scripts/capture_command_manual.py`で合成データのみから
撮影する。Windowsでは`QT_QPA_PLATFORM=windows`としてフォントが正しく表示されることを確認する。

v0.9.2のPXP/Graph Editor画面34–56は`python scripts/capture_pxp_manual.py`で
合成配列から撮影する。元PXPは不要。Windowsでは`QT_QPA_PLATFORM=windows`を使い、
撮影後に34–37とEditorのProperties領域を目視確認する。

- `src/materials_data_studio/version.py` のバージョンを本文へ反映した。
- GUIの英語ラベルを実画面どおりに記載した。
- 未実装機能を操作可能として説明していない。
- 数式、単位、閾値、fit方式を実装とテストへ照合した。
- 新しい画面は `docs/screenshot_plan.md` に目的と撮影条件を追記した。
- スクリーンショットには個人名、実験試料名、ユーザーフォルダーを含めていない。
- ファイル選択の撮影では `scripts/manual_privacy.py` のcapture専用処理で
  所在地を `Demo data` 表示にし、ユーザー名を含むサイドバーを隠す。
  `capture_command_manual.py --igor-file-only` はこの処理を必ず実行する。
  Qtの部品名が変わり匿名化できない場合は撮影を中止する。
- デモデータを変更した場合は、全スクリーンショット、HTML、PDFを再生成した。
- `pytest`、`ruff`、`pyright`、マニュアル検証が完了した。

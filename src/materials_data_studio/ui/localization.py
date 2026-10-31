"""Optional Japanese UI captions; scientific codes/data are never translated."""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTabWidget,
    QWidget,
)

from materials_data_studio.core.settings import create_settings

JA = {
    "File": "ファイル", "Tools": "ツール", "Help": "ヘルプ", "Edit": "編集", "Data": "データ", "Graph": "グラフ", "Table": "表", "View": "表示",
    "New Session": "新規セッション", "Open Session": "セッションを開く", "Save Session": "セッションを保存", "Save Session As": "名前を付けてセッションを保存",
    "Import Data": "データを読み込む", "Export Data": "データを出力", "Export Figure": "図を出力", "Send to Igor Pro": "Igor Proへ転送", "Undo": "元に戻す", "Redo": "やり直す",
    "Licenses and Source (English)": "ライセンス・対応ソース（英語）", "Preferences": "環境設定", "About": "このソフトについて", "Context help": "現在の機能のヘルプ", "Create Diagnostic Bundle": "診断情報を作成",
    "Open Igor Experiment (.pxp)": "Igor実験ファイルを開く (.pxp)", "Open Graph Document (.mdsg)": "グラフ文書を開く (.mdsg)", "Save Graph Document": "グラフ文書を保存", "Save Graph Document As": "名前を付けてグラフ文書を保存",
    "Edit current plot in Graph Editor": "現在の図をGraph Editorで編集", "Analysis Workbench…": "解析ワークベンチ…", "Recovery Center…": "作業の復旧…", "Recent files / resume workspace": "最近使ったファイル / 作業を再開", "Clear recent list": "最近使ったファイルの一覧を消去",
    "User Manual (English)": "ユーザーマニュアル（英語）", "Graph Editor / Igor Import": "グラフ編集 / Igor読み込み", "Molecular Weight Calculator": "分子量計算", "Shannon Radii": "Shannonイオン半径", "RASX Converter": "RASX変換",
    "Datasets": "データセット", "Interactive Plot": "対話型グラフ", "Style": "書式", "Analysis": "解析", "Export": "出力", "Filter datasets…": "名前・タグで絞り込む…", "Find a setting in this category…": "このカテゴリの設定を検索…",
    "Duplicate": "複製", "Rename": "名前の変更", "Remove": "削除", "Apply Gradient": "グラデーションを適用", "Custom…": "カスタム…", "Color": "色", "Line style": "線種", "Line width": "線の太さ", "Marker": "マーカー", "Marker size": "マーカーサイズ", "Marker fill": "マーカーの塗色", "Legend text": "凡例の文字", "Show legend": "凡例を表示",
    "Axis": "軸", "Processing": "処理", "Display axis": "表示する軸", "Logarithmic intensity axis": "強度軸を対数表示", "Wavelength preset": "波長プリセット", "Wavelength (Å)": "波長 (Å)", "Apply Wavelength to All": "全データに波長を適用", "Normalization": "規格化", "Apply Normalization to All": "全データに規格化を適用", "Scale": "倍率", "Offset": "オフセット", "Stacking": "積み重ね表示", "Stack offset": "積み重ね間隔", "Dataset Style": "選択データの書式", "Figure Export": "図の出力設定",
    "Estimate Background": "背景を推定", "Apply Background": "背景を適用", "Clear Background": "背景を解除", "arPLS Background": "arPLS背景", "Lattice Parameters": "格子定数", "Site occupancy": "サイト占有率", "Atom site": "原子サイト", "Occupancy": "占有率", "Relative scale": "相対倍率", "Edit CIF": "CIFを編集", "Open Peak Fitting": "ピークフィッティングを開く", "CIF-Constrained Peak Fitting": "CIF制約付きピークフィット",
    "Run Fit": "フィットを実行", "Stop Fit": "フィットを停止", "Undo Last Fit": "フィット直前に戻す", "Reset to Initial": "初期状態へ戻す", "Save Fit Data": "フィット結果を保存", "Parameters": "パラメータ", "Reflection Groups": "反射グループ", "Use Full Range": "全範囲を使用", "Excluded ranges": "除外範囲", "Weighting": "重み付け", "Robust loss": "ロバスト損失", "Current value": "現在値", "Standard error": "標準誤差",
    "Import XRD Data": "XRDデータを読み込む", "Import MPMS Data": "MPMSデータを読み込む", "Import Heat Capacity DAT": "比熱DATを読み込む", "Import Resistivity DAT": "電気抵抗DATを読み込む", "Import arbitrary PPMS DAT": "任意のPPMS DATを読み込む", "Heat Cap.": "比熱", "Resistivity": "電気抵抗", "Custom Plot": "任意のグラフ",
    "Formula": "組成式", "Sample mass (mg)": "試料質量 (mg)", "Molar mass (g/mol)": "モル質量 (g/mol)", "Calculate molar amount": "物質量を計算", "Calculate": "計算", "Apply Sample Information": "試料情報を適用", "Column Calculation": "列の計算", "New variable name": "新しい変数名", "Expression": "式", "Create calculated column": "計算列を作成", "Recalculate dependent columns": "依存する計算列を再計算", "Available symbols": "使用可能な記号", "Curve Fitting": "曲線フィット", "Minimum x": "X下限", "Maximum x": "X上限", "Run curve fit": "曲線フィットを実行", "Clear fit": "フィットを解除", "Edit data…": "データを編集…", "Delete": "削除",
    "Minimum T (K)": "温度下限 (K)", "Maximum T (K)": "温度上限 (K)", "Run Low-T Fit": "低温フィットを実行", "Use experimental uncertainties": "測定の標準不確かさを使用", "Show low-T residuals": "低温フィット残差を表示", "Show residuals": "残差を表示", "Run Debye–Einstein Fit": "Debye–Einsteinフィットを実行", "Run selected linearized fit": "選択した線形化フィットを実行",
    "Application color theme": "アプリの配色", "UI language": "表示言語", "Elements": "表示要素", "Ticks": "目盛", "Text & lines": "文字・線", "Light colors": "ライト配色", "Dark colors": "ダーク配色", "Applications": "外部アプリ", "Reset to Defaults": "既定値に戻す", "Apply": "適用", "Cancel": "キャンセル", "Close": "閉じる", "Save": "保存", "OK": "OK", "Browse…": "参照…",
    "Plot background": "グラフ背景", "Title": "タイトル", "Axis labels": "軸ラベル", "Tick labels": "目盛ラベル", "Frame": "枠線", "Grid": "グリッド", "Legend background": "凡例背景", "Legend border": "凡例の枠", "Width (cm)": "幅 (cm)", "Height (cm)": "高さ (cm)", "Transparent background": "背景を透明にする", "Export current view": "現在の表示範囲を出力", "Vector image": "ベクター画像", "Raster image": "ラスター画像", "Format": "形式", "Width": "幅", "Height": "高さ", "Resolution": "解像度", "Select All": "すべて選択", "Clear": "解除",
    "Results & comparison": "結果一覧・比較", "Recipes & batch": "レシピ・一括処理", "Sample registry": "試料情報", "Processing history": "処理履歴", "Refresh": "更新", "Copy table": "表をコピー", "Export CSV…": "CSVへ出力…", "Parameter": "パラメータ", "Create comparison graph": "比較グラフを作成", "Capture first selected": "選択データから取得", "Load…": "読み込む…", "Save…": "保存…", "Preview batch": "一括処理を確認", "Run checked datasets": "チェックしたデータへ実行", "Add sample": "試料を追加", "Delete selected sample": "選択試料を削除", "Save registry / link selected sample": "情報を保存・選択試料を関連付け", "Refresh processing history": "処理履歴を更新",
    "Properties": "プロパティ", "Selected object": "選択対象", "Apply properties": "プロパティを適用", "Axes": "軸", "Traces": "トレース", "Images": "画像", "Geometry": "寸法・余白", "Range minimum": "表示下限", "Range maximum": "表示上限", "Label / tick font": "ラベル・目盛のフォント", "Label / tick size (pt)": "ラベル・目盛サイズ (pt)", "Open experiment / graph": "実験・グラフを開く", "Import general text": "テキストデータを読み込む", "New graph": "新しいグラフ", "Create reviewed sharing copy…": "内容を確認して共有用コピーを作成…", "Image ROI / line profiles…": "画像の領域解析・断面抽出…", "Compatibility report…": "互換性レポート…",
    "X column": "X列", "Y / intensity column": "Y・強度列", "Sigma / standard uncertainty": "標準不確かさの列", "First data row": "データ開始行", "Delimiter": "区切り文字", "X axis / source unit": "X軸・元の単位", "Y unit": "Yの単位", "Save template…": "設定テンプレートを保存…", "Load template": "テンプレートを読み込む", "Save figure template…": "図テンプレートを保存…",
    "Recover selected": "選択した作業を復旧", "Delete selected…": "選択した復旧コピーを削除…", "Copy details": "詳細をコピー", "Redact local Windows paths": "ローカルパスを匿名化", "Find Next": "次を検索", "Find in manual…": "マニュアル内を検索…",
}

JA.update({
    "Add matched markers to XRD": "一致ピークのマーカーをXRDに追加",
    "Phase ticks": "相のピーク位置tick",
    "Selected phases: {count}; matched observed peaks: {matched}; unexplained: {unexplained}. Scores remain per phase.": "選択した相：{count}、一致した観測ピーク：{matched}、未説明：{unexplained}。スコアは相ごとの値です。",
    "Add phase ticks to XRD": "相のピーク位置tickをXRDに追加",
    "Phase annotation position": "相マーカー・tickの位置",
    "Above peak (%)": "ピーク上方への距離 (%)",
    "Below pattern (%)": "パターン下方への距離 (%)",
    "Reflection labels": "反射指数ラベル",
    "Show reflection labels": "反射指数ラベルを表示",
    "Show all reflection indices": "すべての反射指数を表示",
    "Marker stroke width": "マーカーの線の太さ",
    "Include composition": "組成を含める",
    "Font": "フォント",
    "Font size": "フォントサイズ",
    "Text color": "文字色",
    "Text angle (deg)": "文字の角度 (deg)",
    "Read reflection indices": "反射指数を取得",
    "Offset: X": "オフセット：X",
    "Offset: Y": "オフセット：Y",
    "Offset: X Multiplier (0 = unchanged)": "オフセット：X倍率（0＝変更なし）",
    "Offset: Y Multiplier (0 = unchanged)": "オフセット：Y倍率（0＝変更なし）",
    "Phase annotations added below the measured dataset. Select each child to adjust its style and position.": "相の注釈を測定データの子として追加しました。各子を選ぶと書式と位置を変更できます。",
    "Simulate selected phase profiles": "選択した相のプロファイルをシミュレーション",
    "Install the original MP index to simulate this saved structure": "保存した構造をシミュレーションするには元のMP索引を導入してください",
    "Datasets panel": "データセット欄", "Settings panel": "設定欄", "Focus plot": "グラフを広く表示",
    "Command Window": "コマンドウインドウ", "Procedure Window": "プロシージャウインドウ",
    "Open Command Window in separate window": "コマンドを別ウインドウで開く",
    "Open Procedure Window in separate window": "プロシージャを別ウインドウで開く",
    "Dock back Command Window": "コマンドをドックへ戻す",
    "Dock back Procedure Window": "プロシージャをドックへ戻す",
    "Import or drop measurement files to begin.": "測定ファイルを読み込むか、ここへドロップしてください。",
    "Reset layout": "配置を初期化", "Editing target": "現在の編集対象", "Select a dataset or branch": "データセットまたはブランチを選択",
    "Expand or collapse section": "セクションを展開・折りたたみ",
    "Drag to resize; double-click to reset column widths": "ドラッグで幅を変更・ダブルクリックで初期幅に戻す",
    "Plot display / selected wavelength": "表示：グラフ全体／波長：選択データ",
    "Plot-wide": "グラフ全体", "Selected dataset": "選択データ", "Selected dataset / branch": "選択データ・ブランチ",
    "File-shared sample": "ファイル内で共通の試料情報", "Active analysis": "現在の解析",
    "Result details…": "結果の詳細…", "Details": "詳細", "Details…": "詳細…",
    "Apply colors": "配色を適用", "Series colors": "系列の配色", "Selected data actions": "選択データの操作",
    "Apply and close": "適用して閉じる", "Clear fit result": "フィット結果を解除",
    "Run Selected Linearized Fit": "選択した線形化フィットを実行", "Run Low-T Fit": "低温フィットを実行",
    "Run Curie-Weiss Fit": "Curie–Weissフィットを実行", "Integrate ΔCp/T": "ΔCp/Tを積分",
    "Working with results": "結果を利用", "Reference information": "参照情報",
    "Edit selected column": "選択列を編集", "Reset automatic branches": "自動分割に戻す",
    "Restore plot height": "グラフの高さを戻す", "Residual panel": "残差パネル",
    "Focus graph": "グラフを広く表示", "Restore panels": "パネルを再表示",
    "Show Command Window": "コマンド欄を表示", "Show Procedure Window": "プロシージャ欄を表示",
    "Relative intensity": "相対強度", "Observed": "観測値", "Unexplained": "未説明", "Excluded": "除外",
    "Before automatic background": "自動背景処理前", "Calculated sticks": "計算スティック", "Predicted not observed": "予測のみ（未観測）",
    "Detect peaks, then Search candidates to calculate and rank all retrieved phases. Only matches above Minimum match score are listed.": "ピーク検出後に候補検索を実行すると、取得した候補を計算・採点して順位付けします。スコア下限以上の候補を表示します。",
    "MDS Match Score {score}; matched {matched}; unexplained {unexplained}; predicted not observed {missing}; fixed shift {shift}°": "MDS Match Score {score}; 一致 {matched}; 未説明 {unexplained}; 予測のみ {missing}; 固定シフト {shift}°",
    "Complete Step 1 with Detect peaks or Use fitted peaks before searching.": "検索の前にStep 1でピーク検出またはフィット済みピークの取り込みを行ってください。",
    "Set the calculation range within the measured 2θ range.": "計算範囲を測定した2θの範囲内に設定してください。",
    "Radiation, input data or detection settings changed. Run Detect peaks again in Step 1.": "X線源・データ・検出設定が変わりました。Step 1でピークを再検出してください。",
    "Stopped. Previous results were retained; no partial result was applied.": "停止しました。前の結果を保持し、途中の結果は反映していません。",
    "Stopped; the previous database registration was retained.": "停止しました。前のデータベース登録を保持しています。",
    "Peak selection changed. Search candidates again to update scores.": "使用ピークを変更しました。候補を再検索してスコアを更新してください。",
    "No saved fit for this experimental dataset. Use Detect peaks.": "この測定データのフィット結果がありません。ピーク検出を行ってください。",
    "No usable fitted groups. Use Detect peaks.": "使用できるフィット済み反射がありません。ピーク検出を行ってください。",
    "Calculated phase added to XRD as a separate dataset. Use its checkbox to show/hide it.": "計算パターンを独立したXRDデータとして追加しました。チェックで表示を切り替えられます。",
    "COD Inorganic — Error: database/index missing; import or verify again": "COD Inorganic — DBまたは索引が見つかりません。再取り込み・検証してください。",
    "Verifying Materials Project index": "Materials Project索引を検証中",
    "Search, calculate and rank phases": "候補相の検索・計算・順位付け",
    "Background subtraction and peak fitting": "背景差し引き・ピークフィット",
    "Complete Step 1 (Detect peaks or Use fitted peaks) before searching.": "Step 1でピーク検出またはフィット済みピークの取り込みを行ってください。",

    "XRD Phase Search–Match": "XRD 相検索・照合", "Open Phase Search–Match": "相検索・照合を開く",
    "Reference Databases": "参照データベース", "Reference Databases…": "参照データベース…",
    "Database": "データベース", "COD + MP + User CIF": "COD + MP + ユーザーCIF", "User CIF": "ユーザーCIF",
    "Step 1 · Observed peaks": "Step 1 · 観測ピーク", "Step 2 · Search settings": "Step 2 · 検索設定",
    "Local library": "ローカルライブラリ", "Experimental dataset": "測定データ", "Detect peaks": "ピークを検出",
    "Use fitted peaks": "フィット済みピークを使用", "Stop": "停止", "Search candidates · score and rank": "候補を検索・採点・順位付け",
    "Required elements": "必須元素", "Allowed elements": "許可する元素", "Excluded elements": "除外元素", "COD IDs": "COD ID",
    "Space group": "空間群", "Crystal system": "結晶系", "All must be present: Na Cl": "すべて含む例: Na Cl",
    "Subsets allowed: Na Ca Cl O": "一部の元素のみでも可（例: Na Ca Cl O）", "Exact seven-digit IDs, separated by commas": "7桁のIDをカンマで区切って入力",
    "H-M symbol or IT number": "H-M記号または国際表番号", "Advanced detection settings…": "ピーク検出の詳細設定…",
    "Minimum volume (Å³; 0 = any)": "体積下限 (Å³; 0 = 指定なし)", "Maximum volume (Å³; 0 = any)": "体積上限 (Å³; 0 = 指定なし)",
    "Radiation": "X線源", "2θ minimum (deg)": "2θ 下限 (deg)", "2θ maximum (deg)": "2θ 上限 (deg)",
    "Peak tolerance (deg)": "ピーク照合許容差 (deg)", "Reflection grouping (deg)": "反射のグループ化幅 (deg)",
    "Fixed zero shift (deg)": "固定ゼロシフト (deg)", "Detection prominence (fraction)": "検出プロミネンス（相対値）",
    "Smoothing σ (deg)": "平滑化 σ (deg)", "Noise σ (0 = estimate)": "ノイズ σ (0 = 自動推定)",
    "Minimum signal / noise": "最小信号／ノイズ比", "Minimum FWHM (deg)": "半値幅の下限 (deg)", "Maximum FWHM (deg)": "半値幅の上限 (deg)",
    "Peak σ (deg; 0 = fitted)": "ピーク σ (deg; 0 = フィット)", "Automatic background smoothness": "自動背景処理の平滑度",
    "Cu Kα2 / Kα1 intensity": "Cu Kα2 / Kα1 強度比", "Minimum match score": "照合スコアの下限",
    "MP isotropic lattice range (±%)": "MP等方格子補正の範囲 (±%)", "Retrieval candidates per database": "データベースごとの検索候補数",
    "Number of ranked results": "順位付けする結果数", "Use": "使用", "Intensity": "強度", "Width (deg)": "幅 (deg)", "Origin": "由来",
    "Rank": "順位", "Source": "出典", "Matched": "一致数", "Peak coverage": "観測ピークの一致率", "Selected": "採用",
    "Lattice (Å; deg)": "格子定数 (Å; deg)", "Source rank": "出典内の順位", "Possible group": "類似候補群", "Hull (eV/atom)": "凸包からのエネルギー (eV/atom)",
    "Cell scale": "格子倍率", "DB version": "DB版", "Filename": "ファイル名", "Identity": "識別子",
    "Add calculated pattern to XRD": "計算パターンをXRDに追加", "Select / unselect candidate phase": "候補相を採用／解除",
    "Open source": "出典を開く", "View CIF / structure": "CIF・構造を表示", "Register CIF files…": "CIFファイルを登録…",
    "Register CIF folder…": "CIFフォルダを登録…", "Remove selected registrations": "選択した登録を解除",
    "Checked peaks are used for matching. Unchecked peaks remain visible.": "チェックしたピークを照合に使用します。外したピークも表示されます。",
    "Registered CIFs are local snapshots. Originals are never changed or uploaded.": "登録したCIFはローカルコピーです。元ファイルの変更やアップロードは行いません。",
    "Score is a search ranking. Ctrl + wheel changes calculated-stick intensity. Cu Kα1/2 fits a constrained doublet.": "スコアは検索順位の指標です。Ctrl＋ホイールで計算スティックの高さを変更できます。Cu Kα1/2は制約付き二重線としてフィットします。",
    "Step 1: confirm radiation and detect observed peaks. Step 2: set chemical filters and search. Results are saved in the MDS session.": "Step 1でX線源を確認して観測ピークを検出し、Step 2で元素などの条件を指定して検索します。結果はセッションに保存されます。",
    "Step 1: run Detect peaks or Use fitted peaks before opening Step 2.": "Step 1でピーク検出またはフィット済みピークの取り込みを行ってからStep 2へ進んでください。",
    "Import the official COD Inorganic ZIP, or use an existing QualX database without copying it.": "公式COD Inorganic ZIPを取り込むか、既存のQualXデータベースを選択してください。",
    "Install / Import ZIP…": "ZIPをインストール・取り込む…", "Use existing .sq database…": "既存の.sqデータベースを使用…",
    "Use database folder…": "データベースフォルダを使用…", "Open official download page": "公式ダウンロードページを開く",
    "Verify": "整合性を検証", "Open Database Folder": "データベースフォルダを開く", "Remove registration": "登録を解除",
    "Remove MP registration": "MPの登録を解除", "Import MP Index…": "MP索引を取り込む…", "Verify MP": "MPを検証", "MP index build guide": "MP索引のガイド",
    "Remove the {source} registration? Database files will remain on disk.": "{source}の登録を解除しますか？ データベースファイルはディスクに残ります。",
    "User CIFs are managed in the Phase Search > Local library tab. Remove registration keeps database files on disk.": "ユーザーCIFは相検索のローカルライブラリで管理します。登録解除してもDBファイルは削除されません。",
    "COD Inorganic — Not installed": "COD Inorganic — 未登録", "Materials Project — Not installed": "Materials Project — 未登録",
    "Step 1 · arPLS Background": "Step 1 · arPLS背景処理", "Step 2 · Fit settings": "Step 2 · フィット設定",
    "Apply arPLS Background": "arPLS背景を適用", "Smoothness λ": "平滑度 λ", "Convergence ratio": "収束比", "Maximum iterations": "最大反復数",
    "Apply arPLS background before configuring the fit. Raw data are retained.": "arPLS背景を適用してからフィットを設定してください。生データは保持されます。",
    "Step 1: apply arPLS background before opening Step 2.": "Step 1でarPLS背景を適用してからStep 2へ進んでください。",
    "arPLS background applied. Continue in Step 2.": "arPLS背景を適用しました。Step 2へ進んでください。",
})


JA.update({'Cancel download': 'ダウンロードをキャンセル',
 'Software updates': 'ソフトウェアの更新',
 'Check for updates…': 'アップデートを確認…',
 'Check for updates at startup': '起動時にアップデートを確認',
 'Do not check for updates at startup': '今後は起動時にアップデートを確認しない',
 'Release notes on GitHub': 'GitHubでリリースノートを開く',
 'Check again': '再確認',
 'Later': '後で',
 'Checking for updates…': 'アップデートを確認しています…',
 'Download update': '更新をダウンロード',
 'Restart and update': '再起動して更新',
 'An update is available:': 'アップデートがあります：',
 'Update operation failed. Please check your connection and try again.': '更新処理に失敗しました。接続を確認して再度お試しください。',
 'The update information is invalid. Please try again later.': '更新情報を確認できませんでした。時間を置いて再度お試しください。',
 'Update downloaded. Save your work and restart to install it.': '更新のダウンロードが完了しました。作業を保存し、再起動して更新してください。',
 'Read the release notes on GitHub before updating.': '更新前に、GitHubでリリースノートを確認できます。',
 'You are using the latest available version.': '現在、利用可能な最新版を使用しています。',
 'Install MDS with Setup to use in-app updates. Source and portable copies can open the release page below.': 'ソフト内で更新するには、SetupからMDSを導入してください。ソース版・portable版では下のリンクからReleaseを開けます。',
 'Downloading update… You can cancel and retry later.': '更新をダウンロードしています… キャンセルして後で再試行できます。',
 'Wait for running analyses or procedures to finish, then retry.': '実行中の解析やプロシージャが完了してから、再度お試しください。',
 'Close other running MDS instances before updating.': '別に起動しているMDSを終了してから更新してください。',
 'Could not start the update. Your work remains open. Please try again.': '更新を開始できませんでした。作業は開いたまま保持しています。再度お試しください。'})

def tr(text: str) -> str:
    if str(create_settings().value("appearance/language", "en")) != "ja":
        return text
    if text in JA:
        return JA[text]
    for pattern, replacement in (
        (r"^(\d+) fitted reflections after automatic background subtraction\. Check peak selection, then go to Step 2 and Search candidates\.$", r"自動背景処理後に\1個の反射をフィットしました。使用するピークを確認し、Step 2で候補を検索してください。"),
        (r"Minimum match score automatically relaxed: ([\d.]+) → ([\d.]+)\. ", r"候補が少ないためスコア下限を自動調整: \1 → \2。 "),
        (r"Scored (\d+)/(\d+) candidates; (\d+) matched above threshold\. ", r"候補\2件中\1件を採点し、閾値以上の\3件を表示しています。 "),
        (r"^Retrieving candidates from (.+)$", r"\1から候補を検索中"),
        (r"^Installing Materials Project part (\d)/(\d)$", r"Materials Projectを取り込み中（\1/\2）"),
        (r"^Checking MP references ([\d,]+) / ([\d,]+)$", r"MP参照データを検証中: \1 / \2"),
        (r"^COD Inorganic — Ready: ([\d,]+) records, ([\d,]+) with reference peaks; snapshot (.+)$", r"COD Inorganic — 利用可能: \1件（参照ピークあり\2件）、版 \3"),
        (r"^Materials Project — ([\d,]+) DFT structures;", r"Materials Project — DFT構造\1件;"),
        (r"^(.+) unavailable; import or verify it in Reference Databases: (.*)$", r"\1を利用できません。参照データベースで取り込み・検証してください: \2"),
    ):
        text = re.sub(pattern, replacement, text)
    return text


class StatusLabel(QLabel):
    """Translate runtime status captions without touching scientific data or codes."""
    def setText(self, text: str) -> None:
        self.setProperty("mdsCaptionSource", text)
        self.setProperty("mdsCaptionRendered", tr(text))
        super().setText(tr(text))


JA.update({
    "Manually configure branches…": "ブランチを手動設定…",
    "Filter files / branches…": "ファイル・ブランチを絞り込み…",
    "Select a branch to style or analyze.": "スタイル・解析を編集するブランチを選択してください。",
    "Add branch": "ブランチを追加", "Delete branch": "ブランチを削除",
    "Use selected rows": "選択した行を適用", "Reset automatic branches": "自動分割に戻す",
    "Branch name": "ブランチ名", "First raw row": "開始行（元データ）", "Last raw row": "終了行（元データ）",
    "Average adjacent pairs": "隣接2点を平均", "Sample weight (mg)": "試料重量 (mg)",
    "Plot Style": "プロットスタイル",
    "Register MDS / open Windows Default Apps": "MDSを登録・Windowsの既定のアプリを開く",
    "MDS document files (.mdss / .mdsg)": "MDS文書ファイル (.mdss / .mdsg)",
    "Select a branch in Datasets. Use the file context menu to edit branches and adjacent-pair averaging.": "左のDatasetsでブランチを選択してください。ファイル名の右クリックから、ブランチと隣接2点の平均を設定できます。",
})


def translate_tree(root: QWidget, language: str | None = None) -> None:
    language = language or str(create_settings().value("appearance/language", "en"))
    for obj in [root, *root.findChildren(QWidget), *root.findChildren(QAction)]:
        if obj.property("mdsLiteralData"):
            translate_hint = getattr(obj, "translate_hint", None)
            if callable(translate_hint):
                translate_hint(language)
            continue
        for name in ("toolTip", "accessibleName"):
            getter = getattr(obj, name, None)
            setter = getattr(obj, "set" + name[0].upper() + name[1:], None)
            if callable(getter) and callable(setter):
                current_value = getter()
                if not isinstance(current_value, str):
                    continue
                previous_value = obj.property("mds" + name + "Source")
                rendered_value = obj.property("mds" + name + "Rendered")
                source_value = previous_value if isinstance(previous_value, str) and current_value == rendered_value else current_value
                translation = JA.get(source_value, source_value) if language == "ja" else source_value
                obj.setProperty("mds" + name + "Source", source_value)
                obj.setProperty("mds" + name + "Rendered", translation)
                if current_value != translation:
                    setter(translation)
        if isinstance(obj, QWidget) and obj.isWindow():
            original = obj.property("mdsWindowSource") or obj.windowTitle()
            obj.setProperty("mdsWindowSource", original)
            obj.setWindowTitle(JA.get(original, original) if language == "ja" else original)
        if isinstance(obj, (QLabel, QAbstractButton, QAction)):
            current = obj.text()
            previous = obj.property("mdsCaptionSource")
            expected = obj.property("mdsCaptionRendered")
            original = previous if isinstance(previous, str) and current == expected else current
            rendered = JA.get(original, original) if language == "ja" else original
            obj.setProperty("mdsCaptionSource", original)
            obj.setProperty("mdsCaptionRendered", rendered)
            if current != rendered:
                if isinstance(obj, StatusLabel):
                    QLabel.setText(obj, rendered)
                else:
                    obj.setText(rendered)
        elif isinstance(obj, QGroupBox):
            original = obj.property("mdsGroupSource") or obj.title()
            obj.setProperty("mdsGroupSource", original)
            obj.setTitle(JA.get(original, original) if language == "ja" else original)
        elif isinstance(obj, QLineEdit):
            original = obj.property("mdsPlaceholderSource") or obj.placeholderText()
            obj.setProperty("mdsPlaceholderSource", original)
            obj.setPlaceholderText(JA.get(original, original) if language == "ja" else original)
        elif isinstance(obj, QTabWidget):
            for i in range(obj.count()):
                page = obj.widget(i)
                if page is None:
                    continue
                original = page.property("mdsTabSource") or obj.tabText(i)
                page.setProperty("mdsTabSource", original)
                obj.setTabText(i, JA.get(original, original) if language == "ja" else original)
        elif isinstance(obj, QComboBox) and obj.property("mdsTranslateItems"):
            blocked = obj.blockSignals(True)
            for i in range(obj.count()):
                original = obj.itemData(i, 258) or obj.itemText(i)
                obj.setItemData(i, original, 258)
                obj.setItemText(i, JA.get(original, original) if language == "ja" else original)
            obj.blockSignals(blocked)
        elif isinstance(obj, QTableWidget):
            for i in range(obj.columnCount()):
                item = obj.horizontalHeaderItem(i)
                if item is not None:
                    original = item.data(258) or item.text()
                    item.setData(258, original)
                    item.setText(JA.get(original, original) if language == "ja" else original)
    # Context-bound actions mirror their translated source buttons only after
    # every caption has been updated. Never translate user data as UI captions.
    for widget in root.findChildren(QWidget):
        refresh = getattr(widget, "refresh_language", None)
        if callable(refresh):
            refresh()


class LanguageFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and watched.isWindow():
            from shiboken6 import isValid
            QTimer.singleShot(0, lambda: translate_tree(watched) if isValid(watched) else None)
        return False


def set_language(language: str) -> None:
    if language not in {"en", "ja"}:
        raise ValueError("Unknown UI language")
    create_settings().setValue("appearance/language", language)
    application = QApplication.instance()
    if isinstance(application, QApplication):
        if application.property("mdsLanguageFilter") is None:
            listener = LanguageFilter(application)
            application.installEventFilter(listener)
            application.setProperty("mdsLanguageFilter", listener)
        for window in application.topLevelWidgets():
            translate_tree(window, language)

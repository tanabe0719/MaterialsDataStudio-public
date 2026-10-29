# Graph Editor マニュアル

[English](graph_viewer_manual_en.md) | [日本語](graph_viewer_manual.md) · [Documentation / 文書一覧](index.md)

**対象バージョン:** 0.14.7

**更新日:** 2026-10-29

データの準備からグラフ作成、解析、仕上げ、出力まで、目的別に使い方をまとめています。画面例は合成データです。

ドロップダウンと数値入力欄はホイールで変更されません。周囲のパネルをスクロールできます。
値の変更にはクリック・キーボード・入力欄の上下ボタンを使います。グラフ上の拡大・縮小操作はそのままです。

## 目次

- [1. はじめに・画面と基本操作](#section-1)
- [2. データの読み込みとwaveの整理](#section-2)
- [3. tableで値を確認・編集する](#section-3)
- [4. wave演算と信号処理](#section-4)
- [5. グラフ・trace・軸を作る](#section-5)
- [6. 線・marker・軸・文字の書式](#section-6)
- [7. 注記・凡例・描画とマウス操作](#section-7)
- [8. 画像・ColorScale・ROI](#section-8)
- [9. 分布図・Contour](#section-9)
- [10. fitと仮説検定](#section-10)
- [11. 複数図・inset・Layout](#section-11)
- [12. 保存・出力・Igorへ送る・共有](#section-12)
- [13. CommandとPythonによる自動化](#section-13)
- [14. Igor / IPFの変換と困ったとき](#section-14)

## 1. はじめに・画面と基本操作

Graph Editorは、wave（数値・文字データ）、グラフ、tableをまとめて編集する独立ウィンドウです。
メイン画面の **Tools > Graph Editor / Igor Import**、またはEditorの **File > Open experiment / graph…** から開きます。
`.pxp`、`.h5xp`、`.mdsg`をメイン画面やEditorへドロップすることもできます。

![合成データだけを用いたIgor PXP Viewerの2D画像表示](screenshots/34_pxp_viewer.png)

左のツリーでwave・graph・table・procedureを選び、中央で内容を確認します。
グラフの対象を選ぶと右側に **Properties** が表示されます。区切りをドラッグして各領域の幅を調整できます。
上のツールバーにはOpen、Save、Import、Export Data、Export Figure、Send to Igor Pro、Undo/Redoがあります。

![合成データによるGraph EditorのProperties操作](screenshots/37_graph_editor.png)

元のPXPは変更しません。再編集には **MDSG**、Igorへ渡す場合には **H5XP**、完成した図には **Export Figure** を使います。
保存した文書は **File > Open Graph Document (.mdsg / .h5xp)** から再び開けます。
メイン画面のSave Sessionは独立したGraph Editorを保存しません。
編集はこのEditor内のUndo/Redo（Ctrl+Z/Ctrl+Y）で戻せます。`[Modified]` は未保存の編集を表します。

**Help > Graph Viewer Manual (English)**（F1）または日本語版からこのガイドを開けます。
自動化の説明は **Help > Python / mds Help…** にあります。ヘルプを開いたまま作業できます。

![合成データを用いたGraph ViewerのHelpメニュー](screenshots/77_graph_help_menu.png)

**ヘルプの左隣の「表示（View）」メニュー** に、データセット欄・設定欄の切替、
グラフを広く表示、配置を初期化、パネルを再表示をまとめています。上部の表示切替ボタン列は廃止しました。
**グラフを広く表示** は左右の欄とCommand／Procedureを隠し、wave・履歴・Procedure本文を保持します。
**パネルを再表示** で直前のドック配置へ戻り、表示メニュー内の **コマンドウインドウ**・
**プロシージャウインドウ** のチェックで個別に表示・非表示にできます。別ウインドウ化・ドックへ戻す操作も
同じメニューにあります。エディタを閉じたときのドックの位置・大きさ・表示状態を記憶します。
カラム境界のダブルクリックでも幅を初期値へ戻せます。

## 2. データの読み込みとwaveの整理

### テキストを読み込む

**Data > Load General Text…**（Ctrl+I）またはDAT/CSV/TSV/TXT/XYのドロップで読み込みます。
プレビューで区切り、先頭データ行（1始まり）、直前行を列名にするかを確認します。
各列にNumber / Text / Skipとwave名を指定します。空の数値セルはNaNになります。
既存waveの置き換えにはOverwriteと確認が必要です。Stopで読み込みを中止できます。

![ヘッダ除去と列ごとのwave読み込み](screenshots/42_graph_wave_import.png)

Quantum Design DATは `[Data]` の後の列名とデータを読み込みます。
空のComment列や欠損値を含む場合も列位置を確認してください。

![合成Quantum Design DATのData区切り・列名・空欄の読み取り](screenshots/60_quantum_design_import.png)

### 作成・編集・名前と単位

**Data > Make / Duplicate / Rename / Delete** でwaveを整理します。
**Edit Wave…** またはツリーの右クリックでセルを編集し、**Apply to Working Wave** で確定します。
Ctrl+C/Ctrl+Vで矩形範囲をコピー・貼り付けできます。編集時の値は表示桁数より高い精度を保持します。
3D/4Dデータはlayer/chunkを選択して編集します。

![全精度の編集用waveテーブル](screenshots/43_graph_wave_edit.png)

**Scaling** で座標の開始値・間隔・単位、**Redimension** でサイズを変更します。
X/Yや誤差waveの長さが合わなくなる変更は拒否されます。削除前に依存するグラフ等を確認してください。
traceを削除するだけならwaveは残ります。

**SetDimLabel…** は次元と各位置のラベル、**Note…** は注記を編集します。
ラベルindex -1は次元全体の名前、空欄はラベルの消去です。注記は置換または追記できます。
Data Folderの名前変更・移動とwaveの移動では、関連するグラフ・tableの参照も更新されます。

## 3. tableで値を確認・編集する

ツリーのTablesからtableを選びます。**Table > New Table…** ではwaveを選び、ドラッグで列順を変えます。
**Modify Columns…** で名前と列を変更します。長さが異なる列の末尾は空欄です。
セルのダブルクリックまたは **Edit cells…** でwaveを編集し、Applyで確定、Cancelで取り消します。
tableの削除はwaveを削除しません。waveの変更は関連グラフにも反映されます。

![合成データによる複数waveのtable](screenshots/52_graph_table.png)

![合成データによるtable内waveの編集画面](screenshots/53_graph_table_edit.png)

**Data > Table Format…** で表示桁数、数値形式、幅、単位見出しを指定します。
表示桁数を変えても元の値は丸めません。Commandではフォント・色・配置・列見出しも設定できます。
データ列に加え、scaled index列（`.i`）とdimension label列（`.l`）を表示できます。これらの派生列は読み取り専用です。

## 4. wave演算と信号処理

### 演算して派生waveを作る

**Data > Calculate Wave…** で `signal - reference` などを入力します。一覧の `w1`、`w2` などをコピーして使えます。
座標・単位の一致を確認し、必要な場合だけpoint-index演算または先頭waveへの1D補間を選びます。
補間範囲外はNaNです。結果の物理単位は確認してください。入力waveは残ります。

![wave演算とコピー可能な記号一覧](screenshots/51_graph_wave_calculation.png)

### 結合・分割・サンプリング

**Data > Concatenate… / SplitWave…** で入力順、出力名、ラベル・注記の扱いを指定し、Preview resultで確認します。
通常のConcatenateは同じ長さの1D waveを2Dの列へまとめます。1Dのまま連結するには `/NP` を指定します。
SplitWaveはmatrixの列などを個別waveに分けます。

**Analysis > Resample…** は補間・間引き倍率、次元、フィルタ・window・端点処理を選びます。
shapeとコマンドをプレビューしてから適用します。X/Yの片方だけの変更で長さが合わなくなる場合は先に複製してください。
Resampleは対象waveを更新し、座標間隔も変更します。

![合成waveのResampleフォームと形状preview](screenshots/82_wave_resample.png)

**Rotate** は値と座標の開始値を移動します。**Unwrap** は正のperiodを指定して位相の飛びを補正します。
NaNの位置は残り、欠損の前後でも累積補正を引き継ぎます。

### 統計・平滑化・FFT・Histogram

CommandからWaveStats、Smooth、FindLevel、FFT、Histogramを実行できます。引数は `Help コマンド名` で確認します。
`Smooth 3,signal` はworking waveを更新するため、入力を残すなら先にDuplicateしてください。
FFTは有限値の偶数長1D waveに対応します。Histogramではrange・bin幅・個数とcount/確率密度/累積を区別してください。
確率密度は範囲外の観測も正規化に含むため、限られた範囲の面積が1にならないことがあります。

## 5. グラフ・trace・軸を作る

**Graph > New Graph…** でplot種類、Y wave、X wave（またはScaling）、軸、名前を選びます。複数Y waveを同時に選べます。
**Append Traces / Images…** は現在のグラフへ追加します。**Replace X / Y Waves…** は書式を残してデータを入れ替えます。
**Reorder Traces…** は上がBACK、下がFRONTです。

![waveからのグラフ作成](screenshots/44_graph_new_graph.png)

traceの右クリックでBrowse、Edit、Hide、Remove、Duplicate、Replace Wave、Modify、Quick Fitなどを選べます。
Removeはtraceだけを除き、waveは保持します。隠したtraceは空白部分の **Show Hidden Trace** から戻せます。

![合成データの系列右クリックメニュー](screenshots/40_graph_trace_menu.png)

### 誤差棒・表示範囲・複数軸

**Graph > XY Error Bars…** で非対称X/Y誤差wave、線幅、cap、色を指定します。
Trace Propertiesでcapの太さとclipも変更できます。誤差waveの長さをtraceに合わせてください。
**Display points first,last** は元waveを切らずにindex範囲を指定します。表示値exportとQuick Fitもこの範囲を使います。

Trace PropertiesでX/Y軸を別々に選べます。top Xは独立した軸で、bottomのmirrorとは異なります。
左右Y、名前付きfree軸、軸の使用区間・交差位置を設定できます。
画像とtraceの重ね描きでは、画像はbottom/left、traceは選択した軸を使う点を確認してください。

![合成画像と独立top X軸上のtrace](screenshots/78_graph_axis_fidelity.png)

文字X waveはcategory軸になります。**Graph > Date Axis…** は1904年起点の日付・時刻や経過時間を表示します。
日付表示はISO/英語で、OSごとの全書式には対応しません。

### 拡大とcursor

plot内で矩形をドラッグして右クリックし、Expand / Shrink、Horiz / Vertを選びます。
**View All** は全体へ戻します。log軸・逆軸でも使え、Undoできます。
**Graph > Cursor…** はA～Jのcursorをtraceの点へ置き、位置と色を保存します。

![合成データの範囲選択後の拡大・縮小メニュー](screenshots/39_graph_range_menu.png)

## 6. 線・marker・軸・文字の書式

対象を選び、Propertiesを変更して **Apply properties** を押します。変更した項目だけを適用します。
traceの色、線幅・線種、marker形状・サイズ、塗り・輪郭、表示modeを設定できます。markerや線種は見本から選べます。

![マーカーの図柄による選択](screenshots/48_graph_visual_markers.png)

### 点ごとの色・サイズ・marker

Trace Propertiesのpoint-color、point-size、marker-waveにはtraceと同じ長さのwaveを指定します。
色の範囲・反転・log、ColorScaleを選べます。log色では非正値は表示しません。
RGB直接指定にはN×3/4のchannel waveを使います。これらの設定はline/marker modeで使用してください。

![合成waveによる点別カラーとColorScale](screenshots/80_graph_point_colors.png)

### 軸・目盛り・グラフの寸法

Axesでrange（両端空欄はauto）、linear/log、major間隔、minor数、tick方向・長さ・太さ、mirrorを変更します。
軸線、label、tick、tick labelの色は個別指定できます。空欄は色の継承です。
Tick / label lower boundとupper boundは目盛り・数値の表示座標範囲で、データや軸rangeは変わりません。

![合成グラフの軸・tickの個別色と表示範囲](screenshots/73_graph_axis_colors.png)

labelの文字、font、サイズ、回転、位置を変更できます。font名は入力するか一覧から選びます。
Graphの既定fontと各軸・注記の個別fontは分けて設定します。
**Geometry** でplot幅・高さ、aspect、marginを調整します。再現性が必要な図にはfontとpoint寸法を明示してください。
Igorの自動文字サイズ・目盛りや未導入fontは、環境によって見た目が変わる場合があります。

![合成データによる座標・フォント・tick設定](screenshots/79_graph_extended_fidelity.png)

![合成データによるIgor形式マーカーとラベル書式の表示](screenshots/35_pxp_markers.png)

## 7. 注記・凡例・描画とマウス操作

### 注記とTag

**Graph > Add Annotation…** または空白部分の右クリックから注記を追加します。
labelはダブルクリックで文字を編集し、legend/textboxはダブルクリックまたはModifyで詳細編集します。
Text / Frame / Position / Symbolsで文字・行間・枠・背景・shadow・anchor・回転・symbolを指定します。
Changeで確定、Cancelで取り消します。注記は複製もできます。

![テキストと凡例の編集ウインドウ](screenshots/45_graph_annotation.png)

Igorの `\s(trace)` symbolや太字・斜体・上付き・下付きに対応します。
**Graph > Tag…** はtraceの点へ注記を結び付け、arrowとoffsetを指定します。
`\ON`、`\OP`、`\OX`、`\OY` などで名前・index・座標を表示できます。任意式やimageへのTagは未対応です。

![合成データによるオフセット系列・複数の注記・矢印](screenshots/36_pxp_offsets.png)

### 図形を描く

縦のText and Drawツールバーでtext、line/arrow、rectangle、oval/arc、polygon/freehand/Bezierを選びます。
textはクリック、図形はドラッグで作ります。Escで中止します。

![合成データによるText and Drawツールバーと文字・楕円・矢印](screenshots/49_graph_drawing_toolbar.png)

polygonは頂点をクリックし、ダブルクリック・Enter・右クリックで終了します。
Bezierはnodeをクリックし、そのままドラッグして接線を設定します。最後のnodeでダブルクリックまたはEnterで終了します。

![Bezier作成途中のノードと接線ベクトル](screenshots/56_graph_bezier_creation.png)

既存Bezierを選ぶとnode/接線handleを編集できます。Altで反対側の接線から独立させます。
Pointerは選択と移動です。青枠やhandleは編集用で、図のexportには入りません。

![合成データによるBezierのノード・接線ハンドル](screenshots/54_graph_bezier_handles.png)

図形のダブルクリック・右クリックで座標と書式を編集します。Cut / Copy / Clear / Duplicateと **Edit > Paste Drawing** も使えます。
Layer / Arrangeで重なり順、Gridで表示・snapを調整します。Shiftで水平・垂直に制約し、Altで一時的にsnapを無効にします。
label、legend、textbox、ColorScaleはドラッグで移動できます。移動は1回のUndoで戻せます。

![図形の座標・文字・書式の編集画面](screenshots/50_graph_drawing_edit.png)

## 8. 画像・ColorScale・ROI

### 画像の表示と色

New GraphのImage plot、またはAppend Traces / Imagesから2D数値waveを追加します。
RGB/RGBA waveにも対応します。uint8 RGBの範囲は0～255、それ以外は0～65535です。
**Graph > Image Coordinates…** で非一様pixel座標のedge waveを指定できます。
edgeは単調に並び、対応する画像次元より1個多い値を持つ必要があります。

Images Propertiesでcolormap、range、reverse、log、補間、表示を設定します。logのrangeは正値にしてください。
NaN pixelは通常透明です。既存Igor色表の一部は近似色のため、重要な図は色を確認してください。

![Image Colormapのグラデーション見本](screenshots/55_graph_colormaps.png)

ColorScaleをダブルクリックまたはModifyし、Axis Labels / Frame / Position / ColorScale Main / Ticksで調整します。
barの位置・幅・高さ・方向、label、font、major/minor目盛りを設定できます。
画像のlog色とColorScale軸のlog表示は別設定です。ColorScaleのwave指定user ticksや第二軸は未対応です。

![ImageのColorScaleとtick編集](screenshots/47_graph_colorscale.png)

### ROI、profile、積分

**File > Image ROI / line profiles…** でX/Y平均profile、ROI平均、sample sum、物理積分を作れます。
範囲は元waveの座標で指定し、Swap X/Yには依存しません。物理積分には少なくとも2×2の有限値が必要です。

![元Waveの座標に基づくROI・断面抽出](screenshots/67_image_roi.png)

### 閾値とmask

**Data > Rectangular ROI…** は行・列indexの範囲から、内側0・外側1のmaskを作ります。
**Analysis > Image Threshold / ROI Mask…** で画像、manual / Iterative / Otsu、反転、ROI外の扱いと出力先を指定します。
Preview resultで確認します。既定出力は0/255、非有限値は64です。manualでは閾値と等しい値は0側です。
ROI外を元pixelのまま残す場合は型とNaNも保持します。

![合成画像のしきい値設定と出力preview](screenshots/83_image_threshold.png)

## 9. 分布図・Contour

### Box Plot

**Graph > New Graph… > Box plot** で1Dの群wave、または列ごとに群を持つ2D waveを選びます。
Trace Propertiesでquartile、whisker、outlier、notch、raw points、mean、fences、jitterを設定します。
元waveを編集すると統計値も更新されます。横幅・jitterの配置はIgorと完全には一致しません。

![合成データのBox Plotと統計設定](screenshots/81_graph_box_plot.png)

### Violin Plot

**Graph > New Graph… > Violin plot** で群を選びます。
bandwidth、method、kernel、extension、density、side、outline、raw points、mean/median、jitter、色・幅を設定します。
分布比較ではbandwidthとdensityを揃えてください。自動bandwidthや画面上の幅・jitterはIgorと異なる場合があります。

![raw群とKDE設定を持つViolin Plot](screenshots/85_graph_violin.png)

### Contour

**Graph > Contour…** でmatrixの等高線または塗りつぶしを追加します。
自動level、または明示したlevelを使います。塗りつぶしには2個以上の明示levelが必要です。
XYZの三角形分割やIgorの全ラベル・style設定には対応しません。

## 10. fitと仮説検定

### Quick FitとCurve Fitting

**Analysis > Quick Fit** でモデルを選ぶと、現在のグラフへfitを直接追加します。traceの右クリックからも同じ操作ができます。
複数traceがある場合は対象を選びます。**Fit Between Cursors / Selected Region** が有効なら、同じtrace上のA・B間を使います。
カーソルが揃っていない場合は選択矩形、どちらもなければ全範囲です。カーソルfitのXは単調である必要があります。
**Weight From Error Bar Wave** は利用可能なY誤差waveを1標準偏差として重みに使います。不要なら解除してください。
係数と標準誤差はCommand履歴に表示され、fit曲線・residual・条件は文書へ保存されます。追加は1回のUndoで戻せます。
**Add Coefficient Textbox** で結果注記も追加できます。

細かく設定する場合は **Analysis > Curve Fitting…** を開きます。対象・モデル・X範囲・重みに加え、
初期係数、固定する係数の0始まりindex、非zero点だけを含めるmask、全X範囲への曲線延長を指定できます。
初期係数はモデルの順番でcomma区切りにします。空欄なら自動推定です。
Run Quick Fitで計算し、係数・標準誤差・curve・residualを確認してから **Add Fit to Graph** を押します。Stopで中止できます。
line、polynomial（2～10次）、Gaussian、Lorentzian、Voigt、exponential、sin、Hill、Sigmoid、Powerなどを選べます。

![Curve Fittingの曲線・残差・係数確認](screenshots/46_graph_quick_fit.png)

画像には **Analysis > Quick Fit > poly2D… / Gauss2D…** を使います。1枚の数値matrixを選び、
1～4次の全次数多項式、または回転した楕円Gaussianをfitします。結果・residual・係数のwaveを作ります。
結果の画像はNew GraphのImage plotから表示できます。Igorのすべての2Dモデル・制約には対応しません。

### Analysisのwave処理

**Data** はwave作成・編集・整理、**Analysis** は計算・信号処理、**Statistics** は統計・検定です。

- Differentiate / Integrate：waveのX scalingで微分・累積台形積分し、新しいwaveへ出力します。
- Smooth：binomial、boxcar、Savitzky-Golay（2/4次）と端点処理を選びます。working waveを変更し、Undoできます。
- Convolve / Correlate：kernelまたはreferenceを先、signalを後に選びます。線形・非因果・循環、DC除去や自己相関を指定できます。
- Interpolate：Y、必要ならXを選び、linear / cubic spline / PCHIPで等間隔に補間します。Xは厳密単調です。
- Filter：signal、分子係数、IIRなら分母係数を選びます。初期状態はzeroです。
- Resample、Sort、Histogram、FindLevel、Compose ExpressionもAnalysisにあります。Sortは関連waveも同じ順に並べ替えます。
- Image Threshold / ROI MaskはAnalysisへ移動しました。

各画面で入力順を確認し、必要ならMove input up/downで変えます。Preview resultは文書を変更しません。
Do Itで結果を適用します。元のPXPには書き込みません。

**Analysis > Transforms** にはFFT・Inverse FFT、Periodogram、Lomb、MultiTaper PSD、Haar DWT、Morlet CWT、
Wigner-Ville、STFTがあります。FFTの周波数は元waveのsample intervalから決まります。
LombはYの後に厳密に増加するXを指定し、normalized powerを出します。MultiTaperはNW=3・5本のDPSSです。
Haar DWTは2のべき乗長で、係数順は最終approximation、粗いdetailから細かいdetailです。
CWTはscaleを1から指定最大まで、STFTはHann・50% overlapのPSDを使います。
Wigner-Villeはanalytic signalを使い、負の値や交差項も出ます。これらの2D出力はImage plotで表示できます。
方式・正規化・出力配置にはIgorとの差があるため、同じ操作名だけで数値の完全一致を判断しないでください。

![AnalysisメニューとQuick Fit](screenshots/87_analysis_menu.png)

### Statisticsとt検定

**Statistics > T-Test…** で一標本、独立二標本、対応ありを選びます。
平均の帰無仮説、両側/greater/less、alpha、出力名を指定します。独立群では自由度を実数・整数・n1+n2-2から選べます。
一標本CIに対応します。二標本のIgor固有CI、paired CI/自由度modeには対応しません。
独立群は各群の非有限値を除外し、対応あり・相関は両方有限なpairだけを使います。

![t検定の方法・tail・alpha・自由度](screenshots/84_t_test.png)

Statisticsには次の解析もあります。入力waveの役割、method、alpha、tailを画面で確認してください。

| 項目 | 方法・入力 |
|---|---|
| Wave Stats | N・欠損数・平均・SD・SEM・最小/最大・median・sum |
| F-Test / Variances Test | 分散比、Bartlett、Levene（mean / median / 10% trimmed） |
| One-way ANOVA | 標準、Welch、Brown-Forsythe。Igorに合わせた整数DFと実数DFを区別 |
| Two-way ANOVA | 完全なmatrixの反復なし、または均等反復の3D配列。欠損cellには非対応 |
| Correlation Tests | Pearson、Spearman、Kendall。等しい長さのpair |
| Multi-Comparison Tests | Tukey HSD。waveごとの独立群 |
| Normality Tests | Jarque-Bera、Shapiro-Wilk。小標本での近似に注意 |
| Circular Statistics Tests | radiansのRayleigh uniformity。Pは近似値 |
| Chi Squared / Contingency | 観測・期待度数、整数matrixのχ²・Yates・2×2 Fisher |
| Resampling Statistics | 平均のbootstrap percentile CI。回数とseedを指定 |
| Kolmogorov-Smirnov / Two Sample Tests | 二標本KS、標準正規との一標本KS、Mann-Whitney、Wilcoxon、Kruskal-Wallis |
| Waves Average | 同じshape・scalingのwaveの点ごとのmean・SD・SEM |
| ANOVA Power | 自由度・noncentrality・alphaからpowerを計算 |

検定結果はラベル付きwave／tableへ出力します。Pは帰無仮説が正しい確率を表すものではありません。
検定の前提とtailを解析目的に合わせて選び、複数の検定を行う場合は多重性も考慮してください。
MDSの結果表の並びはIgorと同一ではありません。対応していないmethod・optionを黙って置き換えることはありません。

![Statisticsメニュー](screenshots/88_statistics_menu.png)

![統計解析の入力・方法・preview](screenshots/89_statistics_form.png)

## 11. 複数図・inset・Layout

**Graph > Subwindow…** はgraph内へ1階層のinset graphを作ります。
元のgraphとinsetのデータ・軸・文字をそれぞれ調整してください。多重inset、panelやguideには対応しません。

**Graph > Layout…** は複数graphを1ページへ配置します。紙面は612×792 ptで、最大32図です。
各graphの矩形をpointで指定します。元graphを編集するとLayout側も更新されます。
幅、fontサイズ、labelの余白を揃え、Export Figureで仕上がりを確認してください。

![参照graphを紙面座標へ配置したLayout](screenshots/86_graph_layout.png)

## 12. 保存・出力・Igorへ送る・共有

### 編集を保存する

**File > Save Graph Document / Save Graph Document As…** でMDSGを保存します。
working waves、graph、table、編集内容をまとめて保持し、元PXPやIgorがなくても開けます。
既存MDSGへの上書きは可能です。異常終了後は **Recover Graph Document…** から復旧候補を開き、保存してください。

### H5XPとSend to Igor Pro

Save Asの **Igor HDF5 Experiment (*.h5xp)** はworking waves、graphs、注記・図形、tablesをIgor用に出力します。
選択graphだけでなく文書全体が対象です。毎回新しいファイル名を指定します。MDSGの保存先や未保存状態は変わりません。
元のuser procedure、notebook、未対応objectは含めません。編集の原本はMDSGでも保存してください。

Editorの **Send to Igor Pro** ボタンまたはFileメニューは、一時H5XPを生成してIgorで開きます。
メイン画面と同じIgor実行ファイル設定を使います。起動に失敗した場合は、表示されたH5XPをIgorから開くことができます。
転送先でも線・文字・軸・色や未対応項目を確認してください。見た目をそのまま渡す場合はExport Figureを使います。

### 図と数値の出力

**File > Export Figure…** はSave Graphics Fileを開きます。
Sameで元の寸法、Customでcm/mm/in/ptを指定します。SVG/PDF/EPSまたはPNG/JPEG/TIFF/Bitmap、
DPI、背景透明、color/monochrome、保存先を選び、Do Itで保存します。
JPEG/Bitmap/EPSは透明背景に対応しません。EMFとTo Cmd Lineは利用できません。

![生成データを用いたIgor風Save Graphics File](screenshots/38_graph_save_graphics.png)

**File > Export Data…**（Ctrl+E）はCSV/TSVを保存します。trace/imageを選び、表示値かraw値かを指定します。
表示値にはoffset、multiplier、Swap X/Yとtraceの点範囲が反映されます。軸rangeだけではデータを切り詰めません。
各traceにX/Y/誤差の列、画像にX/Y/value（RGBは成分列）が出ます。

![Viewer専用CSV/TSV出力](screenshots/41_graph_export_data.png)

### メイン画面との連携・共有用コピー

メイン画面の **File > Edit current plot in Graph Editor** またはgraphと鉛筆のボタンで、現在のplotを独立Editorへ渡せます。
Igorは不要です。以降の編集はメイン画面の解析結果を変更しません。

通常のMDSGには元の実験や注記・sourceが含まれることがあります。
**Create reviewed sharing copy…** で対象graphと必要なwaveだけを選びます。
元PXP、不要wave、wave note、procedure、history、source pathを除いたコピーを作ります。
graphの文字とwave名は残るため、保存前にサンプル識別子などを確認してください。

![共有コピーに含まれるWaveと注記の事前確認](screenshots/68_sharing_copy.png)

## 13. CommandとPythonによる自動化

### Command Window

**View > Command Window / Procedure Window** で開きます。Separate windowで分離、Dock backで戻せます。
閉じてもViewから再表示できます。

![独立表示したCommandとProcedure、明示的なDock backボタン](screenshots/59_command_procedure_separate.png)

Commandへ入力してEnterで実行します。補完候補は矢印とEnterで選び、入力欄下の構文を参考にします。
Up/Downで過去の入力を呼び出せます。Copy outputで出力をコピーできます。
`Help`、`Help ModifyGraph` などで対応コマンド・設定を確認します。

![合成データによるCommand Windowと入力候補・書式表示](screenshots/57_command_window.png)

```igorpro
Make/D/N=201 time,signal
SetScale/P x,0,0.05,"s",time
time=x
signal=sin(2*pi*time/3)
Display/N=Signal signal vs time
Label bottom "Time (s)"
Label left "Signal (a.u.)"
```

セミコロンでつないだ複数コマンドは1回のUndoにできます。途中で失敗した入力は適用されません。
GUI編集に対応する操作もCommandへ表示されます。表示記録は自動実行されません。
**Processing history / command recipe…** から再実行可能と表示されたGUI scriptをコピーし、確認して実行できます。

![合成データで線をGUI作成し、対応する描画コマンドを表示](screenshots/62_gui_command_recording.png)

### Python Procedure

編集可能なProcedureタブはPythonです。**Open .py / Save .py / Run All / Run Selection / Stop** を使います。
imported Igor sourceは別の読み取り専用タブで確認します。開くだけでは実行しません。
結果は成功時にまとめて反映され、Stop・例外・実行中の競合編集では適用されません。

![Python Procedure Windowと読み取り専用のIgorソースタブ](screenshots/58_procedure_window.png)

```python
import numpy as np
from mds import wave, graph, transaction

with transaction("Derivative"):
    t = wave("time")
    y = wave("signal")
    d = wave.create("derivative", np.gradient(y.values, t.values))
    g = graph.new("Derivative")
    g.append(d, t)
```

Pythonは用意された科学計算moduleとmds APIを使う制限付き環境です。ネットワークや任意のファイル操作はできません。
入力はworking wavesと明示した入力ファイル、保存はEditorのExportを使います。
初回実行には時間がかかることがあります。利用できる関数と例は **Help > Python / mds Help…** を参照してください。

![検索可能なPython専用ヘルプと詳細mds APIリファレンス](screenshots/61_procedure_help.png)

## 14. Igor / IPFの変換と困ったとき

### コピーを変換して使う

1. **Convert Igor / IPF…** でimported source、貼り付け、Open .ipfから入力を選びます。
2. **Analyze / Convert** を押し、Entry routineと引数（JSON list）を指定します。
3. Diagnostics / limitsとPython previewを確認します。未対応操作は元の行番号とともに表示されます。
4. **Insert into Python (not run)** で編集用コピーを入れます。
5. コード・wave名・単位を確認してから、必要なら **Run All** を押します。

![合成Igor関数の明示変換と未実行のPythonプレビュー](screenshots/71_igor_conversion.png)

変換はIgor全体の互換実行ではありません。対応しない処理は実行できません。
`LoadWave/G` の数値text読み込みとGUIのLoad General Textでは空欄の扱いが異なります。
欠損列のあるCSVはGUIプレビューで確認し、変換後もshape・列順・値・単位を確認してください。

### 入力ファイル・dialog・parameter

実行中のOpen/Dはファイル選択、NewPathはフォルダと対象textファイルの確認を開きます。
入力元は変更しません。CancelまたはStopで中止できます。Input files…で入力を用意することもできます。

![合成マクロが実行中に開くファイル選択](screenshots/74_igor_runtime_file.png)

Prompt / DoPromptは数値・文字・popup入力、DoAlertはメッセージと応答ボタンになります。
parameter panelは初期化後に表示されます。Applyは値を更新し、各ボタンは対応処理を明示実行します。
閉じたparameterはMacro parameters…で開き直せます。

![合成マクロの数値・文字列・選択肢入力](screenshots/75_igor_runtime_prompt.png)

![合成マクロのメッセージと応答ボタン](screenshots/76_igor_runtime_message.png)

![合成macroの数値パラメータと明示callback](screenshots/72_igor_parameters.png)

### 読み込みや見た目が合わないとき

- 未対応設定や近似表示はgraphの状態表示・ **Compatibility report…** で確認します。
- 元PXPを残し、MDSGを別名保存してから修正します。font・軸range・Scaling・単位も確認してください。
- H5XPは最新版のMDSで新規出力し、Igor側のエラー行を確認します。
- 見た目の受け渡しにはPDF/SVG/PNG、MDSでの再編集にはMDSGを使います。
- 数値結果が予想と違う場合は、欠損値、範囲、補間・重み・検定条件を確認してください。

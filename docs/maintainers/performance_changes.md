# Development performance changes / 開発中の性能改善

These changes are for PR review; they do not publish an installer or update feed.
The existing stable release is unchanged. / この文書はPRレビュー用です。インストーラ・更新feedは公開せず、正式配布版も変更しません。

## Implementation coverage / 実装範囲

| ID | Change / 対応 |
|---|---|
| S01 | Radiation constants/geometry no longer import peak fitting. / 放射線の定数・幾何計算をピーク検出から分離。 |
| S02 | File launches defer analysis workspaces; sessions create their active workspace first. / ファイル起動時は解析画面を遅延し、セッションの使用中モジュールを優先。 |
| S03 | Optional same-user local document forwarding; separate MDSS windows and independent Graph Editors; explicit new-instance override. / 任意の同一ユーザー内文書転送。既存作業を置き換えず、別プロセス起動も可能。 |
| H01 | Immutable JSON-compatible search revisions are shared by snapshots. Small selection/form state remains independently editable. / 不変の検索結果を履歴で共有し、小さい選択・入力状態を分離。 |
| H02 | Retained-memory accounting charges shared frozen revisions once, without revisiting their contents. / 不変の共有データを1回だけ集計し、履歴ごとの再走査を削減。 |
| H03 | Recovery saves skip unchanged successful revisions, preserving atomic saves and retry after failure. / 復旧保存は成功済みの同一状態を省略し、原子的保存・失敗後の再試行を維持。 |
| H04 | Operation-history chunks are immutable; exact-state verified-replay results have a bounded cache included in the memory budget. / 操作履歴を共有し、同一状態で検証済みの再生結果だけを上限付きで再利用。 |
| F01 | MDSS schema 3 stores shared arrays, contiguous branch slices and identical original bytes by reference. Schemas 1/2 remain readable. / 配列・連続ブランチ・原本を参照で共有するMDSS schema 3。旧形式も読込可能。 |
| F02 | NPZ members avoid a second compression layer; session CRC checks do not decompress known entries twice; duplicate NPZ header validation is removed. / 二重圧縮と重複検証を削減し、CRC・展開上限・型・経路検査を維持。 |
| F03 | Only decoder-owned validated arrays use the internal adoption path. Public Dataset/wave constructors retain defensive copying. / 所有権と検証が明確な配列だけを内部で再利用し、通常のconstructorのコピーを維持。 |
| F04 | XRD text/RASX rows stream from text readers; general-text parsing retains its bounded line/CSV path and encoding behavior. / XRD・RASXを行単位で解析。一般テキストの上限・encoding・CSVの動作を維持。 |
| F05 | Canonical MDSG waves decode on first data access after full header/allocation/CRC checks. Metadata selectors do not force decoding. Original PXP parsing remains unchanged. / MDSGは全体の安全検査後に必要なWaveを展開。PXPの初回解析は従来どおり。 |
| G01 | Inspector controls update in place and preserve pending input, selection and scroll position. / プロパティを再利用し、未適用入力・選択・スクロールを保持。 |
| G02 | Preset icons use the same marker paths/dashes through Qt painting, retaining the LRU and custom patterns. / 同じ形状・線パターンをQtで描き、LRUと独自パターンを維持。 |
| G03 | Duplicate bind renders are skipped. Plain XY style edits can update artists; complex graph/legend/structure changes retain full rendering. / 重複描画を省略し、単純XYの書式を既存artistへ反映。複雑な図は完全描画。 |
| G04 | Existing label reuse is preserved; only off-viewport display labels are hidden. Full labels remain available for export. / ラベル再利用を維持し、画面外だけを非表示。出力用の全ラベルは保持。 |
| G05 | Ordinary images avoid unnecessary float/finite copies; disposable export figures release their canvases. Full scientific image data and output DPI are retained. / 画像の余分なコピーを削減し、出力後のcanvasを解放。元画像・出力解像度を維持。 |
| G06 | Scientific conversions and monotonicity use bounded read-only revision caches. Legend-only changes do not resend unchanged arrays. / 変換・軸の単調性を上限付きで再利用し、凡例変更で数値を再送しない。 |
| Q01 | Empty votes return immediately; nonempty votes fetch counts only for voted IDs. Voting, density correction and sorting are unchanged. / 空投票は即返却し、該当相の件数だけを取得。投票・補正・順序は維持。 |
| Q02 | Signatures cache only fully read-only array chains plus all scientific metadata. Mutable inputs always rehash. / 不変配列と科学条件の組だけをキャッシュし、可変配列は再検査。 |
| Q03 | Preview requests coalesce within one event-loop turn; explicit preview and initial/reopened display remain synchronous. / 同一イベント内の再描画を集約し、初期表示・再表示は即座に反映。 |
| Q04 | Full-condition result cache includes validated DB identities, engine/calculator versions and every search/scale limit; cancelled/error results are not cached. / DB・全条件・版を含む結果キャッシュ。中断・失敗結果は保持しない。 |
| Q05 | MP scale search reuses invariant doubled d values, intensity masks and repeated objective values. The 21-point grid, bounds, detailed matcher and arithmetic order are retained. / 不変量を再利用し、探索点数・範囲・詳細照合・演算順を維持。 |
| Q06 | Bounded structural/CIF-metadata caches use exact CIF text and return independent structures/cards. Existing pattern keys still include radiation/range/calculator. / CIF本文をキーに構造・情報を再利用し、呼出側へ独立した構造・カードを返す。 |
| Q07 | Intermediate candidate peaks stay as arrays until result retention; reduced-formula parsing is cached. COD bulk metadata and MP compact records remain. / 採用まで配列を保持し、組成解析を再利用。既存の一括取得・圧縮形式を維持。 |
| D01 | Flat, measurement and XRD trees reuse dataset-ID items and batch selection signals. / 通常・測定・XRDの一覧項目をIDで再利用し、選択通知を集約。 |
| D02 | Transport segments/branch indices and physical conversions are cached; contiguous plot slices avoid advanced-index copies. / 区間・索引・物理変換を再利用し、連続区間のコピーを削減。 |
| N01 | Chunked Bernoulli/Bose-tail Debye integrals replace repeated quadrature, retaining the prior 8-decimal input rounding. Domain tests compare against quadrature. / Debye積分をベクトル化し、既存の丸めを保持。積分結果を従来計算と照合。 |
| N02 | Large low-temperature fits run in cancellable workers with stale-input checks; full fit arrays are prepared in the worker. Small fits remain immediate. / 大きい低温fitはworker化し、古い入力結果を適用しない。全長配列もworkerで作成。 |
| N03 | Existing two-job limit is retained; normal bootstrap bounds nested numerical thread pools unless explicitly configured. CIF structure reuse also reduces repeated setup. / 同時job数を維持し、明示設定がない場合の数値ライブラリの内部並列を制限。構造の再準備も削減。 |
| U01 | Font families are cached and invalidate on font-database changes; unchanged themes avoid a full palette/style reapplication. / フォント一覧を再利用し、フォント変更時に失効。同じテーマの再適用を省略。 |
| U02 | Automatic shell registration waits until after initial document work; update opt-out and download/apply consent are retained. / 初期読込後に関連付け登録を行い、更新の無効化・確認を維持。 |
| U03 | Local first-paint/document-ready and Windows working-set milestones support diagnosis. External Explorer/cloud/antivirus delays remain a separate profiling scope; no security settings are changed. / 初期描画・文書準備・Windowsメモリを記録。外部I/Oの原因は未確定として区別し、保護設定は変更しない。 |
| U04 | Unused Graph Editor dialogs/exports load on demand; procedure isolation and no-network boundaries are unchanged. / 未使用ダイアログ・出力を遅延し、Procedureの隔離境界を維持。 |
| T01 | Fresh-process empty/MDSS/MDSG probes record first events, document-ready, style P95 and Windows RSS. Structural gates prevent peak-detection imports during these startup modes. / 3経路の起動・操作P95・メモリを継続計測し、不要な解析importを検出。 |

## Validation and measurement / 検証・計測

Synthetic tests cover scientific equality, mixed-axis edits, tick-wave pairing, extra-wave transfer through both routes, immutable history, mutable-input invalidation, shared-branch sessions, lazy waves, IPC, and Debye integrals.
Both transfer routes retain only plotted graph traces; extra checked data are waves in the document.
Igor Pro 10.03 read the generated files without a recreation error; numerical, complex, matrix and text values and custom tick labels were checked.

合成データで科学計算、軸の一括変更、目盛Wave、両転送経路、履歴、失効、共有ブランチ、遅延Wave、IPC、Debye積分を検証します。
両転送経路とも、追加Waveによってグラフのtraceは増えません。Igor Pro 10.03でも生成ファイルを読み込み、各型の値と独自目盛を確認しました。

An alternating same-environment Python 3.12 source-startup comparison (3 runs per revision, uncontrolled OS cache) measured median first events **3.62 s → 1.80 s**.
A synthetic parent plus eight branches retained **0.96 MB** of unique raw arrays after reload, versus **4.80 MB** with independent copies; original bytes remained one shared object.
The 2.32 MB search-state fixture serialized in about **0.42 ms**, and ten-snapshot memory accounting took about **6 ms** after warming shared revisions.
These are measurements for these fixtures, not an EXE/Explorer or full COD/MP end-to-end speed guarantee.

同一環境で交互に3回ずつ測ったソース起動では、最初のイベントまでの中央値が **3.62秒→1.80秒** でした（OS cacheは未制御）。
親＋8ブランチの合成例では、再読込後の固有raw配列が **4.80 MB→0.96 MB**、原本も1個の共有を保ちました。
約2.32 MBの検索状態では、共有データの準備後の状態出力が約 **0.42 ms**、10履歴の保持量集計が約 **6 ms** でした。
これらは記載した条件の計測であり、EXE・Explorerの起動時間や実COD/MP検索全体の保証値ではありません。

Run `python scripts/benchmark_startup.py --repeat 3 --report .test-tmp/startup.json` for the public synthetic probe.
The publication gate runs all three modes once in addition to the functional suite.
Keep machine/environment metadata with comparisons; do not compare OS-cold EXE timings directly with these source probes.

公開用の合成計測は上記コマンドで実行でき、公開前検査でも3経路を各1回確認します。
比較時は環境条件を残し、OS初回のEXE計測とソース計測を直接比較しないでください。

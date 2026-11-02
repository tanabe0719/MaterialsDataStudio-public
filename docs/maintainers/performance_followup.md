# v0.15.2 development follow-up / 開発中の追加改善

This is a development PR, not a Release. / この変更は開発PRで、正式配布ではありません。

| Review ID | Implemented behavior / 対応 |
|---|---|
| R01 | Scientific settings and read-only data are snapshotted before transfer preparation runs in a worker. Qt controls stay on the GUI thread. / 科学条件を確定し、Wave準備をworkerへ移動。 |
| R02 | Lazy waves share a synchronized NPZ member store instead of opening the catalog for each wave. / 遅延WaveのNPZ読込元を共有。 |
| R03 | Large-document replay verification is bounded and deferred; pending operations are not replayable. Save/close flush verification, and completion updates the appropriate Undo states. / 大きな文書の検証を分離し、未検証scriptを提供しない。 |
| R04 | Plain line edits can reuse artists with legends/reflection annotations, preserving fallback for dependent changes. / 凡例・指数付き図の線変更を部分更新。 |
| R05 | Sorted anchor indices restrict label transform/bounds checks to conservative viewport candidates. / 回転文字の余白を含めて可視候補を絞る。 |
| R06 | Search cache hits pass immutable results through to the window; mutable edits require explicit independent copies. / 検索結果の再コピーを削減。 |
| R07 | Explicit stick scaling updates existing stick arrays without rebuilding observed data and marker geometry. / 倍率操作はsticksを直接更新。 |
| R08 | Observed peak heights are retained with the exact data/peak revision. / ピーク上方位置の再走査を削減。 |
| R09 | Candidate table cells reuse existing items and update changed text. / 表の全項目再作成を削減。 |
| R10 | Scientific workers prepare immutable result fingerprints/memory accounting before GUI acceptance. All-module state collection remains for correct cross-module Undo. / 重い初回集計をworkerで実施。全module Undoの整合を維持。 |
| R11 | Array caches remove dead-input entries, expose hit/miss counts and have a shared conservative budget; scientific calculations run outside the bookkeeping lock. / 配列cacheの寿命・合計予算・並行処理を整理。 |
| R12 | Original-source hashes are reused by immutable object identity during save/load. Full post-write reader validation is retained. / 同じ原本の再hashを削減し、保存後の全検証を維持。 |
| R13 | Unmodified deferred NPY members stream through the writer without materializing scientific arrays. Metadata-only encoding no longer loads arrays. / 未使用Waveを展開せず保存。 |
| R14 | Optional document forwarding is attempted before importing the GUI application shell. / 既存processへの文書転送を起動の前段へ。 |
| R15 | Graph-only launches use an independent Graph Editor shell; recovery candidates retain the normal recovery route. Original PXP parsing and its read-only array views remain intact. / graph専用起動を軽量化し、原PXPの互換解析は維持。 |
| R16 | Shared member stores spill large compressed backing to an owned temporary file and close it with their last owner. / 大きな圧縮読込元のRAM保持を抑制。 |
| R17 | Append-only operation history shares persistent prefixes; mutable layout clones retain their previous isolation guarantees. / 操作履歴の追記で過去の全件コピーを避ける。 |
| R18 | Ordinary images explicitly resample in data space; wave data, cursor values and final output resolution stay full size. / 表示時のRGBA化をデータ空間の再標本化後に行う。 |
| R19 | A fresh-process 1/2/4-thread by 1/2-job numerical probe checks scientific values. Runtime defaults and user overrides are unchanged. / 数値thread比較を追加。合成例だけで既定値を変更しない。 |
| R20 | Follow-up probes cover transfer, 600 lazy members, 10,000 history records, immutable results, process memory and numerical pools. Startup/style probes also record event-loop gaps. / 負荷軸とGUI待ち時間の計測を追加。 |

## Correctness / 正確性

Transport geometry validates every required positive finite dimension **before division**. Incomplete geometry omits only unavailable derived waves; raw/processed waves and plotted traces remain transferable. No sample dimensions are invented.

抵抗率計算では除算前に寸法を検証します。未設定の寸法を補完せず、計算できない派生Waveだけを省略し、元データ・処理済みデータ・表示曲線の転送を維持します。

Scientific scores, search limits, original arrays, complete exports, Undo/Redo, CRC/path/type/allocation checks and atomic replacement remain required. A shared array-cache budget is not a process RSS limit. Exact latency depends on workload and machine.

科学的score・検索上限・元配列・完全な出力・Undo/Redo・安全検査・原子的保存を維持します。cache予算はprocess全体のメモリ上限ではありません。

## Profiling / 計測

Run `python scripts/benchmark_followup.py --thread-matrix --report .test-tmp/followup.json` and the existing startup probe. Results distinguish synthetic source measurements from EXE/Explorer, real database and external I/O measurements. Numerical-pool probes use linear least squares and Debye integrals; they do not establish optimal COD/MP/CIF thread settings.

上記の合成計測に加え、配布前には凡例・指数が多い図、実規模DB、長時間利用、配布EXEの測定が必要です。原PXPの完全なmetadata-first parser、全moduleのdirty通知cache、保存後検証の実体化省略、独自image pyramidは導入していません。互換性・正確性を守る別の改善を上表に明記しています。

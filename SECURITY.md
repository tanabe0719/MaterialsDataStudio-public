# Security policy / セキュリティ

## Supported version

Security fixes are applied to the latest version on the default branch.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature when it is enabled
for this repository. Do not include credentials, unpublished measurement data,
or other sensitive laboratory information in a public issue.

When reporting a problem, include the affected version, a minimal reproduction,
and the expected impact. Remove sample names, local paths, session files, and raw
instrument data unless they are synthetic and required to reproduce the issue.

## Data and network behavior

Materials Data Studio processes files locally. The application does not automatically upload measurements or retrieve application updates.
Opening external reference links is an explicit user action. Session and export files
remain on the user's computer unless the user moves or uploads them separately.

Repository demo files under `docs/demo_data/` are generated synthetic datasets.
Real `.mdss`/`.mdsession` sessions, `from_user/`, `user_data/`, `exports/`, local
credentials, build output, and development caches are excluded by `.gitignore`.

PXP procedures, history, IPF and Python files are source, not trusted code.
Loading, analyzing and converting never execute them automatically. Explicit
Igor/IPF conversion accepts a bounded subset and reports unsupported routines;
it never follows includes or enables arbitrary imports, host I/O or OS commands.
The public `mds.command` bridge has an explicit command/flag/style allowlist.

Converted legacy file operations use only explicitly GUI-selected, bounded text
snapshots (64 MiB decoded UTF-8 total). Per-run input sidecars are not MDSG metadata.
NewPath/IndexedFile enumerate that collection, not host directories. Reads cannot
escape it; writes/deletions affect run-local memory buffers only, never source files.
Panel metadata becomes a source-positioned MDS form after explicit initialization;
buttons run only on clicks. Modeless file/folder/prompt/message dialogs use bounded
JSONL streams pre-opened by trusted bootstrap. Worker requests cannot open suggested
host paths, execute host code or read folders; the user must explicitly choose files
(and confirm immediate folder contents). Replies carry only those text snapshots.
Strict schema/sequence/frame/cumulative limits, cancellation and bounded UI waits
apply. ExecutionGuard stays active while waiting. No new OS token capabilities or
user-import modules are granted. fontTools/mpl_toolkits are trusted rendering
dependencies, initialized before the user-code guard, not exposed as user modules.
Review converted code and scientific results.

Explicit Procedure runs use the existing Windows AppContainer with no network
capabilities and no ordinary-process fallback, scientific facades, an audit guard
and process/time/memory/output limits. Native host-file/network denial is checked
before user execution. These are defense-in-depth controls, not a VM or a guarantee
against every OS/native-library vulnerability or Windows-shared system resource.
Inspect imported/converted source and results before trusting or sharing them.

## 日本語

セキュリティ修正は最新版を対象とします。private報告窓口が有効な場合はGitHubのSecurityから連絡してください。
公開Issueに鍵、token、研究データ、試料名、私的パス、実セッションを載せず、合成データによる最小再現例を使用してください。
窓口未設定時は、機密情報を含めず非公開窓口の案内のみを依頼してください。

測定データの処理はローカルで行い、測定値の自動アップロードやアプリ更新の自動取得は行いません。
参照先リンクの表示は明示操作です。診断共有は利用者の選択によるもので、自動マスク後も内容を確認してください。

PXP、IPF、Procedure、Python、履歴は信頼できる実行コードとは扱いません。読み込み・解析・変換だけでは実行しません。
変換は対応する範囲に限定し、選択された入力のsnapshot、コマンド・flagの許可一覧、入出力・メモリ・時間・応答サイズの制限を維持します。
実行は利用者の明示操作によるWindows AppContainer内で行い、ネットワーク権限も通常プロセスへのfallbackも与えません。
これは多層防御であり、OSやnative libraryを含む全脆弱性への保証ではありません。ソースと科学的な結果を確認してください。

公開用配布の追加規則は [publication policy](docs/maintainers/publication_policy.md) を参照してください。
必要な対応ソースは、バイナリの配布範囲に合わせて保持します。

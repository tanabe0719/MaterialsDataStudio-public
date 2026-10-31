# Releases and future online updates / Release・将来のオンライン更新

## English

The publication repository uses the existing `MaterialsDataStudio` Velopack
package id, `win` channel and Windows x64 runtime. This preserves the manual
installer upgrade path. The initial audited baseline remains 0.14.7 in this
new repository; every subsequent published binary change needs a higher version.
Same-version replacement tools from the private development process are not exported.

Ordinary pushes run source/tests/publication gates. Installer builds and release
publication are explicit maintainer operations, avoiding unnecessary unsigned
binaries in Actions artifacts. The release workflow is manually dispatched on
an exact version tag. If GitHub quota prevents CI, complete and retain the local
validation/build/installer checks instead; do not report a remote pass.

An instruction to implement or push ends at a development branch or PR by default.
It does not authorize publishing a Release or changing the stable update feed.
Only an explicit instruction to formally distribute or update Releases authorizes
publication. Public main requires a PR and successful Python 3.12/3.14 CI against
the up-to-date base, without bypass actors. A CI quota block leaves the PR
unmerged despite successful local checks. Verify CI on the exact release source
before publishing. Draft preparation and publication are distinct steps.

A release is assembled and verified as a draft. It includes Setup, the full
`.nupkg`, `releases.win.json`, corresponding library source, MDS source, standalone
bilingual HTML manuals, optional COD/MP ZIPs, attribution, manifest and SHA256SUMS.
Do not publish a draft with missing sources or a feed pointing to absent packages.

The full-package HTTPS feed is now used by v0.15.0 installed Windows copies. Startup checks respect opt-out. Download and apply require consent; auto-apply-on-startup stays disabled. A separate process handles bounded network operations; apply is armed in the GUI process only after all document confirmations and idle checks. Downloads are pinned to the exact version tag, rechecked against that feed and validated by the SDK. Embed bilingual NotesMarkdown in every new feed using --releaseNotes. Never embed a PAT. Source/portable copies need Setup, and pre-0.15.0 users must first upgrade manually.

Before public distribution, resolve `packaging/publication.json` readiness items:
current MP Terms review and a signing plan. The user explicitly selected unsigned distribution on 2026-10-30; only NotSigned or Valid is accepted, never an invalid signature. MP database assets are not distributed pending the pre-redistribution contact. The existing v0.14.7 binaries/tag remain unchanged. Secret keys and
PFX files never enter Git, source archives or release assets.

Only latest should remain listed by default. On a later release, safely transfer
older versioned ApplicationSource/CorrespondingSources assets to latest before
removing the old Release; verify their hashes. A cleanup must not remove a
package used by the active feed or source needed for any still-offered binary.
No automatic historical cleanup is allowed unless those checks succeed.

## 日本語

Velopackの既存package id `MaterialsDataStudio`、channel `win`、Windows x64を維持します。
新しいリポジトリの最初の監査済みbaselineは0.14.7です。それ以降の配布バイナリ変更では版を上げ、
私的開発で使っていた同版差替えツールは公開用に移しません。

通常pushは実装・テスト・公開用検査を行い、インストーラは明示的な配布作業で作成します。
Release workflowは正しいversion tagから手動起動します。CI quotaで動かない場合もローカル検査・ビルド・インストーラ検証を完了し、CI成功と報告しません。

「実装して」「pushして」は通常、開発ブランチへの保存またはPR作成までです。
Release公開・安定版更新feedの変更は、「正式配布して」「Releasesを更新して」の明示指示後に行います。
公開用mainはPRと最新baseに対するPython 3.12・3.14のCI成功を必須とし、回避を認めません。
CI利用制限時もローカル検査を行いますが、PRは未マージで保持します。
正式配布前に配布対象ソースのCI成功を確認します。Draftの準備と正式公開を別の段階として扱います。

Setup、full `.nupkg`、`releases.win.json`、ライブラリ対応ソース、MDSソース、日英HTMLマニュアル、
任意のCOD/MP、帰属表示、manifestとSHA256SUMSをdraftで検証してから掲載します。
ソース不足や存在しないパッケージを参照したfeedを公開しません。

v0.15.0のWindows導入版はHTTPSのfull feedで更新します。起動時確認は無効化設定を尊重し、取得・適用には確認を挟みます。ネットワークは別プロセスで処理し、適用指示は全保存確認・実行中ジョブの確認後にGUIプロセスから行います。取得先は版付きtagへ固定して再検査し、SDKでhashを検証します。--releaseNotesで新しいfeedに日英NotesMarkdownを埋め込みます。PATは埋め込まず、起動時の自動適用は無効のままです。ソース版・portable版および0.15.0より前の版の初回更新はSetupを使います。

public配布前に `packaging/publication.json` の現行MP規約確認・Authenticode署名方針を解決します。
2026-10-30の明示指示により当面未署名配布とし、NotSignedまたはValidのみ許容します。不正な署名は拒否します。MPのDBは事前連絡が未実施のため配布しません。既存v0.14.7のバイナリ・tagは変更しません。秘密鍵やPFXをGit・ソースZIP・配布assetに入れません。

既定はlatestだけを掲載します。次のRelease作成時は、旧版のMDSソース・対応ソースassetを最新へ移してhashを検証してから旧Releaseを整理します。
稼働中feedの参照先、配布中バイナリの必要ソースを削除しないよう検査し、検査に通らない自動削除は行いません。

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

A release is assembled and verified as a draft. It includes Setup, the full
`.nupkg`, `releases.win.json`, corresponding library source, MDS source, standalone
bilingual HTML manuals, optional COD/MP ZIPs, attribution, manifest and SHA256SUMS.
Do not publish a draft with missing sources or a feed pointing to absent packages.

The current feed contains a full package only. Online acquisition and in-app
updates remain disabled. A future implementation can use Velopack's GitHub
source for this repository or its documented HTTP feed. Never embed a PAT.
Retain full-package fallback, validate OS/architecture/channel and test upgrades
from older installed versions. Do not silently enable downloads during this task.

Before public distribution, resolve `packaging/publication.json` readiness items:
current MP Terms review and an Authenticode signing plan. The initial private
preparation installer is unsigned and is labeled accordingly. Secret keys and
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

Setup、full `.nupkg`、`releases.win.json`、ライブラリ対応ソース、MDSソース、日英HTMLマニュアル、
任意のCOD/MP、帰属表示、manifestとSHA256SUMSをdraftで検証してから掲載します。
ソース不足や存在しないパッケージを参照したfeedを公開しません。

feedはfullのみです。オンライン取得・アプリ内更新は未実装のまま維持します。
将来はVelopackのGitHub sourceまたはHTTP feedを選び、PATを埋め込まず、
旧導入版からのfull更新・OS・architecture・channelを検証します。

public配布前に `packaging/publication.json` の現行MP規約確認・Authenticode署名方針を解決します。
最初のprivate準備版は未署名と明記します。秘密鍵やPFXをGit・ソースZIP・配布assetに入れません。

既定はlatestだけを掲載します。次のRelease作成時は、旧版のMDSソース・対応ソースassetを最新へ移してhashを検証してから旧Releaseを整理します。
稼働中feedの参照先、配布中バイナリの必要ソースを削除しないよう検査し、検査に通らない自動削除は行いません。

# Publication and push policy / 公開用pushルール

## English

1. **Destination and visibility.** Only `tanabe0719/MaterialsDataStudio-public`
   is the default publication destination. Public visibility was explicitly authorized on 2026-10-30 after final checks, with MP database assets withdrawn and unsigned distribution disclosed. A normal user request
   to push does not authorize changing visibility. Do not add its remote to the
   private-history development checkout.
2. **History boundary.** Export the allowlisted current working files with
   `scripts/export_publication.py`. The publication repository starts at one
   root containing `.publication-root`; do not merge private ancestors, old tags,
   Actions logs, audit captures, user sessions or research inputs. Later commits
   extend this clean history normally. No `--mirror`, `--all`, force pushes, tag
   rewrites or hook bypasses.
3. **Local gates.** Install `git config core.hooksPath .githooks`. Before push,
   run `python -m scripts.validate_publication`; its cached result is bound to the
   content fingerprint. The pre-push hook checks the exact destination, clean
   history, forbidden files, workflow SHA pins, bilingual documentation, validation
   fingerprint and a pinned Gitleaks scan. Do not suppress errors.
4. **Checks.** The validation runner executes the full tests (reusing a hashed successful result only when application/test/data/Python inputs are unchanged), Ruff, source/test
   Pyright, static publication checks and documentation links. New behavior needs
   relevant tests. A GitHub quota block may use the same successful local checks;
   record it, do not claim remote CI passed. CI repeats publication checks. Local
   hooks can be bypassed by Git itself, so owner review and server-side protection
   remain necessary; these are workflow controls, not an absolute security boundary.
5. **Privacy.** No actual user paths, account/host names, unpublished measurements,
   private procedures, sessions, API tokens, keys, credentials, caches, raw DBs,
   local environments or unreviewed screenshots. Synthetic fixtures are explicitly
   allowlisted. New/changed PNGs need a privacy review; a hash match only reuses
   an already reviewed image, not an automatic copyright clearance.
6. **Rights.** Preserve component copyright/license notices, match source archives
   to actual binary versions, and keep LGPL replacement/rebuild rights. Treat
   COD/MP licenses separately from MIT. Recheck every MP record's original license
   and reject BY-NC/unknown/GNoME when rebuilding the distributable index.
7. **Bilingual user material.** Update both READMEs, paired installation/data/source
   guides, corresponding application manuals and release sections together. Keep
   user instructions organized by tasks and put development details here.
8. **Release immutability.** Verify the exact pushed tag, source correspondence,
   installer contents, library notices/sources, database notices, full `.nupkg`,
   `releases.win.json` and checksums in a draft before publishing. Existing
   published assets and version tags must not be replaced. Use a new higher version.
9. **Future updates.** Keep `packId=MaterialsDataStudio`, `channel=win`, Windows
   x64. Publish a complete full package so users can upgrade from old versions
   without a delta chain. Prepare the feed but leave acquisition disabled until
   requested. Never ship a PAT or private repository credential. Avoid build
   artifacts on ordinary source pushes; release artifacts must include sources.
10. **Latest-only listing.** Before removing an old Release, transfer its versioned
    application/corresponding-source assets to the new latest Release and verify
    them. Never delete a package referenced by the active feed. Retain source for
    any binary still offered; honor any additional source-offer retention duties.
11. **Before public visibility.** Resolve and record the pending MP current-Terms
    review and signing plan. Enable available secret protection/private reporting,
    restrict Actions/write permissions and branches/tags, and repeat the audit.
    A maintainer cannot turn readiness flags on without evidence. Visibility
    changes always require a separate explicit user instruction.
12. **Dependency security.** Before each release, review completed dependency-graph
    results against the exact locked and frozen versions. An empty response before
    the first scan finishes is not clearance. Address reported affected dependencies
    and refresh their notices/sources; verify shipped implementations and certificate
    data, not only version metadata. Rebuild with a higher version; never dismiss
    alerts merely to make the repository appear ready.

## 日本語

1. 既定の送信先は `tanabe0719/MaterialsDataStudio-public` のみ。2026-10-30に最終検査後のpublic化が明示許可された（MPのDB配布は撤回、未署名と明記）。通常のpush・Release指示を可視性変更の許可と解釈しない。開発履歴のあるツリーに公開用remoteを追加しない。
2. allowlist方式のexportを使用し、`.publication-root` を持つ単一のroot commitから履歴を始める。以降は通常のcommitを重ねる。私的履歴・旧タグ・監査原文・研究データを持ち込まない。mirror/all/force、タグ上書き、hook回避は禁止。
3. `.githooks` を設定し、`scripts/validate_publication.py` を実行する。内容fingerprintが一致する結果だけ再利用する。pre-pushで送信先・履歴・禁止ファイル・workflow SHA・日英文書・検証記録・Gitleaksを確認する。失敗を無視しない。
4. 全テスト（実装・テスト・データ・Pythonのhashが不変なら成功記録を再利用）、Ruff、実装・テストのPyright、公開用検査と文書リンクを確認する。CIのusage limit時もローカル検査を完了し、CI成功とは報告しない。Gitのhookは利用者が回避できるため、所有者のレビューとサーバー側保護を併用する。
5. 実ユーザー名・端末名・私的パス、未公開測定値、研究Procedure、セッション、鍵、token、キャッシュ、DB原本、環境、未確認画像を含めない。合成fixtureだけを明示許可する。画像の変更時はプライバシーを確認する。
6. 著作権表示・ライセンスとバイナリに対応するソースを保持し、LGPLの差替え・再ビルドの権利を妨げない。COD/MPはMITと別条件。MP再作成時は元licenseを全件確認し、BY-NC・不明・GNoMEを除外する。
7. README、導入・DB・ソース案内、対応マニュアル、Release説明を日英で揃える。利用者向けは作業順で整理し、開発情報はこの文書群へ分ける。
8. 正しいtag、ソースとの一致、Setup、告知・対応ソース、DB、fullパッケージ、feed、hashをdraftで検証してから配布する。掲載済みassetとtagは上書きせず、次の版を増加させる。
9. Velopackのpackage id、channel、x64を維持し、full更新を常に用意する。feedを準備しても取得機能は依頼まで有効にしない。PATを埋め込まない。通常のsource pushでは不要な配布バイナリをActionsへ置かない。
10. 最新Releaseだけを残す前に、旧版の対応ソース・MDSソースを新Releaseへ移し、検証する。稼働中feedが参照するファイルや、配布中バイナリの必要なソースを削除しない。適用されるsource offerの保存義務も守る。
11. public化前にMP現行規約の確認・署名方針・GitHub保護・再監査を終える。根拠なくreadinessを完了にしない。可視性の変更には別の明示指示が必要。
12. 各Releaseの前に、完了した依存関係スキャンを確認し、lockと実配布物の版に照合する。初回解析の完了前に空の応答が返っても安全確認済みと扱わない。影響する依存は修正し、告知・対応ソースを更新する。版情報だけでなく実装・証明書も検証し、版を上げて再ビルドする。見かけ上の完了のために警告を終了しない。

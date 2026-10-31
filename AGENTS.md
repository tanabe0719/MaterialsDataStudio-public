# Publication workflow / 公開用の作業規則

These instructions apply to this tree and its publication checkout.
この作業ツリーと公開用checkoutに適用する。

- The default publication destination is `tanabe0719/MaterialsDataStudio-public`.
  Keep it **private** until the user explicitly requests a visibility change.
  Never change visibility as part of a normal push or release.
- If `.publication-root` is absent, this is the development tree with private
  history. Export using `python scripts/export_publication.py`; commit and push
  **only from `.publication/MaterialsDataStudio`**, never by adding a public
  remote to the development tree. Do not mirror or push the old history/tags.
- If `.publication-root` exists, work directly in the clean publication tree.
  Read `docs/maintainers/publication_policy.md` before any push/release.
- Install `.githooks` using `git config core.hooksPath .githooks`. Do not bypass
  hooks, use force/mirror/all pushes, or overwrite published version tags/assets.
- Run `python -m scripts.validate_publication`, then
  `python scripts/publication_check.py --secrets` before pushing. Reuse a valid
  validation result only while its content fingerprint still matches.
- Keep README, installation guides, application/Graph Editor manuals and
  release notes in English and Japanese. Keep engineering details outside user
  manuals. Do not copy private development reports or raw GUI audit evidence.
- Ship the MDS license, exact dependency notices, corresponding source, full
  update package/feed, checksums and optional DB attribution with release binaries.
  Run the release verifier before publishing. Never embed GitHub/API credentials.
- Keep the existing Velopack package id/channel, increase future release versions,
  and preserve source access while its binaries remain offered. Acquisition of
  online updates was explicitly authorized for v0.15.0. Respect startup opt-out;
  require consent for downloads and applying updates; never silently auto-apply.
- GitHub CI quota failures do not justify skipping local checks. Record local
  successful checks and report the CI account limitation without changing billing.
- Requests to implement changes or push do not authorize publishing a Release.
  Normally save changes on a development branch or open a pull request. Publish
  only after an explicit instruction to formally distribute or update Releases.
- Public `main` accepts changes only through a pull request with successful CI
  checks for Python 3.12 and 3.14 against the up-to-date base. No direct pushes or
  protection bypasses; a CI quota failure leaves the PR unmerged.

「実装して」「pushして」だけではReleaseを公開せず、通常は開発ブランチへの保存・PR作成までとする。
「正式配布して」「Releasesを更新して」の明示指示があって初めて公開する。
公開用mainはPRと最新baseに対するPython 3.12・3.14のCI成功を必須とし、直接push・保護の回避は禁止。
CIの利用制限時も必須条件を緩めず、PRを未マージのまま保持する。

既存履歴の公開用remoteへのpushは禁止。研究データ、個人パス、鍵、ローカルDB、監査原文を含めない。
公開可否の未確認事項は `packaging/publication.json` に保持し、解決していない項目を完了扱いしない。

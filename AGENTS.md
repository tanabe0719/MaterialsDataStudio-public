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
  online updates is currently disabled; do not enable it unless asked.
- GitHub CI quota failures do not justify skipping local checks. Record local
  successful checks and report the CI account limitation without changing billing.

既存履歴の公開用remoteへのpushは禁止。研究データ、個人パス、鍵、ローカルDB、監査原文を含めない。
公開可否の未確認事項は `packaging/publication.json` に保持し、解決していない項目を完了扱いしない。

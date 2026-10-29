# Publication rights review / 公開準備時の確認

Reviewed 2026-10-29. The installed **Igor Pro 10 License Agreement, September 2025**
was read locally; its SHA-256 is `3175238dae902089e514990e95105a403b1c5fe57165c8fd0082ac3b83ef95f5`. Its text is not redistributed here.
The public snapshot does not include Igor software, its proprietary documentation,
activation data, private experiments or raw native-GUI audit captures. The synthetic
numerical compatibility fixtures and MDS implementation are retained. No clause
specifically prohibiting independent compatibility tests was identified in the
installed agreement; this is not a blanket permission to copy WaveMetrics material.

2026-10-29に、実際に導入されているIgor Pro 10（September 2025）の契約文を読み、上記hashを記録しました。
契約文そのものは再配布しません。Igor本体・有償文書・登録情報・私的実験・native GUI監査画面を公開用snapshotから除き、
合成の数値参照fixtureとMDS独自実装を保持します。独立した互換性テストを特に禁じる条項は当該文書では確認しませんでしたが、
WaveMetricsの素材を自由に転載してよいという意味ではありません。

## Materials Project Terms review, 2026-10-30 / MP規約確認

The owner supplied the official six-page Terms PDF; all pages were read, and pages 1-2 visually checked. Source URL: https://materialsproject.org/about/terms . PDF SHA-256: `0448bebadf2fda7ff8db5d7c31f44839f2b9451ecc9d4a35f079884fa54dca40`. The PDF is not redistributed. Its footer identifies website a1993615 / database v2026.09.28; the 2011 date concerns the privacy policy, not an established version date for all Terms.

The Terms permit CC BY 4.0 copying/adaptation with attribution and change notices, distinguish GNoME CC BY-NC, prohibit direct website scraping/API-key sharing, and request contact about acknowledgements before public repackaging/redistribution. The existing local index had 154,369 BY-C records, excluding BY-NC/unknown/GNoME. No completed contact is evidenced. The owner explicitly chose to withdraw MP database distribution and publish software + COD only. Record contact as unresolved, not waived; do not restore MP assets until reviewed. Reader/import/build code remains available. No contact was sent by the agent.

提供された公式規約の全6ページを読み、1-2ページを画像でも確認しました。CC BY 4.0の許諾に加え、公開再配布前の謝辞に関する連絡が求められています。連絡済みの証拠はなく、所有者の明示指示でMPのDB配布を撤回し、ソフトとCODのみ公開します。未解決の連絡事項は保留に残し、読込・作成機能は維持します。規約PDF・連絡先の個人情報はリポジトリへコピーしません。

## Signing decision / 署名方針

The owner explicitly chose unsigned distribution for now (2026-10-30). This is disclosed in both languages. Hash checks verify content, not publisher identity. No valid-signature flag is fabricated; Authenticode remains deferred. Existing v0.14.7 binaries, tag and source archives are not rebuilt or overwritten.

所有者は当面未署名での配布を明示選択しました。日英で説明し、hashは発行元の本人確認ではないと案内します。署名設定は未完了として保持し、既存v0.14.7のバイナリ・tag・ソースZIPは変更しません。

## Dependency security review / 依存ライブラリの安全性確認 (v0.14.8)

GitHub completed its dependency-graph scan after visibility changed and reported 46 alerts: 6 critical, 25 high, 14 moderate, 1 low. The original lock used Pillow 12.3.0, certifi 2026.7.22, urllib3 2.7.0 and setuptools 83.0.0. The owner explicitly authorized retaining public visibility and creating v0.14.8. The new lock uses Pillow 13.0.0, certifi 2026.10.23, urllib3 2.8.0 and setuptools 84.2.1; source/install minimums also exclude the affected ranges. Direct Pillow use renders a generated in-memory graph; this does not establish reachability or absence of reachability for every advisory. No alerts are dismissed as false positives. The release must be rebuilt and verified, not fixed by lockfile changes alone.

References: https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html , https://github.com/advisories/GHSA-6q48-6c7w-9gw2 , https://github.com/advisories/GHSA-vxq7-64xx-v4gw . Exact updated source identities are in packaging/corresponding_sources.json. No assertion of freedom from all vulnerabilities is made.

public化後にGitHubの解析が完了し、46件の警告が表示されました。所有者の明示指示でpublicを維持し、修正版v0.14.8を作成します。上記4依存を修正版へ更新し、影響範囲へ戻らないよう最低版も指定します。警告を誤検知として終了せず、バイナリの再作成・対応ソース・ライセンス・実配布物の照合まで行います。個々の脆弱性がMDSの全経路で悪用可能と確定したものではなく、全脆弱性の不存在を保証するものでもありません。

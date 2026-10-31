# Materials Data Studio v{{VERSION}}

[English README](https://github.com/{{REPOSITORY}}/blob/main/README.md) · [日本語README](https://github.com/{{REPOSITORY}}/blob/main/README_ja.md)

## English

**Windows 11 x64 / Windows 10 x64 · Unsigned installer.** Close MDS and run the newer Setup to upgrade. Settings, saved sessions and external databases are retained. Python and .NET are not required. Installed copies now support startup checks and in-app updates, with confirmation before downloading and restarting.

**[Download Setup](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Setup.exe)** · **[Download COD database](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/COD-Inorganic-2026-07.zip)** · [Offline manuals](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Manuals.zip) · [SHA-256 checksums](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/{{CHECKSUMS}})

### Changes in v{{VERSION}}

- Compare multiple checked or Ctrl-selected Search–Match candidates with phase-colored calculated sticks and matched markers. Shared peaks use the strongest normalized calculated reflection.
- Add all selected simulated profiles, matched markers or reflection ticks to XRD. Markers and ticks are children of the measured pattern with independent styles and visibility; positions follow background subtraction, scaling and stacking.
- Configure reflection labels, including composition, font, color and angle. Arial and the strongest index per observed peak are the defaults; labels remain editable after transfer to Graph Editor or Igor Pro. Rotation keeps the 90° text-start position fixed.
- New matched markers use a constant clearance equal to 2% of the parent maximum intensity. Ticks start 6% below the pattern with 4% phase spacing and outline width 0.1; existing saved settings are retained. Marker fill, dagger markers and separate marker stroke width are available, and up/down triangles display correctly.
- Fix zoom/style exceptions involving per-point tick sizes. Reuse display arrays, curves and labels to reduce repeated work and improve responsiveness for phase annotations.
- Graph Editor adds display-only X/Y offsets and multipliers without changing waves. Shift-drag adjusts Y; Shift+Alt-drag adjusts X, with Undo support.

### In-app updates and installation

- At startup, a newer installed-Windows release opens an update offer with release notes. Choose Download update, then Restart and update. Save confirmations are respected; running analyses/procedures and other MDS instances must be closed before applying.
- Select Do not check for updates at startup to disable startup requests; Help → Check for updates at startup restores them. Help → Check for updates… in the main window and Graph Editor always remains available. Both paths display release notes and link to the exact GitHub release.
- Versions before 0.15.0 must first install this Setup manually. Source/portable copies also require Setup for in-app updates. Settings, sessions and external DBs are retained. Downloads can be cancelled and retried; no measurement data or credentials are sent. No silent apply-on-startup.
- Retains the dependency security fixes from v0.14.8 and ships matching source and license notices.
- Existing scientific analysis features and UI layout are retained.
- Setup is **unsigned**: compare its SHA-256 with `{{CHECKSUMS}}`. A checksum verifies content, not publisher identity. Source code ZIPs and `.nupkg` are not installers. Legacy portable users should install once with Setup.
- COD is optional: import its ZIP through **XRD → Open Phase Search–Match → Reference Databases… → Install / Import ZIP…**. COD data are unchanged; retain CC BY 4.0 attribution.
- **MP database ZIPs are not distributed**, pending the pre-redistribution contact described in MP Terms. Lawfully obtained local MP indexes remain supported. This notice supersedes older MP download instructions.

### Other assets

| Asset | Purpose |
|---|---|
| `...ApplicationSource.zip` | Matching MDS source, build scripts and synthetic test fixtures |
| `...CorrespondingSources.zip` | Exact upstream sources, including LGPL/MPL components |
| `...Manuals.zip` | Offline English/Japanese application and Graph Editor manuals and guides |
| `...full.nupkg`, `releases.win.json` | Complete Velopack package/feed; for the confirmed in-app update flow |
| `REFERENCE_DATABASE_NOTICES*.txt` | Database sources, licenses and attribution |
| `release-manifest.json`, `{{CHECKSUMS}}` | Asset inventory and SHA-256 verification |
| Earlier versioned source ZIPs | Preserved application/corresponding source and synthetic fixture supplement |

MDS application code is MIT; dependencies and data retain their own licenses. MDS Match Score does not prove phase identity. [Installation details](https://github.com/{{REPOSITORY}}/blob/main/docs/installation.md).

## 日本語

**Windows 11 x64 / Windows 10 x64対応・未署名インストーラ。** MDSを終了して新しいSetupを実行すると更新できます。設定・保存したセッション・外部DBは保持します。Pythonと.NETは不要です。導入版では起動時の確認とソフト内更新を利用でき、ダウンロード・再起動の前に確認します。

**[Setupをダウンロード](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Setup.exe)** · **[CODのDBをダウンロード](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/COD-Inorganic-2026-07.zip)** · [オフラインマニュアル](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/MaterialsDataStudio-v{{VERSION}}-Manuals.zip) · [SHA-256一覧](https://github.com/{{REPOSITORY}}/releases/download/v{{VERSION}}/{{CHECKSUMS}})

### v{{VERSION}}の変更点

- Search–Matchのチェック・Ctrl操作で複数の候補相を比較し、計算スティックと一致マーカーを相別に色分けできます。共有ピークは共通スケールで計算強度が最大の相に割り当てます。
- 選択した全相のシミュレーション、一致マーカー、反射位置tickをXRDへ追加できます。マーカー・tickは実測の子として個別に表示・書式を変更でき、背景差し引き・倍率・積み重ねに追随します。
- 反射指数ラベルの組成・フォント・色・角度を設定できます。既定はArial・検出ピークごとに最強の指数1個です。Graph Editor・Igor Proへ送信後も編集でき、角度変更時は90°での文字列の始点を保持します。
- 新規の一致マーカーは親の最大強度の2%を全ピークに共通の間隔として加えます。tickは下方向6%・相間隔4%・輪郭幅0.1で追加します。保存済み設定は保持します。塗色・ダガー・独立したマーカー輪郭幅に対応し、上下三角形の向きも修正しました。
- tickの点ごとのサイズに起因する拡大・書式変更時の例外を修正しました。表示配列・曲線・文字を再利用し、相注釈の操作時の応答を改善しました。
- Graph EditorではWaveを変更せず、X/Yの表示オフセットと倍率を設定できます。Shift＋ドラッグでY、Shift＋Alt＋ドラッグでXを調整でき、Undoにも対応します。

### ソフト内更新と導入

- Windows導入版では起動時に新しい版があれば、リリースノート付きの更新案内を表示します。更新をダウンロード → 再起動して更新と進みます。保存確認のキャンセルは尊重し、解析・プロシージャの実行中や他のMDS起動中は適用しません。
- 今後は起動時にアップデートを確認しない、で起動時の通信を無効にし、Help → 起動時にアップデートを確認で再び有効にできます。メイン画面・Graph EditorのHelp → アップデートを確認…は常に利用できます。両経路でリリースノートと該当版のGitHubページを表示します。
- 0.15.0より前の版からは、最初にこのSetupで手動更新してください。ソース版・portable版も導入が必要です。設定・セッション・外部DBは保持し、ダウンロードはキャンセルして再試行できます。測定データや資格情報を送信せず、起動時の自動適用も行いません。
- v0.14.8の依存ライブラリ修正を維持し、対応するソースと告知を収録します。
- 既存の解析機能とUIの配置は維持します。
- Setupは**未署名**です。`{{CHECKSUMS}}` とSHA-256を照合してください。hashは内容の照合用で、発行者の本人確認ではありません。Source code ZIPと `.nupkg` は通常のインストーラではありません。旧portable利用者はSetupで一度導入してください。
- CODは任意です。ZIPを **XRD → Open Phase Search–Match → Reference Databases… → Install / Import ZIP…** から導入します。DB本体は変更せず、CC BY 4.0の出典表示を保持しています。
- **MPのDB ZIPは配布しません。** 規約にある再配布前の連絡を行うまで保留します。適法に入手したローカルMP索引の読込機能は維持します。旧MPダウンロード案内より、この説明を優先してください。

### その他のAssets

| ファイル | 用途 |
|---|---|
| `...ApplicationSource.zip` | 対応するMDSソース、ビルド手順、合成テストファイル |
| `...CorrespondingSources.zip` | LGPL/MPL対象を含む、版を固定した公式ソース |
| `...Manuals.zip` | 日英のアプリ・Graph Editorマニュアルとガイド |
| `...full.nupkg`, `releases.win.json` | Velopackのfull更新パッケージとfeed。確認後のソフト内更新用 |
| `REFERENCE_DATABASE_NOTICES*.txt` | DBの出典・ライセンス・帰属表示 |
| `release-manifest.json`, `{{CHECKSUMS}}` | 配布一覧とSHA-256 |
| 旧版の版付きソースZIP | 旧版のMDS・対応ソースと合成fixture補足の保持 |

MDS自作部分はMITで、外部依存とデータには個別条件が適用されます。MDS Match Scoreだけで相の存在を確定しないでください。[詳しい導入手順](https://github.com/{{REPOSITORY}}/blob/main/docs/installation_ja.md)。

# 参照データベースの導入・ライセンス

[English](reference_databases.md) | [文書一覧](index.md)

DBは任意導入で、MDSの更新後も保持します。Setupに大容量DBは含みません。
**XRD → Open Phase Search–Match → Reference Databases…** を開きます。
既定の **COD + MP + User CIF** は導入済みの各参照をまとめて検索します。

## COD Inorganic

`COD-Inorganic-2026-07.zip` を取得し、**Install / Import ZIP…** で読み込みます。
4つの対応DBを検証して登録します。登録解除によって元のダウンロードZIPは削除しません。

出典：Cuocci, Corrado; Moliterni, Anna; Altomare, Angela,
*QualX reference databases from the Crystallography Open Database (COD) for powder diffraction phase identification*。
version 2026-07、COD snapshot 2026-07-02。
[DOI 10.5281/zenodo.23297507](https://zenodo.org/records/23297507)。
加工済みQualX参照DBは **CC BY 4.0**、元のCODはCC0です。
MDS配布版ではDB本体を変更せず、日英の告知を追加しています。
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。

共有時は帰属表示・licenseリンクを保持してください。参考文献：
[QualX / Altomareほか2015](https://doi.org/10.1107/S1600576715002319)、
[COD / Gražulisほか2009](https://doi.org/10.1107/S0021889809016690)、
[Vaitkusほか2021](https://doi.org/10.1107/S1600576721000418)。
MDSは独立したreaderを使用し、QualXプログラムを同梱しません。
MDS Match ScoreはQualX FOMや相の存在確率ではありません。

## Materials Project

**MDSからMP索引は現在配布していません。** 以下は適法に入手した互換索引の読込手順です。

`MaterialsProject-2026-09-28-part1-of2.zip` と `MaterialsProject-2026-09-28-part2-of2.zip`
を同じフォルダへ、名前を変えずに保存します。**Import MP Index…** でどちらかを選びます。
両方のmanifest、復元したDBのhashを検証します。手動の展開・結合は不要です。

出典：[Materials Project AWS Open Data](https://registry.opendata.aws/materials-project/)。
summary Delta transaction 1、snapshot 2026-09-28。
配布索引は元の `builder_meta.license` がBY-Cの154,369件で、BY-NC・不明・GNoMEの制限batchは除外しています。
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)として帰属表示します。
DFT緩和構造から回折ピークと可逆なローカル索引を作成したもので、元のデータに追加の利用制限を課しません。
引用：[Jainほか2013](https://doi.org/10.1063/1.4812323)。

DFT緩和構造は実験による確認ではありません。強度はpymatgenのXRDモデルを使用します。
各ZIPのNOTICEと [MP利用規約](https://materialsproject.org/about/terms)を確認してください。
提供された公式規約を2026-10-30に確認しました。CC BY 4.0での再配布を認める一方、一般公開で再パッケージ・再配布する前に謝辞について連絡することを求めています。
この連絡は未実施のため、MDSのReleasesからMPのZIPを取り下げました。ローカルMP読込機能は維持し、制限データの再配布を許可するものではありません。

## User CIFと共有

利用・共有の権限があるデータのみを使用してください。MDSのMITは、第三者CIF、ICSD/ICDD/PDFデータベース、研究セッションの権利を付与しません。
出力した参照にも、必要な出典・ライセンス・変更表示を保持してください。

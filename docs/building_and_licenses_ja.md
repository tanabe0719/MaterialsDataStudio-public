# ライセンス・対応ソース・再ビルド

[English](building_and_licenses.md) | [利用者向け文書](index.md)

MDS自作部分はMITです（`LICENSE`）。外部ライブラリと参照データには、それぞれのライセンスが適用されます。

## 同梱ライブラリと利用者の権利

Windows版にはQt / PySide6 Essentials / shiboken6（配布モジュールはLGPLv3を選択）、
igor2（LGPL-3.0-or-later）、Gemmi・certifi（MPL-2.0）、orjson
（MPL-2.0 AND (Apache-2.0 OR MIT)）、tqdm（MPL-2.0 AND MIT）などを含みます。
`_internal/licenses/` に、版の一覧、著作権・ライセンス本文、CPython、Qtの第三者部品の告知と対応ソースの識別情報を収録します。
一覧には参考としてビルド用ツールの告知も含むため、掲載した全ツールがアプリに入っているわけではありません。

LGPLライブラリの改変・差替えと、その改変のデバッグに必要なリバースエンジニアリングをMDSの利用条件で禁止しません。
元のライセンスに従い、対象ソースを取得、変更、再構築、再結合して実行できます。
改変したライブラリの実行を妨げるための整合性制限は設けていません。

## 対応する版のソースを取得する

[Releaseページ](https://github.com/tanabe0719/MaterialsDataStudio-public/releases/latest)から、使用するインストーラと同じ版の
`MaterialsDataStudio-v<version>-CorrespondingSources.zip` と
`MaterialsDataStudio-v<version>-ApplicationSource.zip` を取得します。
前者にはQt Base、Qt SVG、PySide/shiboken、igor2、MPL対象部品などの未改変の公式ソースアーカイブ、
後者にはMDSのソースとビルド手順を含みます。元ライブラリにはMDSによる改変を加えていません。
Qtの第三者コードもソースツリーに保持します。ソース内の告知には、実行アプリに含まれないモジュールやサンプルのものもあります。

`source-manifest.json`／`packaging/corresponding_sources.json` に版、公式URL、SHA-256を固定し、
Releaseの `SHA256SUMS` で配布物を検証できます。有償サポートへの申込みは不要です。
公開準備中のため、このリポジトリとReleaseは一時的にprivateです。対応ソースへアクセスできない相手へ、この準備版バイナリを配布しないでください。

## MDSの再ビルド・ライブラリの差替え

Windows 11 x64（Windows 10 x64も対応）、Python 3.12、`uv`、インストーラ作成用の.NET 8 SDKを用意します。
導入済みアプリの実行にはPythonも.NETも不要です。ApplicationSourceを書込み可能な場所へ展開し、ルートで実行します。

```powershell
uv sync --frozen --extra dev
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -Package
```

`.build-envs/v<数字>` の環境を作成し、固定した公式ソースの取得・検証、告知収集、ビルドと検証、Setupとfull更新パッケージの作成を行います。
事前にロック済み環境を復元し、検証済みソースを `release_assets/sources/` に配置すれば、パッケージ作成時の再取得を避けられます。
依存版は `uv.lock` に固定します。PyInstallerの生成物にはbootloader例外が適用されます。

igor2を改変する場合は、展開したPythonソースを編集し、
`uv pip install --python <ビルド環境のpython> --no-deps --reinstall <改変ソースのフォルダ>`
で当該環境へ導入します。コンパイルが必要なライブラリは公式手順で互換wheelを作成し、同じ環境へ導入します。
公式手順：[Qt](https://doc.qt.io/qt-6/build-sources.html)、
[Qt for Python](https://doc.qt.io/qtforpython-6/building_from_source/index.html)。

改変版を導入した後は、`uv sync` で元の版へ戻さず、ビルド環境のPythonを直接使用します。

```powershell
$BuildPython = ".build-envs/v0150/Scripts/python.exe"
& $BuildPython -m scripts.collect_licenses
& $BuildPython -m PyInstaller --noconfirm --clean --workpath build_modified --distpath dist_modified packaging/materials_data_studio.spec
powershell -ExecutionPolicy Bypass -File packaging/build_installer.ps1 -PackDirectory dist_modified/MaterialsDataStudio -Version 0.15.0 -PythonExecutable $BuildPython
```

再配布する場合は改変に対応するソースmanifestと告知も更新し、各ライセンス条件を保持します。
ABIが互換なQt DLLなら、アプリの版ディレクトリを書込み可能な場所へコピーし、MDS終了中に差し替え、メインEXEを直接実行して検証する方法もあります。
原本を保管してください。改変版は利用者自身のビルドであり、MDS公式署名版と同一ではありません。
署名の運用によってLGPLに基づく改変の権利を制限しません。

## 外部データと名称

COD / MPは別途導入する任意の参照データで、MDSのMITとは別の条件です。
[参照DBのライセンス](reference_databases_ja.md)、各ZIPのNOTICE、提供元の条件を確認してください。
MDSは独立したアプリであり、WaveMetrics、Quantum Design、COD、QualX、Materials Projectの公認・提携製品ではありません。

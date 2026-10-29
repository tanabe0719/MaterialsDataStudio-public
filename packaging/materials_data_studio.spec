# -*- mode: python ; coding: utf-8 -*-

import os
import re
from importlib.metadata import distribution

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

project_root = os.path.abspath(os.path.join(SPECPATH, ".."))
source_root = os.path.join(project_root, "src")
datas = [
    (os.path.join(project_root, "resources"), "resources"),
    (os.path.join(project_root, "LICENSE"), "."),
    (os.path.join(project_root, "THIRD_PARTY_NOTICES.md"), "."),
    (os.path.join(project_root, "docs", "user_manual.md"), "docs"),
    (os.path.join(project_root, "docs", "user_manual_en.md"), "docs"),
    (os.path.join(project_root, "docs", "graph_viewer_manual.md"), "docs"),
    (os.path.join(project_root, "docs", "graph_viewer_manual_en.md"), "docs"),
]
# This tree is prepared from the exact build environment and checksum-pinned
# upstream Qt sources; dependency metadata alone is not a redistribution notice.
legal_root = os.path.join(project_root, ".test-tmp", "legal")
if not os.path.isfile(os.path.join(legal_root, "inventory.json")):
    raise ValueError("Run scripts/collect_licenses.py before PyInstaller")
datas.append((legal_root, "licenses"))
for user_document in ("index.md", "installation.md", "installation_ja.md",
                      "reference_databases.md", "reference_databases_ja.md",
                      "building_and_licenses.md", "building_and_licenses_ja.md"):
    datas.append((os.path.join(project_root, "docs", user_document), "docs"))
# Publish only referenced images, not unrelated or OneDrive-conflict files.
manual_images = set()
for language_file in ("user_manual.md", "user_manual_en.md", "graph_viewer_manual.md", "graph_viewer_manual_en.md"):
    with open(os.path.join(project_root, "docs", language_file), encoding="utf-8") as stream:
        manual_images.update(re.findall(r"!\[[^\]]*\]\((screenshots/[^)]+\.png)\)", stream.read()))
for relative in sorted(manual_images):
    image_path = os.path.abspath(os.path.join(project_root, "docs", relative))
    image_root = os.path.abspath(os.path.join(project_root, "docs", "screenshots"))
    if not image_path.startswith(image_root + os.sep) or not os.path.isfile(image_path):
        raise ValueError("Invalid manual screenshot reference")
    datas.append((image_path, "docs/screenshots"))
binaries = []
datas += copy_metadata("pymatgen")
# Figure.savefig selects vector backends dynamically. Include them explicitly
# so PDF/SVG export works in the frozen application, not only in development.
hiddenimports = ["matplotlib.backends.backend_pdf", "matplotlib.backends.backend_svg", "matplotlib.backends.backend_ps"]
# PyInstaller's standard hooks collect Qt, Matplotlib, NumPy, SciPy, pandas,
# h5py, and extension binaries from imports. Pymatgen has a very broad optional
# module tree, so collecting the whole distribution also bundled Plotly,
# chemistry-environment databases, examples, and unrelated simulation formats.
# Only the data used by Element, symmetry/CIF parsing, and powder XRD is needed.
datas += collect_data_files(
    "pymatgen",
    includes=[
        "py.typed",
        "core/*.json",
        "core/*.json.gz",
        "core/*.yaml",
        "symmetry/*.json",
        "symmetry/*.yaml",
        "symmetry/*.sqlite",
        "analysis/diffraction/*.json",
    ],
)
# h5py ships an official PyInstaller hook that collects its compiled extensions and
# HDF5 DLLs. Avoid collect_all here because it would also bundle h5py's test suite.
h5py_distribution = distribution("h5py")
for license_file in h5py_distribution.files or ():
    normalized = str(license_file).replace("\\", "/")
    marker = ".dist-info/licenses/"
    if marker not in normalized:
        continue
    relative = normalized.split(marker, 1)[1]
    datas.append(
        (
            str(h5py_distribution.locate_file(license_file)),
            os.path.join("licenses", "h5py", os.path.dirname(relative)),
        )
    )
igor2_distribution = distribution("igor2")
for license_file in igor2_distribution.files or ():
    normalized = str(license_file).replace("\\", "/")
    if not normalized.endswith("licenses/LICENSE"):
        continue
    datas.append((str(igor2_distribution.locate_file(license_file)), "licenses/igor2"))

a = Analysis(
    [os.path.join(source_root, "materials_data_studio", "__main__.py")],
    pathex=[source_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Optional visualization, notebook, test, and OpenGL trees are not used by
    # the desktop application. Explicit exclusions prevent transitive imports
    # in pymatgen/pyqtgraph from restoring hundreds of MiB removed above.
    excludes=[
        "tkinter",
        "pytest",
        "_pytest",
        "plotly",
        "narwhals",
        "IPython",
        "jupyter",
        "notebook",
        "OpenGL",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MaterialsDataStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=os.path.join(project_root, "resources", "icons", "materials-data-studio.ico"),
    version=os.path.join(project_root, "packaging", "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="MaterialsDataStudio",
)

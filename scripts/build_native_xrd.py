"""Build the approved XRD Rust library for Windows x64, without Cargo dependencies."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "x86_64-pc-windows-gnu"
TOOLCHAIN = "1.95.0-" + TARGET
XRD = ROOT / "src/materials_data_studio/modules/xrd"


def main() -> None:
    rustup = shutil.which("rustup")
    if rustup is None:
        raise SystemExit(f"Install Rustup, then run: rustup toolchain install {TOOLCHAIN} --profile minimal")
    found = subprocess.run([rustup, "which", "--toolchain", TOOLCHAIN, "rustc"], capture_output=True, text=True)
    if found.returncode:
        raise SystemExit(f"Install the pinned toolchain: rustup toolchain install {TOOLCHAIN} --profile minimal")
    rustc = Path(found.stdout.strip()).resolve()
    toolchain = rustc.parents[1]
    linker = toolchain / f"lib/rustlib/{TARGET}/bin/rust-lld.exe"
    if not linker.is_file():
        raise SystemExit("The pinned Rust toolchain does not contain its self-contained linker.")
    with tempfile.TemporaryDirectory(prefix=".mds-xrd-", dir=ROOT / ".test-tmp") as directory:
        output = Path(directory) / "mds_xrd.dll"
        subprocess.run([
            str(rustc), "--target", TARGET, "--crate-type", "cdylib", "-O", "-C", "panic=abort",
            "-C", f"linker={linker}", "-C", "linker-flavor=ld.lld", "-C", "strip=debuginfo",
            "--remap-path-prefix", f"{ROOT}=.", "-o", str(output), str(XRD / "native_xrd.rs"),
        ], cwd=ROOT, check=True)
        output.replace(XRD / output.name)
    notices = ROOT / ".test-tmp/native-xrd-notices"
    source_notices = toolchain / "share/doc/rust"
    notices.mkdir(parents=True, exist_ok=True)
    for name in ("COPYRIGHT-library.html", "COPYRIGHT.html"):
        shutil.copyfile(source_notices / name, notices / name)
    shutil.copytree(source_notices / "licenses", notices / "licenses", dirs_exist_ok=True)


if __name__ == "__main__":
    (ROOT / ".test-tmp").mkdir(exist_ok=True)
    main()

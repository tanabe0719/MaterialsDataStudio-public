"""Synthetic Windows path aliases must not weaken AppContainer path checks."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from materials_data_studio.tools.procedure_sandbox import (
    SandboxUnavailable,
    canonical_sandbox_root,
)


def test_canonical_sandbox_root_is_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "generated private root"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    assert canonical_sandbox_root(Path(root.name)) == root.resolve()
    with pytest.raises(FileNotFoundError):
        canonical_sandbox_root(tmp_path / "missing")


@pytest.fixture
def short_root(tmp_path: Path):
    root = tmp_path / "generated sandbox root with spaces"
    root.mkdir()
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(str(root), buffer, len(buffer))
    assert 0 < length < len(buffer), ctypes.WinError(ctypes.get_last_error())
    short = Path(buffer.value)
    if short == root:
        pytest.skip("8.3 name generation is disabled on this volume")
    return root, short


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 8.3 aliases")
def test_canonical_sandbox_root_accepts_real_short_name(short_root):
    root, short = short_root
    (root / "output").mkdir()
    # This was the broker's old comparison: it falsely rejected a regular dir.
    assert (short / "output").resolve() != short / "output"
    canonical = canonical_sandbox_root(short)
    assert canonical == root.resolve()
    assert (canonical / "output").resolve() == canonical / "output"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")
def test_short_root_worker_roundtrip(short_root):
    from materials_data_studio.tools.graph_document import (
        EditableGraphDocument,
        load_graph_document,
        save_graph_document,
    )
    from materials_data_studio.tools.procedure_sandbox import launch
    from materials_data_studio.tools.pxp_model import PxpDocument

    root, short = short_root
    (root / "input").mkdir()
    (root / "output").mkdir()
    document = EditableGraphDocument.from_pxp(
        PxpDocument("generated", "synthetic.pxp", "", 0, "10", "synthetic", "", status="FULL")
    )
    save_graph_document(root / "input/input.mdsg", document)
    (root / "input/procedure.py").write_text(
        'from mds import wave\nwave.create("shortPathResult",[1.,2.,3.])', encoding="utf-8",
    )
    assert launch(short) == 0
    restored = load_graph_document(root / "output/output.mdsg")
    wave = restored.render_source().wave("shortPathResult")
    assert wave is not None and wave.data.tolist() == [1., 2., 3.]
    assert not (root / "sandbox.json").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
@pytest.mark.parametrize("nested", [False, True])
def test_canonical_sandbox_root_rejects_real_junction(tmp_path: Path, nested: bool):
    target = tmp_path / "generated target"
    target.mkdir()
    link = tmp_path / "generated junction"
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert link.is_junction()
    root = link
    if nested:
        (target / "child").mkdir()
        root = link / "child"
    with pytest.raises(SandboxUnavailable, match="link/junction"):
        canonical_sandbox_root(root)

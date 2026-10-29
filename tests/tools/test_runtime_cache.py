"""A scientific runtime snapshot cannot be reused by a different Python environment."""
from __future__ import annotations

import sys

from materials_data_studio.tools.procedure_sandbox import runtime_identity


def test_isolated_runtime_cache_separates_python_and_source(monkeypatch, tmp_path):
    first = runtime_identity(tmp_path, "source-a")
    assert first == runtime_identity(tmp_path, "source-a")
    assert first != runtime_identity(tmp_path, "source-b")
    monkeypatch.setattr(sys, "version", "different Python ABI")
    assert first != runtime_identity(tmp_path, "source-a")
    monkeypatch.undo()
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "other-environment"))
    assert first != runtime_identity(tmp_path, "source-a")

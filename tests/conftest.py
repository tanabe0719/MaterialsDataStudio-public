"""Test configuration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "materials-data-studio-matplotlib"),
)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "MDS_RECOVERY_DIRECTORY",
    str(Path(tempfile.gettempdir()) / "materials-data-studio-test-recovery"),
)

@pytest.fixture(autouse=True)
def isolated_application_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GUI tests and child smoke processes never change the user's preferences."""
    monkeypatch.setenv("MDS_SETTINGS_DIRECTORY", str(tmp_path / "settings"))

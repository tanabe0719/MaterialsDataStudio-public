"""Repository helpers remain importable through both supported pytest launchers."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("launcher", ["console", "module"])
def test_pytest_entrypoint_collects_documentation_helpers(launcher, tmp_path):
    root = Path(__file__).resolve().parents[1]
    executable = Path(sys.executable).with_name("pytest.exe" if os.name == "nt" else "pytest")
    command = [str(executable)] if launcher == "console" else [sys.executable, "-m", "pytest"]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTEST_ADDOPTS"] = ""
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [*command, "tests/tools/test_manual_capture_privacy.py", "--collect-only", "-q",
         "--no-cov", "-o", f"cache_dir={tmp_path / 'child-cache'}"],
        cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 tests collected" in result.stdout

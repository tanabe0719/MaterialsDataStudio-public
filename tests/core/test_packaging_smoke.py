from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_packaging_smoke_exercises_lazy_dependencies(tmp_path: Path) -> None:
    report = tmp_path / "smoke.txt"
    script = (
        "from pathlib import Path; "
        "from materials_data_studio.core.packaging_smoke import run_packaging_smoke_test; "
        f"raise SystemExit(0 if run_packaging_smoke_test(Path({str(report)!r})) else 1)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        # Native sandbox preparation/font caching can exceed two minutes on
        # Windows; use the same budget as the actual frozen-build smoke gate.
        timeout=300,
    )
    text = report.read_text(encoding="utf-8") if report.exists() else "Smoke report was not written."
    assert completed.returncode == 0, "\n".join((text, completed.stdout, completed.stderr))
    assert text.startswith("OK\n")
    assert "reflections=" in text
    assert "profile_points=" in text
    assert "mdsg_roundtrip=OK" in text
    assert "graph_exports=PNG,SVG,PDF,EPS,BMP,JPEG,TIFF" in text
    assert "selection_properties_lifetime=OK" in text
    assert "igor_conversion_and_isolated_runtime=OK" in text
    assert "isolated_public_mds_command=OK" in text
    assert "local_cif_phase_search_match=OK" in text
    assert "phase_search_gui_session=OK" in text
    assert "qualx_sqlite_offline_reference_search=OK" in text
    assert "materials_project_offline_search_and_session=OK" in text

"""Run the local publication gate and bind successful checks to the current files."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

from scripts.publication_check import ROOT, VALIDATION, fingerprint, static_checks, tracked


def functional_fingerprint() -> str:
    """Bind functional results to app/tests/data, while checking publishing code separately."""
    value = hashlib.sha256(sys.version.encode())
    for name in sorted(tracked()):
        functional = (name.startswith(("src/", "tests/", "resources/", "docs/demo_data/", "docs/igor_audit/"))
                      or name in {"pyproject.toml", "uv.lock", "scripts/package_mp_index.py", "scripts/package_reference_distributions.py"})
        if functional and name != "tests/test_publication_safety.py":
            value.update(name.encode() + b"\0" + hashlib.sha256((ROOT / name).read_bytes()).digest())
    return value.hexdigest()


def main() -> None:
    static_checks()
    before = fingerprint()
    functional_before = functional_fingerprint()
    functional_cache = ROOT / ".test-tmp/publication-functional-validation.json"
    task_temp = ROOT / ".test-tmp" / ("validation-" + uuid.uuid4().hex)
    task_temp.mkdir(parents=True)
    commands = [
        [sys.executable, "-m", "pytest", "--basetemp", str(task_temp / "pytest"), "-o", "cache_dir=" + str(task_temp / "pytest-cache"), "--cov=materials_data_studio", "--cov-report=term-missing", "--cov-fail-under=80", "--maxfail=1", "-q"],
        [sys.executable, "-m", "pytest", "tests/test_publication_safety.py", "tests/gui/test_license_notice.py", "tests/tools/test_graph_help_manual.py", "--basetemp", str(task_temp / "publication-pytest"), "-o", "cache_dir=" + str(task_temp / "publication-cache"), "--maxfail=1", "-q"],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "pyright"],
        [sys.executable, "-m", "pyright", "--project", "pyright-tests.json"],
        [sys.executable, "scripts/build_manual.py"],
        [sys.executable, "scripts/check_visual_performance.py", "--report", ".test-tmp/publication-visual-performance.json"],
    ]
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", TEMP=str(task_temp), TMP=str(task_temp),
                       COVERAGE_FILE=str(task_temp / ".coverage"), MPLCONFIGDIR=str(task_temp / "matplotlib"))
    VALIDATION.parent.mkdir(exist_ok=True)
    records = []
    for index, command in enumerate(commands, 1):
        if index == 1 and functional_cache.is_file():
            cached = json.loads(functional_cache.read_text(encoding="utf-8"))
            old_log = VALIDATION.parent / cached["log"]
            if (cached.get("fingerprint") == functional_before and old_log.is_file()
                    and hashlib.sha256(old_log.read_bytes()).hexdigest() == cached["log_sha256"]):
                records.append({"command": command[1:], "exit_code": 0, "log": cached["log"], "reused": True})
                print("Reused successful functional suite: app/tests/data and Python fingerprint unchanged", flush=True)
                continue
        path = VALIDATION.parent / f"publication-validation-{index}.log"
        print("Running " + " ".join(command[1:]), flush=True)
        with path.open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        records.append({"command": command[1:], "exit_code": result.returncode, "log": path.name})
        if result.returncode:
            print(path.read_text(encoding="utf-8", errors="replace")[-12000:])
            raise SystemExit(result.returncode)
        if index == 1:
            if functional_before != functional_fingerprint():
                raise RuntimeError("Functional inputs changed while tests ran")
            functional_cache.write_text(json.dumps({"fingerprint": functional_before, "log": path.name,
                                        "log_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, indent=2) + "\n", encoding="utf-8")
    if before != fingerprint():
        raise RuntimeError("Publication files changed while validation was running")
    result = {"format": 1, "fingerprint": before, "passed": True, "checked_at_utc": datetime.now(UTC).isoformat(), "checks": records}
    VALIDATION.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("All local publication checks passed", flush=True)


if __name__ == "__main__":
    main()

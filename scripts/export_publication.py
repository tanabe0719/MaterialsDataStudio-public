"""Export only the current allowlisted files; never copy Git history or local data."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".publication/MaterialsDataStudio"
ROOT_FILES = {"AGENTS.md", "README.md", "README_ja.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md",
              ".gitignore", ".gitattributes", "pyproject.toml", "uv.lock", "pyright-tests.json"}
DOC_FILES = {"index.md", "installation.md", "installation_ja.md", "reference_databases.md", "reference_databases_ja.md",
             "building_and_licenses.md", "building_and_licenses_ja.md", "user_manual.md", "user_manual_en.md",
             "graph_viewer_manual.md", "graph_viewer_manual_en.md", "command_reference.md", "screenshot_plan.md",
             "manual_build_instructions.md", "phase_search/materials_project.md"}
FIXTURES = {"docs/igor_audit/evidence/mds/native_wave_reference.json", "docs/igor_audit/evidence/igor/rainbow.tsv",
            "docs/igor_audit/evidence/igor/top-axis.txt", "docs/igor_audit/evidence/igor/mixed.txt",
            "docs/igor_audit/evidence/igor/analysis-info.txt", "docs/igor_audit/evidence/igor/analysis-waves.tsv",
            "docs/igor_audit/exploration/evidence/native/boxes_after_gui.txt"}
OMIT = {"scripts/replace_published_installer.py", "tests/test_release_replacement.py",
        "scripts/release_assets.py", "tests/test_release_assets.py"}


def selected_files() -> list[str]:
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    # Explicit publication files may be newly authored in the development tree.
    candidates = set(tracked)
    candidates.update(subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", "src", "tests", "resources"], cwd=ROOT, text=True).splitlines())
    for prefix in ("scripts", "packaging", ".github", ".githooks", "docs/maintainers"):
        candidates.update(p.relative_to(ROOT).as_posix() for p in (ROOT / prefix).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    candidates.update(p for p in ROOT_FILES if (ROOT / p).is_file())
    candidates.update("docs/" + p for p in DOC_FILES if (ROOT / "docs" / p).is_file())
    images = set()
    for name in ("user_manual.md", "user_manual_en.md", "graph_viewer_manual.md", "graph_viewer_manual_en.md"):
        images.update("docs/" + s for s in re.findall(r"!\[[^\]]*\]\((screenshots/[^)]+\.png)\)", (ROOT / "docs" / name).read_text(encoding="utf-8")))
    chosen = []
    for name in sorted(candidates):
        if name in OMIT:
            continue
        keep = (name in ROOT_FILES or name in FIXTURES or name in images or
                name.startswith(("src/", "tests/", "resources/", "scripts/", "packaging/", ".github/", ".githooks/", "docs/demo_data/", "docs/maintainers/")) or
                (name.startswith("docs/") and name[5:] in DOC_FILES))
        if keep and (ROOT / name).is_file():
            chosen.append(name)
    return chosen


def export() -> None:
    if (ROOT / ".publication-root").exists():
        raise RuntimeError("This is already the publication checkout; edit and commit here directly")
    if not TARGET.resolve().is_relative_to((ROOT / ".publication").resolve()):
        raise RuntimeError("Export target escaped publication workspace")
    TARGET.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "packaging/publication.json").read_text(encoding="utf-8"))
    if (TARGET / ".git").exists():
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=TARGET, text=True)
        if status.strip():
            raise RuntimeError("Publication checkout has uncommitted work; preserve it before exporting")
    previous = TARGET / ".publication-files.json"
    names = selected_files()
    if previous.is_file():
        for old in json.loads(previous.read_text(encoding="utf-8")):
            if old not in names:
                path = (TARGET / old).resolve()
                if not path.is_relative_to(TARGET.resolve()) or ".git" in path.relative_to(TARGET).parts:
                    raise RuntimeError("Unsafe previous export entry")
                path.unlink(missing_ok=True)
    for name in names:
        target = TARGET / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (TARGET / ".publication-root").write_text(config["baseline_id"] + "\n", encoding="utf-8")
    previous.write_text(json.dumps(names, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(names)} current files, without Git history, to {TARGET}")


if __name__ == "__main__":
    export()

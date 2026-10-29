"""Fail closed on private-history exports, unsafe publication files and wrong remotes."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".lock", ".yml", ".yaml", ".txt", ".ps1", ".spec", ".ipf", ".tsv", ".csv", ".xy", ".dat", ".svg"}
ALLOWED_FIXTURES = {"tests/reference_igor/v10_03_discovery/labels.h5xp", "tests/reference_igor/v10_03_p2/mds_generated.h5xp"}
VALIDATION = ROOT / ".test-tmp/publication-validation.json"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def config() -> dict:
    return json.loads((ROOT / "packaging/publication.json").read_text(encoding="utf-8"))


def tracked() -> list[str]:
    return git("ls-files").splitlines()


def fingerprint() -> str:
    value = hashlib.sha256()
    for name in sorted(tracked()):
        path = ROOT / name
        value.update(name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return value.hexdigest()


def static_checks() -> None:
    settings = config()
    if (ROOT / ".publication-root").read_text(encoding="utf-8").strip() != settings["baseline_id"]:
        raise ValueError("Missing audited publication root")
    module = ast.parse((ROOT / "src/materials_data_studio/version.py").read_text(encoding="utf-8"))
    versions = [ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)]
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if versions != [settings["version"]] or project_version != settings["version"]:
        raise ValueError("Publication and application versions differ")
    errors = []
    images = json.loads((ROOT / "packaging/reviewed_images.json").read_text(encoding="utf-8"))["sha256"]
    for name in tracked():
        path = ROOT / name
        parts = Path(name).parts
        suffix = path.suffix.lower()
        if (any(p in {"sample", "from_user", "user_data", ".venv", ".build-envs", ".test-tmp", "__pycache__"} for p in parts)
                or suffix in {".pem", ".pfx", ".p12", ".key", ".sqlite", ".pxp", ".mdss", ".mdsg", ".exe", ".dll", ".zip", ".log"}
                or (suffix == ".h5xp" and name not in ALLOWED_FIXTURES)
                or path.name == ".env" or path.name.startswith(".env.")):
            errors.append("Forbidden publication file: " + name)
        if suffix == ".png" and images.get(name) != hashlib.sha256(path.read_bytes()).hexdigest():
            errors.append("PNG needs privacy/provenance review: " + name)
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for match in re.finditer(r"[A-Z]:[\\/]+Users[\\/]+([^\\/\s<>\"']+)", text, re.I):
                if match.group(1).lower() not in {"private", "user", "username", "example", "public", "default"}:
                    errors.append("Personal Windows path: " + name)
            if re.search(r"\b(?:LAPTOP|DESKTOP)-[A-Z0-9]{5,}\b", text):
                errors.append("Personal host name: " + name)
        if name.startswith(".github/workflows/") and suffix in {".yml", ".yaml"}:
            text = path.read_text(encoding="utf-8")
            if "pull_request_target:" in text:
                errors.append("Privileged pull_request_target is forbidden: " + name)
            for action in re.findall(r"uses:\s*([^\s#]+)", text):
                if not action.startswith("./") and not re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action):
                    errors.append("Action must be pinned to a full commit SHA: " + action)
    for name in tracked():
        if not name.endswith(".md"):
            continue
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        for link in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", text):
            if link.startswith(("https://", "http://", "mailto:", "#")):
                continue
            relative = link.split("#", 1)[0]
            if relative and not (path.parent / relative).is_file():
                errors.append(f"Broken local documentation link: {name}: {relative}")
    for left, right in [("README.md", "README_ja.md"), ("docs/installation.md", "docs/installation_ja.md"),
                        ("docs/reference_databases.md", "docs/reference_databases_ja.md"),
                        ("docs/building_and_licenses.md", "docs/building_and_licenses_ja.md"),
                        ("docs/user_manual_en.md", "docs/user_manual.md"), ("docs/graph_viewer_manual_en.md", "docs/graph_viewer_manual.md")]:
        if not (ROOT / left).is_file() or not (ROOT / right).is_file():
            errors.append("Missing bilingual pair: " + left)
    if errors:
        raise ValueError("\n".join(sorted(set(errors))))
    print(f"Publication file and documentation checks passed ({len(tracked())} files)")


def history_checks() -> None:
    roots = git("rev-list", "--all", "--max-parents=0").splitlines()
    if len(roots) != 1:
        raise ValueError("Publication history must have exactly one clean root")
    if git("show", roots[0] + ":.publication-root") != config()["baseline_id"]:
        raise ValueError("Private development history must not be pushed")


def gitleaks() -> None:
    settings = config()["gitleaks"]
    if os.name != "nt":
        raise RuntimeError("Use the Windows publication validation environment")
    folder = ROOT / ".tools/gitleaks"
    executable = folder / "gitleaks.exe"
    if not executable.is_file() or hashlib.sha256(executable.read_bytes()).hexdigest() != settings["windows_executable_sha256"]:
        folder.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/gitleaks/gitleaks/releases/download/v{settings['version']}/gitleaks_{settings['version']}_windows_x64.zip"
        archive = folder / "download.zip"
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as target:
            while chunk := response.read(1024**2):
                target.write(chunk)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != settings["windows_archive_sha256"]:
            raise ValueError("Gitleaks archive checksum mismatch")
        with zipfile.ZipFile(archive) as source:
            executable.write_bytes(source.read("gitleaks.exe"))
        if hashlib.sha256(executable.read_bytes()).hexdigest() != settings["windows_executable_sha256"]:
            raise ValueError("Gitleaks executable checksum mismatch")
    output = ROOT / ".test-tmp"
    output.mkdir(exist_ok=True)
    subprocess.run([str(executable), "git", ".", "--log-opts=--all --full-history", "--redact=100", "--no-banner",
                    "--report-format=json", "--report-path=" + str(output / "gitleaks-publication.json")], cwd=ROOT, check=True)


def check_push(remote: str, url: str) -> None:
    expected = config()["repository"]
    normalized = url.removesuffix(".git").rstrip("/")
    if normalized not in {f"https://github.com/{expected}", f"git@github.com:{expected}", f"ssh://git@github.com/{expected}"}:
        raise ValueError("Push destination is not the approved publication repository")
    if git("status", "--porcelain"):
        raise ValueError("Commit all reviewed changes before pushing")
    metadata = json.loads(subprocess.check_output(["gh", "api", f"repos/{expected}"], text=True, encoding="utf-8"))
    if metadata["full_name"] != expected or (not metadata["private"] and not config()["allow_public_repository"]):
        raise ValueError("Repository visibility is not approved by the publication policy")
    for line in sys.stdin:
        if not line.strip():
            continue
        local_ref, local_sha, remote_ref, remote_sha = line.split()
        if set(local_sha) == {"0"}:
            raise ValueError("Ref deletion is not allowed by the publication hook")
        if remote_ref.startswith("refs/tags/") and set(remote_sha) != {"0"} and local_sha != remote_sha:
            raise ValueError("Published tag updates are forbidden")
        if local_ref.startswith("refs/tags/") and local_ref != "refs/tags/v" + config()["version"]:
            raise ValueError("Only the current version tag may be pushed")
    result = json.loads(VALIDATION.read_text(encoding="utf-8"))
    if result.get("fingerprint") != fingerprint() or not result.get("passed"):
        raise ValueError("Run validate_publication.py for the current content before push")
    print(f"Approved push target: {remote}: {expected} ({metadata['visibility']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets", action="store_true")
    parser.add_argument("--pre-push", nargs=2, metavar=("REMOTE", "URL"))
    args = parser.parse_args()
    static_checks()
    history_checks()
    if args.pre_push:
        check_push(*args.pre_push)
    if args.secrets or args.pre_push:
        gitleaks()


if __name__ == "__main__":
    main()

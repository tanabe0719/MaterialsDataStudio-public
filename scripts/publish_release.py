"""Assemble/verify an immutable, bilingual release for the approved repository."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def policy() -> dict:
    return json.loads((ROOT / "packaging/publication.json").read_text(encoding="utf-8"))


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], cwd=ROOT, text=True, encoding="utf-8")


def api(endpoint: str, *, method: str = "GET", payload: dict | None = None):
    if payload is None:
        response = gh("api", "--method", method, endpoint)
        return json.loads(response) if response.strip() else None
    folder = ROOT / ".test-tmp"
    folder.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", dir=folder, delete=False) as stream:
        json.dump(payload, stream)
        path = Path(stream.name)
    try:
        return json.loads(gh("api", "--method", method, endpoint, "--input", str(path)))
    finally:
        path.unlink(missing_ok=True)


def target_repository() -> dict:
    config = policy()
    repo = api("repos/" + config["repository"])
    if repo["full_name"] != config["repository"]:
        raise ValueError("Wrong publication repository")
    if not repo["private"] and (not config["allow_public_repository"] or not all(config["public_readiness"].values())):
        raise ValueError("Public distribution readiness is not approved")
    if (not repo["private"] and config.get("distribute_materials_project", False)
            and not config.get("deferred_items", {}).get("materials_project_redistribution_contact_completed", False)):
        raise ValueError("MP redistribution contact is not completed")
    return repo


def validate_signature(signature: str, config: dict) -> None:
    """Allow only explicitly reviewed unsigned distribution or a valid signature."""
    if signature == "Valid":
        return
    if signature == "NotSigned" and config.get("signing_policy") == "unsigned-disclosed":
        return
    raise ValueError("Installer signature does not satisfy the reviewed signing policy")


def identity() -> tuple[str, str]:
    marker = ROOT / ".publication-root"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != policy()["baseline_id"]:
        raise ValueError("Release only from the clean publication checkout, never the private-history development tree")
    version = policy()["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid release version")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tagged = subprocess.check_output(["git", "rev-parse", "v" + version + "^{}"], cwd=ROOT, text=True).strip()
    if commit != tagged or subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise ValueError("Release must use the clean exact version-tag checkout")
    return version, commit


def validate_feed(folder: Path, version: str) -> None:
    feed = json.loads((folder / "releases.win.json").read_text(encoding="utf-8-sig"))
    assets = feed["Assets"]
    if len(assets) != 1:
        raise ValueError("A single complete full-package feed is required")
    item = assets[0]
    expected = f"MaterialsDataStudio-{version}-full.nupkg"
    if (item["PackageId"] != "MaterialsDataStudio" or item["Version"] != version or item["Type"] != "Full" or item["FileName"] != expected):
        raise ValueError("Update feed identity mismatch")
    package = folder / expected
    if package.stat().st_size != item["Size"] or digest(package) != item["SHA256"].lower():
        raise ValueError("Update feed hash/size mismatch")
    with package.open("rb") as stream:
        if hashlib.file_digest(stream, "sha1").hexdigest() != item["SHA1"].lower():
            raise ValueError("Update feed SHA1 mismatch")


def assemble() -> Path:
    version, commit = identity()
    folder = ROOT / "release_assets"
    folder.mkdir(exist_ok=True)
    references = json.loads((ROOT / "packaging/reference_assets.json").read_text(encoding="utf-8"))
    for item in references:
        if item["name"].startswith("MaterialsProject-") and not policy().get("distribute_materials_project", False):
            continue
        path = folder / item["name"]
        if Path(item["name"]).name != item["name"] or path.stat().st_size != item["size"] or "sha256:" + digest(path) != item["digest"]:
            raise ValueError("Reference asset differs from the reviewed source manifest")
    validate_feed(folder, version)
    app_source = folder / f"MaterialsDataStudio-v{version}-ApplicationSource.zip"
    subprocess.run(["git", "archive", "--format=zip", "--output=" + str(app_source), "HEAD"], cwd=ROOT, check=True)
    manual_archive = folder / f"MaterialsDataStudio-v{version}-Manuals.zip"
    sys.path.insert(0, str(ROOT))
    from scripts import build_manual
    output_names = {source.resolve(): target.name for source, target, _language, _title in build_manual.MANUALS}
    template = (ROOT / "dist_manual/Materials_Data_Studio_User_Manual_EN.html").read_text(encoding="utf-8")
    with zipfile.ZipFile(manual_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manuals = list((ROOT / "dist_manual").glob("*.html"))
        if len(manuals) != 4:
            raise ValueError("Build all four localized HTML manuals first")
        for source in sorted((ROOT / "docs").rglob("*.md")):
            build_manual.SOURCE = source
            content = build_manual.render_markdown(source.read_text(encoding="utf-8"))
            for original, target in output_names.items():
                content = content.replace('href="' + original.name, 'href="' + target)
            content = re.sub(r'href="([^"?#]+)\.md([#?][^"]*)?"', lambda m: 'href="' + m[1] + '.html' + (m[2] or '') + '"', content)
            page = template.split("<body>", 1)[0] + "<body>" + content + "</body></html>"
            title = source.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
            page = re.sub(r"<title>.*?</title>", lambda _m, title=title: "<title>" + html.escape(title) + "</title>", page)
            name = output_names.get(source.resolve(), source.relative_to(ROOT / "docs").with_suffix(".html").as_posix())
            archive.writestr(name, page)
    names = [f"MaterialsDataStudio-v{version}-Setup.exe", f"MaterialsDataStudio-v{version}-Setup.exe.sha256",
             f"MaterialsDataStudio-{version}-full.nupkg", "releases.win.json", app_source.name, manual_archive.name,
             f"MaterialsDataStudio-v{version}-CorrespondingSources.zip", "COD-Inorganic-2026-07.zip", "COD-Inorganic-2026-07.zip.sha256",
             "MaterialsProject-2026-09-28-part1-of2.zip", "MaterialsProject-2026-09-28-part1-of2.zip.sha256",
             "MaterialsProject-2026-09-28-part2-of2.zip", "MaterialsProject-2026-09-28-part2-of2.zip.sha256",
             "REFERENCE_DATABASE_NOTICES.txt", "REFERENCE_DATABASE_NOTICES_ja.txt", "reference-payload-verification.json"]
    # Retain versioned corresponding/application source previously offered with
    # binaries when using a latest-only release listing.
    names += [p.name for p in folder.glob("MaterialsDataStudio-v*-*Source*.zip") if p.name not in names]
    names = [name for name in names if not name.startswith("MaterialsProject-") or policy().get("distribute_materials_project", False)]
    items = []
    for name in sorted(set(names)):
        path = folder / name
        if not path.is_file() or not 0 < path.stat().st_size < 2 * 1024**3:
            raise ValueError("Missing or oversized release asset: " + name)
        items.append({"name": name, "bytes": path.stat().st_size, "sha256": digest(path)})
    result = {"format": 1, "version": version, "source_commit": commit, "repository": policy()["repository"],
              "package_id": "MaterialsDataStudio", "channel": "win", "runtime": "win-x64",
              "online_acquisition_enabled": False, "signature": "unsigned-private-preparation", "assets": items}
    signature = json.loads((folder / "signature.json").read_text(encoding="utf-8-sig"))
    if signature["sha256"] != next(i["sha256"] for i in items if i["name"].endswith("-Setup.exe")):
        raise ValueError("Signature record belongs to another installer")
    result["signature"] = signature["authenticode"]
    manifest = folder / "release-manifest.json"
    manifest.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    sums = [i["sha256"] + "  " + i["name"] for i in items] + [digest(manifest) + "  " + manifest.name]
    (folder / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="ascii")
    verify_local()
    return manifest


def verify_local() -> dict:
    folder = ROOT / "release_assets"
    manifest = json.loads((folder / "release-manifest.json").read_text(encoding="utf-8"))
    version, commit = identity()
    if manifest["version"] != version or manifest["source_commit"] != commit or manifest["repository"] != policy()["repository"]:
        raise ValueError("Release manifest does not match source identity")
    sums = {}
    for line in (folder / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        checksum, name = line.split("  ", 1)
        if name in sums or Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Invalid release checksum list")
        sums[name] = checksum
    expected = {i["name"]: i["sha256"] for i in manifest["assets"]}
    expected["release-manifest.json"] = digest(folder / "release-manifest.json")
    if sums != expected:
        raise ValueError("Checksum list differs from release manifest")
    for item in manifest["assets"]:
        name = item["name"]
        if Path(name).name != name or item["bytes"] != (folder / name).stat().st_size or digest(folder / name) != item["sha256"]:
            raise ValueError("Release asset mismatch: " + name)
    validate_feed(folder, version)
    if (folder / f"MaterialsDataStudio-v{version}-Setup.exe.sha256").read_text(encoding="ascii").split() != [sums[f"MaterialsDataStudio-v{version}-Setup.exe"], f"MaterialsDataStudio-v{version}-Setup.exe"]:
        raise ValueError("Installer checksum pair mismatch")
    print(f"Verified {len(expected) + 1} local release assets", flush=True)
    return manifest


def all_releases(repository: str) -> list[dict]:
    items = []
    page = 1
    while True:
        batch = api(f"repos/{repository}/releases?per_page=100&page={page}")
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def retain_sources(repository: str, releases: list[dict]) -> None:
    folder = ROOT / "release_assets"
    folder.mkdir(exist_ok=True)
    for release in releases:
        if release["draft"]:
            continue
        for asset in release["assets"]:
            if not re.fullmatch(r"MaterialsDataStudio-v\d+\.\d+\.\d+-(?:CorrespondingSources|ApplicationSource(?:Supplement)?)\.zip", asset["name"]):
                continue
            path = folder / asset["name"]
            if not path.is_file():
                gh("release", "download", release["tag_name"], "--repo", repository, "--pattern", asset["name"], "--dir", str(folder))
            if "sha256:" + digest(path) != asset["digest"]:
                raise ValueError("Historical corresponding-source checksum mismatch")


def publish(*, keep_previous: bool = False) -> None:
    repository_info = target_repository()
    repository = repository_info["full_name"]
    version, commit = identity()
    tag = "v" + version
    if api(f"repos/{repository}/commits/{tag}")["sha"] != commit:
        raise ValueError("Push the exact verified tag first")
    releases = all_releases(repository)
    current = next((r for r in releases if r["tag_name"] == tag), None)
    if current and not current["draft"]:
        raise ValueError("A published version is immutable; increase the version")
    prior = [r for r in releases if not r["draft"]]
    if any(tuple(map(int, r["tag_name"].removeprefix("v").split("."))) >= tuple(map(int, version.split("."))) for r in prior if re.fullmatch(r"v\d+\.\d+\.\d+", r["tag_name"])):
        raise ValueError("Release versions must increase")
    retain_sources(repository, prior)
    build_python = ROOT / ".build-envs" / ("v" + version.replace(".", "")) / "Scripts/python.exe"
    subprocess.run([str(build_python), str(ROOT / "scripts/verify_installer.py")], cwd=ROOT, check=True)
    assemble()
    manifest = verify_local()
    validate_signature(manifest["signature"], policy())
    notes = (ROOT / "packaging/release_notes.md").read_text(encoding="utf-8").replace("{{VERSION}}", version).replace("{{REPOSITORY}}", repository).replace("{{CHECKSUMS}}", "SHA256SUMS")
    if "## English" not in notes or "## 日本語" not in notes:
        raise ValueError("Both release-note languages are required")
    if current is None:
        current = api(f"repos/{repository}/releases", method="POST", payload={"tag_name": tag, "target_commitish": commit,
                       "name": "Materials Data Studio " + tag, "body": notes, "draft": True, "prerelease": False})
    expected = {i["name"]: {"size": i["bytes"], "digest": "sha256:" + i["sha256"]} for i in manifest["assets"]}
    for name in ("release-manifest.json", "SHA256SUMS"):
        path = ROOT / "release_assets" / name
        expected[name] = {"size": path.stat().st_size, "digest": "sha256:" + digest(path)}
    for name, identity_data in expected.items():
        current = api(f"repos/{repository}/releases/{current['id']}")
        found = next((a for a in current["assets"] if a["name"] == name), None)
        if found:
            if any(found[k] != v for k, v in identity_data.items()):
                raise ValueError("Draft asset differs; no overwrite attempted: " + name)
            continue
        print(f"Uploading {name} ({identity_data['size']:,} bytes)", flush=True)
        gh("release", "upload", tag, str(ROOT / "release_assets" / name), "--repo", repository)
    verify_remote(repository, current["id"], expected)
    api(f"repos/{repository}/releases/{current['id']}", method="PATCH", payload={"draft": False, "make_latest": "true", "body": notes})
    if not keep_previous:
        for old in prior:
            latest = api(f"repos/{repository}/releases/latest")
            if latest["id"] != current["id"]:
                raise ValueError("Latest changed; no cleanup allowed")
            verify_remote(repository, current["id"], expected)
            api(f"repos/{repository}/releases/{old['id']}", method="DELETE")
    print(f"Published in {target_repository()['visibility']} repository: https://github.com/{repository}/releases/tag/{tag}", flush=True)


def verify_remote(repository: str, release_id: int, expected: dict) -> None:
    release = api(f"repos/{repository}/releases/{release_id}")
    if {a["name"] for a in release["assets"]} != set(expected):
        raise ValueError("Remote release asset set differs")
    for asset in release["assets"]:
        if any(asset[k] != value for k, value in expected[asset["name"]].items()):
            raise ValueError("Remote release digest/size mismatch: " + asset["name"])
    print("All remote release names, sizes and SHA-256 digests verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("assemble", "verify", "publish"))
    parser.add_argument("--keep-previous", action="store_true")
    args = parser.parse_args()
    if args.action == "assemble":
        assemble()
    elif args.action == "verify":
        verify_local()
    else:
        publish(keep_previous=args.keep_previous)

"""Publication boundaries must fail before remote mutation or unsafe extraction."""
from __future__ import annotations

import json
import zipfile

import pytest
from scripts import prepare_reference_notices as notices
from scripts import publication_check as guard
from scripts import publish_release as releases
from scripts import validate_publication as validation
from scripts.collect_licenses import legal_name


def test_local_ignored_fixture_cannot_hide_incomplete_checkout(tmp_path, monkeypatch):
    (tmp_path / "fixture.h5xp").write_bytes(b"local-only")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "FIXTURE_HASHES", {"fixture.h5xp": "0" * 64})
    with pytest.raises(ValueError, match="not tracked"):
        guard.check_required_fixtures([])
    with pytest.raises(ValueError, match="provenance"):
        guard.check_required_fixtures(["fixture.h5xp"])


@pytest.mark.parametrize("status", ["HashMismatch", "NotTrusted", "UnknownError"])
def test_unsigned_policy_does_not_accept_broken_signatures(status):
    with pytest.raises(ValueError, match="signing policy"):
        releases.validate_signature(status, {"signing_policy": "unsigned-disclosed"})


def test_unsigned_installer_needs_explicit_policy():
    with pytest.raises(ValueError, match="signing policy"):
        releases.validate_signature("NotSigned", {})
    releases.validate_signature("NotSigned", {"signing_policy": "unsigned-disclosed"})
    releases.validate_signature("Valid", {})


def test_public_mp_distribution_requires_completed_contact(monkeypatch):
    monkeypatch.setattr(releases, "policy", lambda: {
        "repository": "owner/approved", "allow_public_repository": True,
        "public_readiness": {"terms_reviewed": True}, "distribute_materials_project": True,
        "deferred_items": {"materials_project_redistribution_contact_completed": False},
    })
    monkeypatch.setattr(releases, "api", lambda *_a: {"full_name": "owner/approved", "private": False})
    with pytest.raises(ValueError, match="contact"):
        releases.target_repository()


def test_functional_cache_is_invalidated_by_app_changes_not_documentation(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("value=1")
    (tmp_path / "README.md").write_text("First guide")
    monkeypatch.setattr(validation, "ROOT", tmp_path)
    monkeypatch.setattr(validation, "tracked", lambda: ["src/app.py", "README.md"])
    before = validation.functional_fingerprint()
    (tmp_path / "README.md").write_text("Updated guide")
    assert validation.functional_fingerprint() == before
    (tmp_path / "src/app.py").write_text("value=2")
    assert validation.functional_fingerprint() != before


@pytest.mark.parametrize("name", ["LicenseRef-Qt-Commercial.txt", "LICENSE", "COPYING_GCC", "dragon4_LICENSE.txt", "LICENSE-MPL-2.0"])
def test_license_names_from_real_wheel_metadata_are_preserved(name):
    assert legal_name("component/" + name)


def test_library_implementation_is_not_mistaken_for_license_text():
    assert not legal_name("library/implementation.py")
    assert not legal_name("ci/check_wheel_licenses.py")


def test_wrong_push_remote_stops_before_any_remote_call(monkeypatch):
    monkeypatch.setattr(guard, "config", lambda: {"repository": "owner/approved"})
    monkeypatch.setattr(guard.subprocess, "check_output", lambda *_a, **_kw: pytest.fail("No network is allowed"))
    with pytest.raises(ValueError, match="destination"):
        guard.check_push("origin", "https://github.com/owner/private-history.git")


def test_publisher_rejects_development_tree_before_git_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(releases, "ROOT", tmp_path)
    monkeypatch.setattr(releases.subprocess, "check_output", lambda *_a, **_kw: pytest.fail("No Git or network allowed"))
    with pytest.raises(ValueError, match="clean publication checkout"):
        releases.identity()


def test_reviewed_reference_digest_is_required_before_assembly(tmp_path, monkeypatch):
    folder = tmp_path / "release_assets"
    folder.mkdir()
    (folder / "reference.zip").write_bytes(b"changed")
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    (packaging / "reference_assets.json").write_text(json.dumps([{"name": "reference.zip", "size": 7, "digest": "sha256:" + "0" * 64}]))
    monkeypatch.setattr(releases, "ROOT", tmp_path)
    monkeypatch.setattr(releases, "identity", lambda: ("0.14.7", "synthetic"))
    monkeypatch.setattr(releases, "validate_feed", lambda *_a: pytest.fail("No assembly after digest mismatch"))
    with pytest.raises(ValueError, match="reviewed source manifest"):
        releases.assemble()


def test_private_history_root_is_rejected(monkeypatch):
    monkeypatch.setattr(guard, "config", lambda: {"baseline_id": "reviewed"})
    monkeypatch.setattr(guard, "git", lambda *args: "old-root" if args[0] == "rev-list" else "different-root")
    with pytest.raises(ValueError, match="Private development history"):
        guard.history_checks()


def test_merging_another_root_is_rejected(monkeypatch):
    monkeypatch.setattr(guard, "git", lambda *_args: "one\ntwo")
    with pytest.raises(ValueError, match="exactly one"):
        guard.history_checks()


def test_feed_cannot_reference_another_channel_version_or_path(tmp_path):
    payload = {"Assets": [{"PackageId": "MaterialsDataStudio", "Version": "0.14.7", "Type": "Full", "FileName": "../escape.nupkg"}]}
    (tmp_path / "releases.win.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="identity"):
        releases.validate_feed(tmp_path, "0.14.7")


def test_new_update_feeds_require_bilingual_notes_before_publish(tmp_path):
    (tmp_path / "releases.win.json").write_text(json.dumps({"Assets": [{}]}))
    with pytest.raises(ValueError, match="bilingual release notes"):
        releases.validate_feed(tmp_path, "0.15.0")


def test_repacking_notices_preserves_payload_and_original_archive(tmp_path):
    source = tmp_path / "original.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("database.part1", b"synthetic-data" * 100)
        archive.writestr("manifest.json", '{"synthetic":true}')
        archive.writestr("NOTICE.txt", "old local-only wording")
    original = source.read_bytes()
    target = tmp_path / "prepared.zip"
    result = notices.repackage(source, target, "CC BY 4.0 / attribution", "出典とライセンス")
    assert source.read_bytes() == original
    assert len(result["unchanged_payloads"]) == 2
    with zipfile.ZipFile(target) as archive:
        assert archive.read("database.part1") == b"synthetic-data" * 100
        assert archive.read("NOTICE.txt") == b"CC BY 4.0 / attribution"
        assert archive.read("NOTICE_JA.txt").decode() == "出典とライセンス"
        assert len(archive.namelist()) == len(set(archive.namelist()))


def test_published_release_is_never_replaced(monkeypatch):
    monkeypatch.setattr(releases, "target_repository", lambda: {"full_name": "owner/approved", "private": True})
    monkeypatch.setattr(releases, "identity", lambda: ("0.14.7", "same-commit"))
    monkeypatch.setattr(releases, "api", lambda endpoint, **_kw: {"sha": "same-commit"} if endpoint.endswith("/commits/v0.14.7") else pytest.fail("No mutation allowed"))
    monkeypatch.setattr(releases, "all_releases", lambda _repo: [{"tag_name": "v0.14.7", "draft": False}])
    with pytest.raises(ValueError, match="immutable"):
        releases.publish()

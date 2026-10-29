"""Real SDK/feed/cache/launch contract; recorder never installs or edits registry."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows SDK")


def test_native_full_download_notes_hash_and_graceful_restart_request(tmp_path):
    import velopack

    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        pytest.skip("C# compiler is not installed")
    root = tmp_path / "isolated app with spaces"
    current = root / "current"
    packages = root / "packages"
    current.mkdir(parents=True)
    packages.mkdir()
    manifest = ('<package xmlns="http://schemas.microsoft.com/packaging/2010/07/nuspec.xsd"><metadata>'
                '<id>MDSUpdateTestFixture</id><version>{version}</version><title>Synthetic fixture</title>'
                '<authors>MDS</authors><description>Synthetic only</description><channel>win</channel>'
                '<mainExe>Fixture.exe</mainExe><os>win</os><rid>win-x64</rid></metadata></package>')
    (current / "sq.version").write_text(manifest.format(version="0.15.0"))
    marker = root / "restart-arguments.txt"
    source = root / "Recorder.cs"
    source.write_text('using System.IO; class Recorder { static void Main(string[] args) { '
                      'File.WriteAllLines(@"' + str(marker).replace('"', '""') + '", args); } }')
    updater = root / "Recorder.exe"
    built = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/out:" + str(updater), str(source)],
                           capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    web = tmp_path / "feed"
    web.mkdir()
    prefix = web / "releases/download/v0.15.1"
    prefix.mkdir(parents=True)
    filename = "MDSUpdateTestFixture-0.15.1-full.nupkg"
    package = prefix / filename
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("MDSUpdateTestFixture.nuspec", manifest.format(version="0.15.1"))
        archive.writestr("lib/app/sq.version", manifest.format(version="0.15.1"))
        archive.writestr("lib/app/synthetic.txt", "synthetic update only")
    payload = package.read_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    notes = "## English\nSynthetic update\n## 日本語\n合成の更新試験"
    item = {"PackageId": "MDSUpdateTestFixture", "Version": "0.15.1", "Type": "Full", "FileName": filename,
            "SHA1": hashlib.sha1(payload).hexdigest(), "SHA256": sha256, "Size": len(payload),
            "NotesMarkdown": notes, "NotesHtml": ""}
    feed = prefix / "releases.win.json"
    feed.write_text(json.dumps({"Assets": [item]}), encoding="utf-8")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                               "--directory", str(web)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AssertionError("Local fixture server did not start") from None
                time.sleep(.05)
        locator = velopack.VelopackLocatorConfig(root, updater, packages, current / "sq.version", current, False)
        manager = velopack.UpdateManager(velopack.HttpSource(f"http://127.0.0.1:{port}/releases/download/v0.15.1", velopack.HttpOptions([], 5000)),
                                        velopack.UpdateOptions(False, -1, "win"), locator)
        assert manager.get_app_id() == "MDSUpdateTestFixture" and not manager.get_is_portable()
        assert manager.get_current_version() == "0.15.0"
        info = manager.check_for_updates()
        assert info is not None and info.TargetFullRelease.NotesMarkdown == notes
        progress = []
        manager.download_updates(info, lambda value: progress.append(value))
        assert all(0 <= value <= 100 for value in progress)
        cached = packages / filename
        assert cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == sha256
        pending = manager.get_update_pending_restart()
        assert pending is not None and pending.Version == "0.15.1"
        manager.wait_exit_then_apply_updates(pending, False, True, [])
        deadline = time.monotonic() + 5
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(.05)
        arguments = marker.read_text().splitlines()
        assert str(os.getpid()) in arguments and any("apply" in arg for arg in arguments)
        # The native SDK must reject wrong SHA-256, even with a correct SHA1.
        cached.unlink()
        item["SHA256"] = "0" * 64
        feed.write_text(json.dumps({"Assets": [item]}), encoding="utf-8")
        invalid = manager.check_for_updates()
        assert invalid is not None
        with pytest.raises(Exception, match=r"(?i)(checksum|hash|sha256|verification)"):
            manager.download_updates(invalid)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def test_native_other_instance_detection_does_not_query_or_change_user_files(monkeypatch):
    from materials_data_studio.integrations import updates
    monkeypatch.setattr(sys, "executable", "system")
    assert updates.other_instances_running()
    monkeypatch.setattr(sys, "executable", "MDS-Nonexistent-Test-Process-927A.exe")
    assert not updates.other_instances_running()

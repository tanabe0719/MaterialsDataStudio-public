"""Same-user document requests never replace an existing workspace."""
import json
import subprocess
import sys

import pytest

from materials_data_studio.integrations.document_open import (
    DocumentOpenServer,
    request_paths,
)


def test_document_request_allows_only_existing_mds_files(tmp_path):
    document = tmp_path / "synthetic.mdsg"
    document.write_bytes(b"Synthetic protocol fixture")
    assert request_paths(json.dumps({"version": 1, "paths": [str(document)]}).encode()) == [document]
    for request in ({"version": 1, "paths": ["relative.mdsg"]}, {"version": 1, "paths": [str(tmp_path / "run.exe")]},
                    {"version": 2, "paths": [str(document)]}, {"version": 1, "paths": [str(document)], "command": "anything"}):
        with pytest.raises(ValueError):
            request_paths(json.dumps(request).encode())


def test_server_acknowledges_one_document_and_respects_instance_lock(qtbot, tmp_path):
    settings = tmp_path / "runtime" / "settings.ini"
    document = tmp_path / "synthetic.mdss"
    document.write_bytes(b"Synthetic protocol fixture")
    received = []
    server = DocumentOpenServer(settings, received.append)
    other = DocumentOpenServer(settings, lambda _: pytest.fail("The other instance received a request"))
    try:
        assert server.listening and not other.listening
        # Exercise a second process, as in a real document launch. Blocking Qt
        # socket waits in a Python thread can hold the same interpreter's GIL.
        code = "from pathlib import Path; import sys; from PySide6.QtCore import QCoreApplication; from materials_data_studio.integrations.document_open import forward_documents; app=QCoreApplication([]); print(forward_documents([Path(sys.argv[1])],Path(sys.argv[2])),flush=True)"
        process = subprocess.Popen([sys.executable, "-c", code, str(document), str(settings)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            qtbot.waitUntil(lambda: process.poll() is not None, timeout=10000)
            output, error = process.communicate(timeout=1)
            assert process.returncode == 0 and output.strip() == "True", (output, error, received)
            qtbot.waitUntil(lambda: len(received) == 1, timeout=5000)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        assert received == [[document]]
    finally:
        other.close()
        server.close()

"""Per-run prepared text must not consume the MDSG metadata budget."""
import json

import pytest

from materials_data_studio.tools.igor_input_resources import (
    read_input_resources,
    write_input_resources,
)


def test_sidecar_roundtrip_preserves_unicode_and_crlf(tmp_path):
    resources = [{"name": "synthetic.dat", "path": "C:/合成/synthetic.dat", "text": "日本語\r\n0,1\n"}]
    path = tmp_path / "igor_inputs.json"
    write_input_resources(path, resources)
    assert read_input_resources(path) == resources
    assert json.loads(path.read_bytes())["version"] == 1


@pytest.mark.parametrize("payload", [[], {"version": True, "resources": []},
    {"version": 2, "resources": []}, {"version": 1, "resources": [], "extra": 1},
    {"version": 1, "resources": [{"name": "bad", "path": "bad", "text": "\0"}]}])
def test_sidecar_rejects_invalid_schema_and_resources(tmp_path, payload):
    path = tmp_path / "igor_inputs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        read_input_resources(path)


def test_sidecar_bound_checked_on_read_and_write(tmp_path, monkeypatch):
    monkeypatch.setattr("materials_data_studio.tools.igor_input_resources.MAX_SNAPSHOT_BYTES", 10)
    path = tmp_path / "igor_inputs.json"
    with pytest.raises(ValueError, match="encoded limit"):
        write_input_resources(path, [])
    path.write_bytes(b"x" * 11)
    with pytest.raises(ValueError, match="encoded limit"):
        read_input_resources(path)

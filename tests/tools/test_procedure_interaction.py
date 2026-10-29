from __future__ import annotations

import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from materials_data_studio.tools.procedure_interaction import (
    BrokerInteractionClock,
    FrameReader,
    ProcedureInteractionSession,
    WorkerInteraction,
    prepare_interaction,
    validate_request,
    validate_response,
)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    return tmp_path


def request(kind="message", payload=None, sequence=1):
    return {"version": 1, "sequence": sequence, "kind": kind,
            "payload": payload or {"title": "Test", "text": "Synthetic message", "buttons": ["OK"]}}


def reply(req, payload=None, status="ok"):
    return {"version": 1, "sequence": req["sequence"], "status": status,
            "payload": {} if status == "cancelled" else payload or {"button": 0}}


def test_preopened_worker_roundtrip_and_cancellation(root):
    session = ProcedureInteractionSession(root)
    worker = WorkerInteraction(root, timeout=2)
    try:
        with ThreadPoolExecutor() as executor:
            for index, status in enumerate(("ok", "cancelled"), 1):
                future = executor.submit(worker.request, "message", {"title": "Synthetic", "text": "Prompt", "buttons": ["OK"]})
                deadline = time.monotonic() + 1
                req = None
                while req is None and time.monotonic() < deadline:
                    req = session.poll()
                    time.sleep(.002)
                assert req is not None and req["sequence"] == index
                assert session.poll() is None
                response = reply(req, status=status)
                session.respond(req, {"status": status, "payload": response["payload"]})
                assert future.result(timeout=1) == ({"status": "ok", "button": 0} if status == "ok" else {"status": "cancelled"})
                with pytest.raises(ValueError, match="stale"):
                    session.respond(req, {"status": status, "payload": response["payload"]})
    finally:
        worker.close()
        session.close()
    assert session.poll() is None


def test_worker_timeout_and_error(root):
    session = ProcedureInteractionSession(root)
    worker = WorkerInteraction(root, timeout=.03)
    with pytest.raises(TimeoutError, match="timed out"):
        worker.request("message", request()["payload"])
    worker.close()
    session.close()
    session = ProcedureInteractionSession(root)
    worker = WorkerInteraction(root, timeout=1)
    try:
        with ThreadPoolExecutor() as executor:
            future = executor.submit(worker.request, "message", request()["payload"])
            req = None
            while req is None:
                req = session.poll()
                time.sleep(.002)
            session.respond(req, {"status": "error", "payload": {"message": "Synthetic host failure"}})
            with pytest.raises(RuntimeError, match="Synthetic host failure"):
                future.result(timeout=1)
    finally:
        worker.close()
        session.close()


@pytest.mark.parametrize("bad", [
    {"version": 1, "sequence": 0, "kind": "message", "payload": {}},
    {"version": True, "sequence": 1, "kind": "message", "payload": {}},
    {"version": 1, "sequence": 129, "kind": "message", "payload": {}},
    {"version": 2, "sequence": 1, "kind": "message", "payload": {}},
    request("shell", {}), request("message", {"title": "T", "text": "Hi", "buttons": []}),
    request("file_open", {"title": "T", "default_name": "C:\\host\\file.dat"}),
    request("directory", {"title": "T", "path": "\\\\server\\share"}),
    request("file_open", {"title": "\0"}),
    request("prompt", {"title": "T", "fields": []}),
    request("prompt", {"title": "T", "fields": [{"name": "n", "label": "N", "type": "number", "value": float("inf")}]}),
    request("prompt", {"title": "T", "fields": [{"name": "s", "label": "S", "type": "text", "value": "x", "choices": []}]}),
    request("prompt", {"title": "T", "fields": [{"name": "a", "label": "A", "type": "code", "value": "x"}]}),
])
def test_reject_unbounded_or_capability_expanding_requests(bad):
    with pytest.raises(ValueError):
        validate_request(bad)


def test_all_supported_responses_strictly_validate():
    file = request("file_open", {"title": "Select a *.prf file", "filter": "*.prf", "default_name": "synthetic.prf"})
    resources = [{"name": "synthetic.prf", "path": "C:\\explicit\\synthetic.prf", "text": "1 2\n3 4\n"}]
    assert validate_response(reply(file, {"resources": resources, "selected": resources[0]["path"]}), file)
    prompt = request("prompt", {"title": "Inputs", "help": "Synthetic inputs", "fields": [
        {"name": "weight", "label": "Weight", "type": "number", "value": 10},
        {"name": "name", "label": "Name", "type": "text", "value": "Sample"},
        {"name": "mode", "label": "Mode", "type": "choice", "value": 0, "choices": ["A", "B"]},
    ]})
    validate_request(prompt)
    good = reply(prompt, {"values": {"weight": 2.5, "name": "New", "mode": 1}})
    assert validate_response(good, prompt)
    for bad in (reply(prompt, {"values": {"weight": 1}}),
                reply(prompt, {"values": {"weight": True, "name": "New", "mode": 0}}),
                reply(prompt, {"values": {"weight": 1, "name": "New", "mode": 2}}),
                {**good, "sequence": 5}, {**good, "status": "execute"},
                {**good, "status": "cancelled", "payload": {"code": "exec"}}):
        with pytest.raises(ValueError):
            validate_response(bad, prompt)


def test_incremental_frame_reader_partial_frames_and_limits():
    stream = io.BytesIO()
    reader = FrameReader(stream, 20, 30)
    assert reader.poll() is None
    stream.write(b'{"x":')
    stream.seek(0)
    assert reader.poll() is None
    stream.seek(0, 2)
    stream.write(b'1}\n{"y":2}\n')
    stream.seek(5)
    assert reader.poll() == {"x": 1}
    assert reader.poll() == {"y": 2}
    assert reader.poll() is None
    with pytest.raises(ValueError, match="size limit"):
        FrameReader(io.BytesIO(b"x" * 22), 20, 30).poll()
    with pytest.raises(ValueError, match="size limit"):
        FrameReader(io.BytesIO(b"x" * 12), 20, 10).poll()
    with pytest.raises(ValueError, match="size limit"):
        FrameReader(io.BytesIO(b"x" * 20 + b"\n"), 20, 30).poll()
    for data in (b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":Infinity}\n'):
        with pytest.raises(ValueError):
            FrameReader(io.BytesIO(data), 128, 512).poll()
    reader = FrameReader(io.BytesIO(b'{"x":1}\n' * 5), 10, 512)
    for _ in range(5):
        assert reader.poll() == {"x": 1}


def test_session_sequence_and_total_are_fail_closed(root, monkeypatch):
    import materials_data_studio.tools.procedure_interaction as protocol
    session = ProcedureInteractionSession(root)
    path = root / "output" / protocol.REQUEST_NAME
    path.write_text(json.dumps(request(sequence=2)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="strictly increasing"):
        session.poll()
    session.close()
    path.write_text(json.dumps(request()) + "\n", encoding="utf-8")
    session = ProcedureInteractionSession(root)
    try:
        req = session.poll()
        assert req is not None
        monkeypatch.setattr(protocol, "MAX_RESPONSE_TOTAL", 1)
        with pytest.raises(ValueError, match="input total"):
            session.respond(req, {"status": "ok", "payload": {"button": 0}})
    finally:
        session.close()


def test_broker_clock_pauses_only_valid_outstanding_dialogs(root):
    import materials_data_studio.tools.procedure_interaction as protocol
    prepare_interaction(root)
    clock = BrokerInteractionClock(root)
    path = root / "output" / protocol.REQUEST_NAME
    path.write_text(json.dumps(request()) + "\n", encoding="utf-8")
    try:
        assert clock.poll(10) == 0
        assert clock.poll(20) == 10
        response_path = root / "input" / protocol.RESPONSE_NAME
        with response_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(reply(request())) + "\n")
        assert clock.poll(25) == 15
        assert clock.poll(30) == 15
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(request(sequence=2)) + "\n")
        assert clock.poll(40) == 15
        with pytest.raises(TimeoutError, match="5-minute"):
            clock.poll(341)
    finally:
        clock.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Requires no-capability Windows AppContainer")
def test_native_interactive_procedure_roundtrip_and_cancel(root):
    """Real isolated process, not a fake runtime; all data is generated here."""
    from materials_data_studio.tools.graph_document import (
        EditableGraphDocument,
        load_graph_document,
        save_graph_document,
    )
    from materials_data_studio.tools.procedure_policy import validate
    from materials_data_studio.tools.procedure_sandbox import launch
    from materials_data_studio.tools.pxp_model import PxpDocument

    document = EditableGraphDocument.from_pxp(PxpDocument(
        "generated", "synthetic.pxp", "", 0, "10", "synthetic", "", status="FULL"))
    save_graph_document(root / "input/input.mdsg", document)
    (root / "input/procedure.json").write_text(json.dumps({"igor_interactive": True}), encoding="utf-8")
    source = '''from mds import igor, wave
igor.command('Variable weight=10')
igor.command('Prompt weight,"Weight (mg)"')
igor.command('DoPrompt "Synthetic inputs",weight')
igor.command('Make/D/N=1 accepted; accepted=weight')
igor.command('Open/D/R/F="Text:*.prf;"/M="Select a *.prf file" handle')
igor.command('LoadWave/G/O S_fileName')
igor.command('DoAlert 0,"Synthetic complete"')
try:
    igor.command('Open/R forbidden as "C:/not-selected-by-user.dat"')
except PermissionError:
    wave.create("host_read_denied", [1])
'''
    validate(source)
    with pytest.raises(ValueError, match="not allowed"):
        validate('open("C:/host-write.txt","w")')
    (root / "input/procedure.py").write_text(source, encoding="utf-8")
    session = ProcedureInteractionSession(root)
    seen = []
    try:
        with ThreadPoolExecutor() as executor:
            future = executor.submit(launch, root)
            deadline = time.monotonic() + 160
            while not future.done() and time.monotonic() < deadline:
                req = session.poll()
                if req is not None:
                    seen.append(req["kind"])
                    if req["kind"] == "prompt":
                        payload = {"values": {"weight": 12.5}}
                    elif req["kind"] == "file_open":
                        assert req["payload"]["title"] == "Select a *.prf file"
                        selected = "C:/explicit-user-choice/synthetic.prf"
                        payload = {"selected": selected, "resources": [
                            {"name": "synthetic.prf", "path": selected, "text": "0 2\n1 4\n2 6\n"}]}
                    else:
                        payload = {"button": 0}
                    session.respond(req, {"status": "ok", "payload": payload})
                time.sleep(.01)
            assert future.result(timeout=5) == 0, (root / "output/procedure.log").read_text(encoding="utf-8")
    finally:
        session.close()
    assert seen == ["prompt", "file_open", "message"]
    output = load_graph_document(root / "output/output.mdsg")
    waves = {wave.name: wave for wave in output.render_source().waves}
    assert waves["accepted"].data.tolist() == [12.5]
    assert waves["host_read_denied"].data.tolist() == [1]
    assert "igor_interactive" not in json.loads((root / "output/output.json").read_text(encoding="utf-8"))
    # A fresh job tests native prompt cancellation without changing the input.
    cancelled = root / "cancelled"
    (cancelled / "input").mkdir(parents=True)
    (cancelled / "output").mkdir()
    save_graph_document(cancelled / "input/input.mdsg", document)
    (cancelled / "input/procedure.json").write_text(json.dumps({"igor_interactive": True}), encoding="utf-8")
    (cancelled / "input/procedure.py").write_text('''from mds import igor
igor.command('Variable weight=10')
igor.command('Prompt weight,"Weight"')
igor.command('DoPrompt "Cancelled prompt",weight')
igor.command('Make/D/N=1 cancellationFlag; cancellationFlag=V_flag')
igor.command('Make/D/N=1 cancellationWeight; cancellationWeight=weight')
''', encoding="utf-8")
    session = ProcedureInteractionSession(cancelled)
    try:
        with ThreadPoolExecutor() as executor:
            future = executor.submit(launch, cancelled)
            deadline = time.monotonic() + 160
            while not future.done() and time.monotonic() < deadline:
                req = session.poll()
                if req is not None:
                    session.respond(req, {"status": "cancelled", "payload": {}})
                time.sleep(.01)
            assert future.result(timeout=5) == 0, (cancelled / "output/procedure.log").read_text(encoding="utf-8")
    finally:
        session.close()
    result = load_graph_document(cancelled / "output/output.mdsg")
    waves = {wave.name: wave for wave in result.render_source().waves}
    assert waves["cancellationFlag"].data.tolist() == [1]
    assert waves["cancellationWeight"].data.tolist() == [10]

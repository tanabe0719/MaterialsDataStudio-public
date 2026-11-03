"""Optional same-user document forwarding; no commands or network endpoints."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

MAX_REQUEST = 64 * 1024


def request_paths(payload: bytes) -> list[Path]:
    if len(payload) > MAX_REQUEST:
        raise ValueError("Document request is too large")
    request = json.loads(payload)
    if not isinstance(request, dict) or request.get("version") != 1 or set(request) != {"version", "paths"}:
        raise ValueError("Invalid document request")
    names = request["paths"]
    if not isinstance(names, list) or not 1 <= len(names) <= 64 or any(not isinstance(p, str) for p in names):
        raise ValueError("Invalid document paths")
    paths = [Path(p) for p in names]
    if any(not p.is_absolute() or p.suffix.lower() not in {".mdss", ".mdsg", ".mdsession"} or not p.is_file() for p in paths):
        raise ValueError("Only existing MDS documents can be forwarded")
    return paths


def runtime_path() -> Path:
    import os

    from materials_data_studio.core.logging_config import user_data_directory
    configured = os.environ.get("MDS_SETTINGS_DIRECTORY", "").strip()
    return (Path(configured) if configured else user_data_directory()) / "document-open.runtime"


def endpoint(settings_path: Path) -> str:
    return "mds-documents-" + hashlib.sha256(str(settings_path.resolve()).encode()).hexdigest()[:24]


def forward_documents(paths: list[Path], settings_path: Path) -> bool:
    try:
        payload = json.dumps({"version": 1, "paths": [str(p.resolve()) for p in paths]}).encode()
        request_paths(payload)
    except (OSError, ValueError):
        return False
    socket = QLocalSocket()
    socket.connectToServer(endpoint(settings_path))
    if socket.state() != QLocalSocket.LocalSocketState.ConnectedState and not socket.waitForConnected(100):
        logging.getLogger(__name__).debug("Document forwarding connection unavailable: %s", socket.errorString())
        return False
    socket.write(payload + b"\n")
    if socket.bytesToWrite():
        socket.waitForBytesWritten(1000)
    if not socket.bytesAvailable():
        socket.waitForReadyRead(1000)
    if not socket.bytesAvailable():
        logging.getLogger(__name__).debug("Document forwarding acknowledgement unavailable: %s", socket.errorString())
        socket.abort()
        return False
    accepted = bytes(socket.readAll().data()).strip() == b"OK"
    socket.disconnectFromServer()
    return accepted


class DocumentOpenServer(QObject):
    def __init__(self, settings_path: Path, accept, parent=None):
        super().__init__(parent)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(settings_path.parent / "document-open.lock"))
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.accept = accept
        self.sockets = set()
        self.listening = False
        if self.lock.tryLock(0):
            QLocalServer.removeServer(endpoint(settings_path))
            self.listening = self.server.listen(endpoint(settings_path))
            self.server.newConnection.connect(self._connection)

    def _connection(self):
        socket = self.server.nextPendingConnection()
        if socket is None:
            return
        if len(self.sockets) >= 8:
            socket.abort()
            socket.deleteLater()
            return
        self.sockets.add(socket)
        request = b""
        done = False
        def read():
            nonlocal request, done
            if done:
                return
            request += bytes(socket.readAll().data())
            data = request
            if len(data) > MAX_REQUEST + 1:
                socket.abort()
                return
            if b"\n" not in data:
                return
            try:
                paths = request_paths(data.strip())
            except (ValueError, OSError, TypeError):
                socket.abort()
                return
            socket.write(b"OK\n")
            done = True
            socket.flush()
            QTimer.singleShot(0, lambda: self.accept(paths))
        def closed():
            self.sockets.discard(socket)
            socket.deleteLater()
        socket.readyRead.connect(read)
        socket.disconnected.connect(closed)
        timeout = QTimer(socket)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: socket.abort())
        timeout.start(2000)
        if socket.bytesAvailable():
            read()

    def close(self):
        for socket in tuple(self.sockets):
            socket.abort()
        self.server.close()
        self.lock.unlock()

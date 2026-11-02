"""Shared, bounded in-memory backing for validated lazy NumPy members."""
from __future__ import annotations

import shutil
import tempfile
import threading
import zipfile

import numpy as np


class WaveStore:
    def __init__(self, payload: bytes):
        self.lock = threading.RLock()
        self.stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024**2)  # noqa: SIM115 -- owned until the shared store dies
        self.stream.write(payload)
        self.stream.seek(0)
        self.archive = zipfile.ZipFile(self.stream)
        self.retained_size = min(len(payload), 8 * 1024**2) + sum(512 + len(info.filename) for info in self.archive.infolist())

    def load(self, key):
        with self.lock, self.archive.open(key + ".npy") as stream:
            array = np.lib.format.read_array(stream, allow_pickle=False)
            array.setflags(write=False)
            return array

    def copy_member(self, key, destination, name):
        with self.lock, self.archive.open(key + ".npy") as source, destination.open(name, "w", force_zip64=True) as output:
            shutil.copyfileobj(source, output, 1024 * 1024)

    def __del__(self):
        if hasattr(self, "archive"):
            self.archive.close()
        if hasattr(self, "stream"):
            self.stream.close()

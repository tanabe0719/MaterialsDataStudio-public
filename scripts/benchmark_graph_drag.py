"""Compare the former full-scene drag path with object-only blitting; synthetic only."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from types import SimpleNamespace

import numpy as np
from PySide6.QtWidgets import QApplication

from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpDocument, PxpWave
from materials_data_studio.tools.pxp_recreation import parse_recreation


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
    app = QApplication.instance() or QApplication([])
    x = np.linspace(0, 10, 100_000)
    waves = []
    for name, values in [("x", x), *[(f"y{i}", np.sin(x + i) + i) for i in range(6)]]:
        values.setflags(write=False)
        waves.append(PxpWave(name, "root:" + name, name, values.dtype.str, values.shape, values, ((0., 1.),), ("",), "", ""))
    commands = "Window Demo() : Graph\nDisplay y0 vs x\n" + "\n".join(f"AppendToGraph y{i} vs x" for i in range(1, 6)) + '\nLabel bottom "Position"\nLabel left "Signal"\nEndMacro'
    graphs, _ = parse_recreation(commands)
    raw = b"Generated drag benchmark"
    source = PxpDocument("benchmark", "Synthetic.pxp", hashlib.sha256(raw).hexdigest(), len(raw), "10", "synthetic", "1", tuple(waves), graphs=graphs, status="FULL", source_bytes=raw)
    viewer = GraphEditorWindow()
    viewer.bind_document(source)
    viewer._discard_approved = True
    viewer.show()
    app.processEvents()
    assert viewer.editor is not None
    frames = 30
    started = time.perf_counter()
    for _ in range(frames):
        viewer.artist_targets = viewer.renderer.render(viewer.figure, viewer.editor.document, "Demo")
        viewer.canvas.draw()
    full_ms = (time.perf_counter() - started) * 1000 / frames
    artist = next(a for a, target in viewer.artist_targets.items() if target == ("label", "bottom"))
    box = artist.get_window_extent(viewer.canvas.get_renderer())
    x0, y0 = box.x0 + box.width / 2, box.y0 + box.height / 2
    viewer.pointer_press(SimpleNamespace(x=x0, y=y0, button=1, dblclick=False, inaxes=None))
    assert viewer._drag is not None
    started = time.perf_counter()
    for index in range(frames):
        viewer.pointer_move(SimpleNamespace(x=x0 + index, y=y0 + index / 2))
        viewer._paint_drag()
    blit_ms = (time.perf_counter() - started) * 1000 / frames
    viewer.pointer_release(SimpleNamespace())
    print(json.dumps({"points": 600_000, "frames": frames, "full_redraw_ms_per_frame": round(full_ms, 3), "object_blit_ms_per_frame": round(blit_ms, 3), "speedup": round(full_ms / blit_ms, 2)}, indent=2))
    viewer.close()


if __name__ == "__main__":
    main()

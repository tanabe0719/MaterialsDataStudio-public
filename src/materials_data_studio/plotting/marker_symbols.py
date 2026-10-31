"""Consistent marker geometry across screen and figure rendering."""
from __future__ import annotations

DAGGER = ((-.04, -.5), (.04, -.5), (.04, .11), (.3, .11), (.3, .19),
          (.04, .19), (.04, .5), (-.04, .5), (-.04, .19), (-.3, .19), (-.3, .11), (-.04, .11), (-.04, -.5))


def qt_symbol(marker: str):
    if marker != "dagger":
        return {"none": None, "^": "t1", "v": "t"}.get(marker, marker)
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    path.moveTo(DAGGER[0][0], -DAGGER[0][1])
    for x, y in DAGGER[1:]:
        path.lineTo(x, -y)
    path.closeSubpath()
    return path


def mpl_symbol(marker: str):
    if marker == "dagger":
        from matplotlib.path import Path
        return Path(DAGGER)
    return None if marker.lower() == "none" else marker

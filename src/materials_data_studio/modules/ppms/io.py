"""PPMS text decoding and file-kind detection."""

from __future__ import annotations

from pathlib import Path


def read_ppms_text(path: Path) -> str:
    """Decode common Quantum Design exports without losing malformed micro signs."""
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def detect_ppms_kind(text: str) -> str:
    """Classify specialized PPMS data, falling back to the generic workspace."""
    normalized = text.lower().replace("µ", "u").replace("μ", "u")
    data_tail = normalized.split("[data]", 1)[-1]
    first_line = next((line for line in data_tail.splitlines() if line.strip()), "")
    if "samp hc" in first_line:
        return "heat_capacity"
    if "bridge" in first_line and ("resistance" in first_line or "resistivity" in first_line):
        return "resistivity"
    if "[header]" in normalized and "[data]" in normalized and first_line:
        return "generic"
    raise ValueError("The file does not contain recognizable PPMS [Header]/[Data] sections.")

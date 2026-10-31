"""Shared state models for datasets and plot appearance."""

from __future__ import annotations

import copy
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


class ModuleType(StrEnum):
    """Top-level analysis module identifiers."""

    XRD = "xrd"
    MPMS = "mpms"
    PPMS = "ppms"


class AxisType(StrEnum):
    """XRD source and display axis types."""

    TWO_THETA = "two_theta"
    D_SPACING = "d_spacing"
    INV_D = "inverse_d"
    TOF = "tof"


class NormalizationMode(StrEnum):
    """Supported reversible intensity normalization modes."""

    NONE = "none"
    MAX_1 = "max_1"
    MAX_100 = "max_100"
    AREA = "area"


class StackingMode(StrEnum):
    """Global vertical stacking behavior."""

    NONE = "none"
    CONSTANT = "constant"
    AUTOMATIC = "automatic"
    INDIVIDUAL = "individual"


@dataclass(slots=True)
class PlotStyle:
    """Backend-independent dataset appearance."""

    color: str = "#2563EB"
    line_style: str = "-"
    line_width: float = 1.6
    marker: str = "none"
    marker_size: float = 6.0
    marker_edge_color: str | None = None
    marker_fill_color: str | None = None
    legend_text: str = ""
    marker_edge_width: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return {
            "color": self.color,
            "line_style": self.line_style,
            "line_width": self.line_width,
            "marker": self.marker,
            "marker_size": self.marker_size,
            "marker_edge_color": self.marker_edge_color,
            "marker_fill_color": self.marker_fill_color,
            "legend_text": self.legend_text,
            "marker_edge_width": self.marker_edge_width,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PlotStyle:
        """Restore a style from serialized data."""
        restored = {key: value[key] for key in cls.__dataclass_fields__ if key in value}
        legacy_pen_styles = {
            0: "none",
            1: "-",
            2: "--",
            3: ":",
            4: "-.",
        }
        line_style = restored.get("line_style")
        if isinstance(line_style, int):
            restored["line_style"] = legacy_pen_styles.get(line_style, "-")
        elif not isinstance(line_style, str):
            restored["line_style"] = "-"
        return cls(**restored)


def source_sha256(path: Path) -> str:
    """Calculate a source-file hash without assuming an ASCII path."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class Dataset:
    """Non-destructive scientific dataset and its serializable state."""

    display_name: str
    module_type: ModuleType
    raw_data: dict[str, NDArray[np.generic]]
    source_filename: str = ""
    source_hash: str = ""
    dataset_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    processed_data: dict[str, NDArray[np.generic]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    plot_style: PlotStyle = field(default_factory=PlotStyle)
    visible: bool = True
    plot_order: int = 0
    analysis_results: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_bytes: bytes | None = field(default=None, repr=False)
    plot_warning: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        """Own immutable copies of raw arrays."""
        frozen: dict[str, NDArray[np.generic]] = {}
        for name, values in self.raw_data.items():
            array = np.array(values, copy=True)
            array.setflags(write=False)
            frozen[name] = array
        self.raw_data = frozen

    def array(self, name: str, *, processed: bool = True) -> NDArray[np.generic]:
        """Return a processed array when present, otherwise its raw counterpart."""
        if processed and name in self.processed_data:
            return self.processed_data[name]
        return self.raw_data[name]

    def reset_processing(self) -> None:
        """Clear derived arrays without modifying raw data."""
        self.processed_data.clear()
        self.analysis_results.clear()

    def duplicate(self) -> Dataset:
        """Create an independent dataset while preserving scientific state."""
        return Dataset(
            display_name=f"{self.display_name} copy",
            module_type=self.module_type,
            raw_data=self.raw_data,
            source_filename=self.source_filename,
            source_hash=self.source_hash,
            processed_data={
                key: np.array(value, copy=True) for key, value in self.processed_data.items()
            },
            metadata=copy.deepcopy(self.metadata),
            plot_style=PlotStyle.from_dict(self.plot_style.to_dict()),
            visible=self.visible,
            plot_order=self.plot_order,
            analysis_results=copy.deepcopy(self.analysis_results),
            warnings=list(self.warnings),
            source_bytes=self.source_bytes,
        )

    def to_manifest(self) -> dict[str, Any]:
        """Return dataset metadata stored in session.json."""
        return {
            "dataset_id": self.dataset_id,
            "display_name": self.display_name,
            "source_filename": self.source_filename,
            "source_hash": self.source_hash,
            "module_type": self.module_type.value,
            "metadata": self.metadata,
            "plot_style": self.plot_style.to_dict(),
            "visible": self.visible,
            "plot_order": self.plot_order,
            "analysis_results": self.analysis_results,
            "warnings": self.warnings,
            "created_at": self.created_at,
            "has_source": self.source_bytes is not None,
        }

    @classmethod
    def from_manifest(
        cls,
        manifest: dict[str, Any],
        raw_data: dict[str, NDArray[np.generic]],
        processed_data: dict[str, NDArray[np.generic]],
        source_bytes: bytes | None = None,
    ) -> Dataset:
        """Restore a dataset from manifest and array payloads."""
        return cls(
            dataset_id=str(manifest["dataset_id"]),
            display_name=str(manifest["display_name"]),
            module_type=ModuleType(str(manifest["module_type"])),
            raw_data=raw_data,
            processed_data=processed_data,
            source_filename=str(manifest.get("source_filename", "")),
            source_hash=str(manifest.get("source_hash", "")),
            metadata=dict(manifest.get("metadata", {})),
            plot_style=PlotStyle.from_dict(dict(manifest.get("plot_style", {}))),
            visible=bool(manifest.get("visible", True)),
            plot_order=int(manifest.get("plot_order", 0)),
            analysis_results=dict(manifest.get("analysis_results", {})),
            warnings=list(manifest.get("warnings", [])),
            created_at=str(manifest.get("created_at", datetime.now(UTC).isoformat())),
            source_bytes=source_bytes,
        )

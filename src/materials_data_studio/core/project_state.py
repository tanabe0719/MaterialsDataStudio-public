"""In-memory state for one Materials Data Studio session."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from materials_data_studio.config import SESSION_SCHEMA_VERSION
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.tools.pxp_model import PxpDocument


@dataclass(slots=True)
class ProjectState:
    """Serializable application state independent of Qt widgets."""

    datasets: list[Dataset] = field(default_factory=list)
    active_module: ModuleType = ModuleType.XRD
    module_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    axis_settings: dict[str, Any] = field(default_factory=dict)
    window_state: dict[str, Any] = field(default_factory=dict)
    pxp_documents: dict[str, PxpDocument] = field(default_factory=dict)
    pxp_view_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    schema_version: int = SESSION_SCHEMA_VERSION
    path: Path | None = None
    modified: bool = False
    recovery_source: Path | None = field(default=None, repr=False)
    change_token: int = field(default=0, init=False, repr=False)
    generation: int = field(default=0, init=False, repr=False)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "modified" and value:
            object.__setattr__(self, "change_token", getattr(self, "change_token", 0) + 1)
        object.__setattr__(self, name, value)

    def invalidate_jobs(self) -> None:
        """Invalidate asynchronous results belonging to a replaced workspace."""
        self.generation += 1

    def add_dataset(self, dataset: Dataset) -> None:
        """Append a dataset and assign its visual order."""
        dataset.plot_order = len(self.datasets)
        self.datasets.append(dataset)
        self.modified = True

    def remove_dataset(self, dataset_id: str) -> Dataset:
        """Remove and return a dataset by UUID."""
        for index, dataset in enumerate(self.datasets):
            if dataset.dataset_id == dataset_id:
                removed = self.datasets.pop(index)
                self.reorder([item.dataset_id for item in self.datasets])
                self.modified = True
                return removed
        raise KeyError(dataset_id)

    def reorder(self, ordered_ids: list[str]) -> None:
        """Reorder datasets while rejecting missing or duplicate UUIDs."""
        by_id = {dataset.dataset_id: dataset for dataset in self.datasets}
        if len(ordered_ids) != len(by_id) or set(ordered_ids) != set(by_id):
            raise ValueError("The dataset order must contain every dataset exactly once.")
        self.datasets = [by_id[dataset_id] for dataset_id in ordered_ids]
        for index, dataset in enumerate(self.datasets):
            dataset.plot_order = index
        self.modified = True

    def visible_datasets(self, module_type: ModuleType | None = None) -> list[Dataset]:
        """Return visible datasets in plot order."""
        return sorted(
            (
                dataset
                for dataset in self.datasets
                if dataset.visible and not dataset.metadata.get("measurement_container")
                and (module_type is None or dataset.module_type == module_type)
            ),
            key=lambda dataset: dataset.plot_order,
        )

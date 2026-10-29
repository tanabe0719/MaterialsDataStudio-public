"""Central analysis-module registration."""

from __future__ import annotations

from collections.abc import Iterable

from materials_data_studio.modules.base import AnalysisModule


class ModuleRegistry:
    """Ordered module registry that keeps MainWindow module-agnostic."""

    def __init__(self) -> None:
        self._modules: dict[str, AnalysisModule] = {}

    def register(self, module: AnalysisModule) -> None:
        """Register one module and reject duplicate identifiers."""
        if module.module_id in self._modules:
            raise ValueError(f"Module '{module.module_id}' is already registered.")
        self._modules[module.module_id] = module

    def get(self, module_id: str) -> AnalysisModule:
        """Return a registered module by identifier."""
        return self._modules[module_id]

    def modules(self) -> tuple[AnalysisModule, ...]:
        """Return modules in registration order."""
        return tuple(self._modules.values())

    def register_all(self, modules: Iterable[AnalysisModule]) -> None:
        """Register a sequence of modules."""
        for module in modules:
            self.register(module)


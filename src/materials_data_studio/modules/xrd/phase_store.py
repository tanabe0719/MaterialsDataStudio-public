"""Content-addressed local CIF snapshots and disposable phase-search caches."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from materials_data_studio.modules.xrd.cif_backend import (
    CifDocument,
    extract_cif_symmetry,
    extract_cif_tables,
    validate_cif_text,
)
from materials_data_studio.modules.xrd.phase_models import MAX_BYTES, Candidate, SearchQuery


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def cif_metadata(text: str, identity: str, source: str, filename: str = "") -> Candidate:
    """Reuse Gemmi validation/site extraction without changing supplied symmetry."""
    import gemmi
    from pymatgen.core import Composition, Structure
    validate_cif_text(text)
    document = CifDocument(Path(filename or "snapshot.cif"), text, text)
    cell, sites, _warnings = extract_cif_tables(document)
    if not sites:
        raise ValueError("CIF contains no atom sites.")
    symmetry = extract_cif_symmetry(document)
    block = gemmi.cif.read_string(text).sole_block()
    def value(*tags: str) -> str:
        for tag in tags:
            v = block.find_value(tag)
            if v and v not in {"?", "."}:
                return str(gemmi.cif.as_string(v))
        return ""
    formula = value("_chemical_formula_sum", "_chemical_formula_structural")
    if not formula:
        if len(sites) > 2000:
            raise ValueError("Too many atom sites for phase-search metadata.")
        # Asymmetric-unit site counts need not have the chemical stoichiometry.
        # Use the same symmetry-expanded structure parser as simulate_cif.
        formula = str(Structure.from_str(text, fmt="cif").composition.reduced_composition.formula)
    element_set = sorted(str(e) for e in Composition(formula).elements)
    if not element_set:
        raise ValueError("CIF composition is empty.")
    return Candidate(identity, source, formula, element_set, symmetry.name, symmetry.number, symmetry.crystal_system,
                     [cell.a, cell.b, cell.c, cell.alpha, cell.beta, cell.gamma],
                     float(gemmi.UnitCell(cell.a, cell.b, cell.c, cell.alpha, cell.beta, cell.gamma).volume),
                     value("_publ_section_title", "_chemical_name_common"), value("_journal_paper_doi"),
                     filename=filename, content_hash=digest(text.encode()), retrieved_at=timestamp(), site_count=len(sites))


class PhaseStore:
    """A registered library is durable; cache files can be regenerated independently."""
    def __init__(self, root: Path):
        self.root = root
        for folder in ("cif", "patterns", "metadata"):
            (root / folder).mkdir(parents=True, exist_ok=True)

    def read(self, relative: str, default: Any = None) -> Any:
        path = self.root / relative
        try:
            if path.stat().st_size > 16 * MAX_BYTES:
                return default
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write(self, relative: str, value: Any) -> None:
        path = self.root / relative
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            f.write(payload)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def put_cif(self, text: str) -> str:
        data = text.encode()
        if len(data) > MAX_BYTES:
            raise ValueError("CIF exceeds the 4 MiB size limit.")
        key = digest(data)
        self.write(f"cif/{key}.json", text)
        return key

    def cif(self, key: str) -> str:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid CIF content identity.")
        text = self.read(f"cif/{key}.json", "")
        if not isinstance(text, str) or digest(text.encode()) != key:
            raise ValueError("CIF snapshot is missing or damaged; register/download it again.")
        return text

    def library(self) -> list[Candidate]:
        rows = self.read("library.json", [])
        candidates = []
        for row in rows if isinstance(rows, list) else []:
            try:
                candidates.append(Candidate(**row))
            except (TypeError, ValueError):
                logging.getLogger(__name__).warning("Ignored a damaged local CIF registration.")
        return candidates

    def register(self, paths: list[Path], stop=None) -> tuple[list[Candidate], list[str]]:
        library = {c.content_hash: c for c in self.library()}
        errors = []
        if len(paths) > 1000:
            raise ValueError("Register at most 1,000 CIFs at a time.")
        for path in paths:
            if stop is not None and stop.is_set():
                raise InterruptedError("Registration stopped")
            try:
                if path.stat().st_size > MAX_BYTES:
                    raise ValueError("CIF exceeds 4 MiB")
                text = path.read_text(encoding="utf-8-sig")
                key = digest(text.encode())
                if key not in library:
                    c = cif_metadata(text, f"local:{key}", "Local", str(path))
                    self.put_cif(text)
                    library[key] = c
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
        self.write("library.json", [c.to_dict() for c in library.values()])
        return list(library.values()), errors

    def remove(self, identities: set[str]) -> None:
        self.write("library.json", [c.to_dict() for c in self.library() if c.identity not in identities])

    def local_search(self, query: SearchQuery) -> list[Candidate]:
        query.validate()
        return [c for c in self.library() if query.accepts(c)]

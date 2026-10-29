"""Lazy Gemmi-backed CIF document loading, validation, and save-as."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from materials_data_studio.core.exceptions import CifValidationError, DataImportError

_CIF_NUMBER_WITH_ESD = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:\(\d+\))?([Ee][+-]?\d+)?$"
)


def parse_cif_number(value: str) -> float:
    """Parse a CIF number while accepting a parenthesized standard uncertainty.

    CIF values such as ``2.38(4)`` represent 2.38 with an estimated standard
    uncertainty of 0.04.  Numerical simulation needs the central value, while
    the unmodified source text remains available in :class:`CifDocument`.
    """
    cleaned = value.strip().strip("'\"")
    match = _CIF_NUMBER_WITH_ESD.fullmatch(cleaned)
    if match is None:
        raise ValueError(f"'{value}' is not a valid CIF number.")
    exponent = match.group(2) or ""
    return float(match.group(1) + exponent)


@dataclass(slots=True)
class CifDocument:
    """Original and independently editable CIF text."""

    source_path: Path
    original_text: str
    edited_text: str
    source_bytes: bytes | None = None

    def revert(self) -> None:
        """Discard edits without modifying the source file."""
        self.edited_text = self.original_text


@dataclass(slots=True)
class UnitCell:
    """Editable unit-cell parameters."""

    a: float
    b: float
    c: float
    alpha: float
    beta: float
    gamma: float


@dataclass(slots=True)
class SpaceGroupInfo:
    """Explicit CIF symmetry identity and its crystal-system constraints."""

    name: str
    number: int
    crystal_system: str


@dataclass(slots=True)
class AtomSite:
    """Editable atom-site fields without implicit symmetry operations."""

    label: str
    species: str
    fract_x: float
    fract_y: float
    fract_z: float
    occupancy: float = 1.0
    displacement: float | None = None


def _gemmi():  # type: ignore[no-untyped-def]
    """Import Gemmi only when CIF functionality is requested."""
    try:
        import gemmi
    except ImportError as exc:
        raise DataImportError(
            "CIF support requires Gemmi. Synchronize the project environment first."
        ) from exc
    return gemmi


def load_cif(path: Path) -> CifDocument:
    """Load and validate a CIF while retaining its exact original text."""
    try:
        source_bytes = path.read_bytes()
        text = source_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        document = _gemmi().cif.read_string(text)
        if len(document) == 0:
            raise ValueError("The CIF has no data block.")
    except (OSError, RuntimeError, ValueError) as exc:
        raise DataImportError(f"Could not parse CIF '{path.name}': {exc}") from exc
    return CifDocument(path, text, text, source_bytes)


def resolve_space_group(name: str = "", number: int = 0) -> SpaceGroupInfo:
    """Resolve an H-M symbol or International Tables number with Gemmi."""
    gemmi = _gemmi()
    cleaned_name = name.strip().strip("'\"")
    group = gemmi.find_spacegroup_by_name(cleaned_name) if cleaned_name else None
    if group is None and number:
        group = gemmi.find_spacegroup_by_number(number)
    if group is None:
        raise CifValidationError(
            "The space-group symbol or International Tables number is not recognized."
        )
    return SpaceGroupInfo(
        str(group.xhm()),
        int(group.number),
        str(group.crystal_system_str()).lower(),
    )


def extract_cif_symmetry(document: CifDocument) -> SpaceGroupInfo:
    """Read the supplied space-group symbol/number without changing its setting."""
    try:
        parsed = _gemmi().cif.read_string(document.edited_text)
        block = parsed.sole_block()
        name = next(
            (
                block.find_value(tag)
                for tag in (
                    "_space_group_name_H-M_alt",
                    "_symmetry_space_group_name_H-M",
                )
                if block.find_value(tag)
            ),
            "",
        )
        number_text = next(
            (
                block.find_value(tag)
                for tag in (
                    "_space_group_IT_number",
                    "_symmetry_Int_Tables_number",
                )
                if block.find_value(tag)
            ),
            "",
        )
        number = int(parse_cif_number(number_text)) if number_text not in {"", "?", "."} else 0
        return resolve_space_group(name or ("" if number else "P 1"), number)
    except (RuntimeError, ValueError, TypeError) as exc:
        if isinstance(exc, CifValidationError):
            raise
        raise CifValidationError(f"Could not read CIF space-group symmetry: {exc}") from exc


def constrain_unit_cell(
    cell: UnitCell,
    symmetry: SpaceGroupInfo,
    *,
    changed_parameter: str = "a",
) -> UnitCell:
    """Apply conventional crystal-system metric constraints to a unit cell."""
    values = {
        "a": cell.a,
        "b": cell.b,
        "c": cell.c,
        "alpha": cell.alpha,
        "beta": cell.beta,
        "gamma": cell.gamma,
    }
    system = symmetry.crystal_system
    if system == "cubic":
        length = values[changed_parameter] if changed_parameter in {"a", "b", "c"} else cell.a
        values.update(a=length, b=length, c=length, alpha=90.0, beta=90.0, gamma=90.0)
    elif system == "tetragonal":
        length = values[changed_parameter] if changed_parameter in {"a", "b"} else cell.a
        values.update(a=length, b=length, alpha=90.0, beta=90.0, gamma=90.0)
    elif system == "orthorhombic":
        values.update(alpha=90.0, beta=90.0, gamma=90.0)
    elif system == "hexagonal" or (system == "trigonal" and ":H" in symmetry.name.upper()):
        length = values[changed_parameter] if changed_parameter in {"a", "b"} else cell.a
        values.update(a=length, b=length, alpha=90.0, beta=90.0, gamma=120.0)
    elif system == "trigonal":
        length = values[changed_parameter] if changed_parameter in {"a", "b", "c"} else cell.a
        angle = (
            values[changed_parameter]
            if changed_parameter in {"alpha", "beta", "gamma"}
            else cell.alpha
        )
        values.update(
            a=length,
            b=length,
            c=length,
            alpha=angle,
            beta=angle,
            gamma=angle,
        )
    elif system == "monoclinic":
        values.update(alpha=90.0, gamma=90.0)
    return UnitCell(**values)


def validate_cif_text(text: str) -> None:
    """Validate edited CIF syntax and basic cell geometry."""
    try:
        document = _gemmi().cif.read_string(text)
        block = document.sole_block()
        lengths = [
            parse_cif_number(block.find_value(tag))
            for tag in ("_cell_length_a", "_cell_length_b", "_cell_length_c")
        ]
        angles = [
            parse_cif_number(block.find_value(tag))
            for tag in ("_cell_angle_alpha", "_cell_angle_beta", "_cell_angle_gamma")
        ]
    except (RuntimeError, ValueError, TypeError) as exc:
        raise CifValidationError(f"The edited CIF is invalid: {exc}") from exc
    if any(length <= 0 for length in lengths):
        raise CifValidationError("Unit-cell lengths must be positive.")
    if any(not 0 < angle < 180 for angle in angles):
        raise CifValidationError("Unit-cell angles must be between 0 and 180 degrees.")


def extract_cif_tables(document: CifDocument) -> tuple[UnitCell, list[AtomSite], list[str]]:
    """Extract editable cell/site tables while reporting partial occupancy."""
    try:
        parsed = _gemmi().cif.read_string(document.edited_text)
        block = parsed.sole_block()
        cell = UnitCell(
            *[
                parse_cif_number(block.find_value(tag))
                for tag in (
                    "_cell_length_a",
                    "_cell_length_b",
                    "_cell_length_c",
                    "_cell_angle_alpha",
                    "_cell_angle_beta",
                    "_cell_angle_gamma",
                )
            ]
        )
        label_column = block.find_loop("_atom_site_label")
        if not label_column:
            raise ValueError("The CIF has no atom-site loop.")
        loop = label_column.get_loop()
        tags = list(loop.tags)
        required = {
            "label": "_atom_site_label",
            "fract_x": "_atom_site_fract_x",
            "fract_y": "_atom_site_fract_y",
            "fract_z": "_atom_site_fract_z",
        }
        indices = {name: tags.index(tag) for name, tag in required.items()}
        species_index = tags.index("_atom_site_type_symbol") if "_atom_site_type_symbol" in tags else None
        occupancy_index = (
            tags.index("_atom_site_occupancy") if "_atom_site_occupancy" in tags else None
        )
        displacement_tag = next(
            (
                tag
                for tag in ("_atom_site_U_iso_or_equiv", "_atom_site_B_iso_or_equiv")
                if tag in tags
            ),
            None,
        )
        displacement_index = tags.index(displacement_tag) if displacement_tag else None
        rows = [
            loop.values[start : start + loop.width()]
            for start in range(0, len(loop.values), loop.width())
        ]
        sites = []
        warnings = []
        for row in rows:
            label = str(_gemmi().cif.as_string(row[indices["label"]]))
            if species_index is None:
                # CIF permits atom labels to supply the element when type_symbol
                # is absent (common in older COD entries). Do not alter source.
                match = re.match(r"([A-Z][a-z]?)", label)
                species = match.group(1) if match else ""
                if not species or _gemmi().Element(species).atomic_number < 1:
                    raise ValueError(f"Cannot infer the element from atom label '{label}'.")
            else:
                species = str(_gemmi().cif.as_string(row[species_index]))
            occupancy = (
                parse_cif_number(row[occupancy_index])
                if occupancy_index is not None and row[occupancy_index] not in {"?", "."}
                else 1.0
            )
            displacement = (
                parse_cif_number(row[displacement_index])
                if displacement_index is not None and row[displacement_index] not in {"?", "."}
                else None
            )
            site = AtomSite(
                label=label,
                species=species,
                fract_x=parse_cif_number(row[indices["fract_x"]]),
                fract_y=parse_cif_number(row[indices["fract_y"]]),
                fract_z=parse_cif_number(row[indices["fract_z"]]),
                occupancy=occupancy,
                displacement=displacement,
            )
            sites.append(site)
            if occupancy < 1.0:
                warnings.append(
                    f"Site {site.label} is partially occupied ({occupancy:g}); "
                    "disorder is preserved without symmetry expansion."
                )
        return cell, sites, warnings
    except (RuntimeError, ValueError, TypeError) as exc:
        raise CifValidationError(f"Could not extract editable CIF tables: {exc}") from exc


def _validate_tables(cell: UnitCell, sites: list[AtomSite]) -> None:
    values = dataclass_values = (
        cell.a,
        cell.b,
        cell.c,
        cell.alpha,
        cell.beta,
        cell.gamma,
    )
    if any(not math.isfinite(value) for value in values):
        raise CifValidationError("Unit-cell values must be finite.")
    if any(length <= 0 for length in dataclass_values[:3]):
        raise CifValidationError("Unit-cell lengths must be positive.")
    if any(not 0 < angle < 180 for angle in dataclass_values[3:]):
        raise CifValidationError("Unit-cell angles must be between 0 and 180 degrees.")
    for site in sites:
        if not site.label.strip() or not site.species.strip():
            raise CifValidationError("Every atom site requires a label and species.")
        numeric = (site.fract_x, site.fract_y, site.fract_z, site.occupancy)
        if any(not math.isfinite(value) for value in numeric):
            raise CifValidationError(f"Site {site.label} contains a non-finite value.")
        if not 0 <= site.occupancy <= 1:
            raise CifValidationError(f"Site {site.label} occupancy must be between 0 and 1.")
        if site.displacement is not None and not math.isfinite(site.displacement):
            raise CifValidationError(f"Site {site.label} displacement parameter must be finite.")


def update_cif_tables(
    document: CifDocument,
    cell: UnitCell,
    sites: list[AtomSite],
    symmetry: SpaceGroupInfo | None = None,
) -> list[str]:
    """Apply table edits while preserving unrelated tags and the supplied space group."""
    if symmetry is not None:
        symmetry = resolve_space_group(symmetry.name, symmetry.number)
        cell = constrain_unit_cell(cell, symmetry)
    _validate_tables(cell, sites)
    try:
        parsed = _gemmi().cif.read_string(document.edited_text)
        block = parsed.sole_block()
        if symmetry is not None:
            name_tags = (
                "_space_group_name_H-M_alt",
                "_symmetry_space_group_name_H-M",
            )
            number_tags = (
                "_space_group_IT_number",
                "_symmetry_Int_Tables_number",
            )
            existing_name_tags = [tag for tag in name_tags if block.find_value(tag)]
            existing_number_tags = [tag for tag in number_tags if block.find_value(tag)]
            for tag in existing_name_tags or [name_tags[0]]:
                block.set_pair(tag, _gemmi().cif.quote(symmetry.name))
            for tag in existing_number_tags or [number_tags[0]]:
                block.set_pair(tag, str(symmetry.number))
        for tag, value in zip(
            (
                "_cell_length_a",
                "_cell_length_b",
                "_cell_length_c",
                "_cell_angle_alpha",
                "_cell_angle_beta",
                "_cell_angle_gamma",
            ),
            (cell.a, cell.b, cell.c, cell.alpha, cell.beta, cell.gamma),
            strict=True,
        ):
            block.set_pair(tag, f"{value:.12g}")
        loop = block.find_loop("_atom_site_label").get_loop()
        tags = list(loop.tags)
        if "_atom_site_type_symbol" not in tags:
            loop.add_columns(["_atom_site_type_symbol"], "?")
            tags = list(loop.tags)
        required_tags = (
            "_atom_site_label",
            "_atom_site_type_symbol",
            "_atom_site_fract_x",
            "_atom_site_fract_y",
            "_atom_site_fract_z",
        )
        for tag in required_tags:
            if tag not in tags:
                raise ValueError(f"Required atom-site tag {tag} is missing.")
        if "_atom_site_occupancy" not in tags:
            loop.add_columns(["_atom_site_occupancy"], "1")
            tags = list(loop.tags)
        displacement_tag = next(
            (
                tag
                for tag in ("_atom_site_U_iso_or_equiv", "_atom_site_B_iso_or_equiv")
                if tag in tags
            ),
            None,
        )
        if displacement_tag is None and any(site.displacement is not None for site in sites):
            displacement_tag = "_atom_site_U_iso_or_equiv"
            loop.add_columns([displacement_tag], "?")
            tags = list(loop.tags)

        old_rows = [
            list(loop.values[start : start + loop.width()])
            for start in range(0, len(loop.values), loop.width())
        ]
        new_rows: list[list[str]] = []
        for row_index, site in enumerate(sites):
            row = old_rows[row_index] if row_index < len(old_rows) else ["?"] * len(tags)
            replacements: dict[str, Any] = {
                "_atom_site_label": site.label.strip(),
                "_atom_site_type_symbol": site.species.strip(),
                "_atom_site_fract_x": site.fract_x,
                "_atom_site_fract_y": site.fract_y,
                "_atom_site_fract_z": site.fract_z,
                "_atom_site_occupancy": site.occupancy,
            }
            if displacement_tag is not None:
                replacements[displacement_tag] = (
                    "?" if site.displacement is None else site.displacement
                )
            for tag, value in replacements.items():
                row[tags.index(tag)] = str(value)
            new_rows.append(row)
        columns = [[row[column] for row in new_rows] for column in range(len(tags))]
        loop.set_all_values(columns)
        document.edited_text = parsed.as_string()
        validate_cif_text(document.edited_text)
    except (RuntimeError, ValueError, TypeError) as exc:
        if isinstance(exc, CifValidationError):
            raise
        raise CifValidationError(f"Could not apply CIF table edits: {exc}") from exc
    return [
        f"Site {site.label} remains partially occupied ({site.occupancy:g})."
        for site in sites
        if site.occupancy < 1
    ]


def save_cif_as(document: CifDocument, path: Path) -> None:
    """Validate and save edited CIF text to a new path."""
    validate_cif_text(document.edited_text)
    if path.resolve() == document.source_path.resolve():
        raise CifValidationError("Use a new filename; the original CIF is never overwritten.")
    path.write_text(document.edited_text, encoding="utf-8")

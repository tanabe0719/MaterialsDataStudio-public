from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.core.exceptions import CifValidationError
from materials_data_studio.modules.xrd.cif_backend import (
    AtomSite,
    SpaceGroupInfo,
    UnitCell,
    constrain_unit_cell,
    extract_cif_symmetry,
    extract_cif_tables,
    load_cif,
    parse_cif_number,
    save_cif_as,
    update_cif_tables,
    validate_cif_text,
)
from materials_data_studio.modules.xrd.phase_store import cif_metadata
from materials_data_studio.modules.xrd.simulation import (
    SimulatedPattern,
    broaden_pattern,
    preview_pattern_for_cell,
    preview_pattern_for_occupancies,
    restore_occupancy_preview_model,
    simulate_cif,
    store_occupancy_preview_model,
)

NACL_CIF = """data_NaCl
_symmetry_space_group_name_H-M 'F m -3 m'
_cell_length_a 5.6402
_cell_length_b 5.6402
_cell_length_c 5.6402
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Na1 Na 0 0 0 1
Cl1 Cl 0.5 0.5 0.5 1
"""


def test_cif_load_revert_validate_and_save_as(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    document = load_cif(source)
    assert document.original_text == NACL_CIF
    document.edited_text = document.edited_text.replace("5.6402", "5.6500")
    target = tmp_path / "NaCl-edited.cif"
    save_cif_as(document, target)
    assert "5.6500" in target.read_text()
    assert source.read_text() == NACL_CIF
    document.revert()
    assert document.edited_text == NACL_CIF


def test_cif_validation_rejects_nonphysical_cell_and_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    document = load_cif(source)
    with pytest.raises(CifValidationError, match="lengths"):
        validate_cif_text(NACL_CIF.replace("5.6402", "-1"))
    with pytest.raises(CifValidationError, match="never overwritten"):
        save_cif_as(document, source)


def test_cif_numbers_with_parenthesized_uncertainties_are_accepted(
    tmp_path: Path,
) -> None:
    uncertain = (
        NACL_CIF.replace("5.6402", "5.6402(3)")
        .replace("90\n", "90.0(2)\n")
        .replace("Na1 Na 0 0 0 1", "Na1 Na 0.000(2) 0 0 0.80(4)")
        .replace("Cl1 Cl 0.5 0.5 0.5 1", "Cl1 Cl 0.5 0.5 0.5 1.0(1)")
    )
    source = tmp_path / "uncertain.cif"
    source.write_text(uncertain)
    document = load_cif(source)
    validate_cif_text(document.edited_text)
    cell, sites, warnings = extract_cif_tables(document)
    assert parse_cif_number("2.38(4)") == pytest.approx(2.38)
    assert parse_cif_number("1.25(3)e2") == pytest.approx(125.0)
    assert cell.a == pytest.approx(5.6402)
    assert cell.alpha == pytest.approx(90.0)
    assert sites[0].fract_x == pytest.approx(0.0)
    assert sites[0].occupancy == pytest.approx(0.8)
    assert warnings
    pattern = simulate_cif(document, wavelength_a=1.540598, two_theta_range=(20, 80))
    assert len(pattern.two_theta_deg) > 0


def test_site_occupancy_changes_simulated_intensity(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    document = load_cif(source)
    original = simulate_cif(document, wavelength_a=1.540598, two_theta_range=(20, 80))
    cell, sites, _warnings = extract_cif_tables(document)
    sites[0].occupancy = 0.5
    update_cif_tables(document, cell, sites, extract_cif_symmetry(document))
    changed = simulate_cif(document, wavelength_a=1.540598, two_theta_range=(20, 80))
    np.testing.assert_allclose(changed.two_theta_deg, original.two_theta_deg)
    assert not np.allclose(changed.intensity, original.intensity)


def test_cached_occupancy_preview_matches_exact_recalculation(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    document = load_cif(source)
    original = simulate_cif(document, wavelength_a=1.540598, two_theta_range=(20, 80))
    assert original.occupancy_model is not None
    preview = preview_pattern_for_occupancies(
        original, {"Na1": 0.5, "Cl1": 1.0}
    )

    cell, sites, _warnings = extract_cif_tables(document)
    sites[0].occupancy = 0.5
    update_cif_tables(document, cell, sites, extract_cif_symmetry(document))
    exact = simulate_cif(document, wavelength_a=1.540598, two_theta_range=(20, 80))
    np.testing.assert_allclose(preview.two_theta_deg, exact.two_theta_deg)
    np.testing.assert_allclose(preview.intensity, exact.intensity, rtol=2e-6, atol=2e-6)


def test_occupancy_preview_cache_can_be_saved_and_restored(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    pattern = simulate_cif(
        load_cif(source), wavelength_a=1.540598, two_theta_range=(20, 80)
    )
    assert pattern.occupancy_model is not None
    arrays: dict[str, np.ndarray] = {}
    metadata: dict[str, object] = {}
    store_occupancy_preview_model(pattern.occupancy_model, arrays, metadata)
    restored = restore_occupancy_preview_model(arrays, metadata)
    assert restored is not None
    np.testing.assert_allclose(
        restored.intensities({"Na1": 0.65, "Cl1": 1.0}),
        pattern.occupancy_model.intensities({"Na1": 0.65, "Cl1": 1.0}),
    )


def test_table_edit_preserves_unrelated_tags_and_supports_site_changes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF + "_audit_creation_method 'test marker'\n")
    document = load_cif(source)
    cell, sites, warnings = extract_cif_tables(document)
    assert not warnings
    sites[0].occupancy = 0.8
    sites.append(AtomSite("Na2", "Na", 0.25, 0.25, 0.25, 1.0, 0.01))
    updated_cell = UnitCell(5.7, cell.b, cell.c, cell.alpha, cell.beta, cell.gamma)
    update_warnings = update_cif_tables(
        document,
        updated_cell,
        sites,
        extract_cif_symmetry(document),
    )
    assert "test marker" in document.edited_text
    assert "_symmetry_space_group_name_H-M" in document.edited_text
    assert "Na2" in document.edited_text
    assert update_warnings
    extracted_cell, extracted_sites, extracted_warnings = extract_cif_tables(document)
    assert extracted_cell.a == pytest.approx(5.7)
    assert extracted_cell.b == pytest.approx(5.7)
    assert extracted_cell.c == pytest.approx(5.7)
    assert extracted_sites[0].occupancy == pytest.approx(0.8)
    assert extracted_sites[-1].displacement == pytest.approx(0.01)
    assert extracted_warnings


def test_known_nacl_pattern_and_gaussian_profile(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    document = load_cif(source)
    pattern = simulate_cif(
        document,
        wavelength_a=1.540598,
        two_theta_range=(20, 80),
        fwhm_deg=0.15,
        profile_points=1200,
    )
    assert pattern.two_theta_deg[0] == pytest.approx(27.37, abs=0.15)
    assert np.max(pattern.intensity) == pytest.approx(100)
    assert pattern.profile_x is not None
    assert pattern.profile_y is not None
    assert np.max(pattern.profile_y) == pytest.approx(100)
    assert pattern.hkl_labels is not None
    assert pattern.hkl_labels[0]
    assert pattern.d_spacing_a is not None
    assert pattern.d_spacing_a[0] > 0


def test_simulated_two_theta_uses_selected_wavelength(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    document = load_cif(source)
    copper = simulate_cif(document, wavelength_a=1.540598, two_theta_range=(5, 80))
    molybdenum = simulate_cif(document, wavelength_a=0.71073, two_theta_range=(5, 80))
    assert molybdenum.two_theta_deg[0] < copper.two_theta_deg[0]
    assert copper.d_spacing_a is not None
    assert molybdenum.d_spacing_a is not None
    assert molybdenum.d_spacing_a[0] == pytest.approx(copper.d_spacing_a[0])


@pytest.mark.parametrize(
    ("symmetry", "changed", "expected"),
    [
        (
            SpaceGroupInfo("P 4/m m m", 123, "tetragonal"),
            "b",
            UnitCell(6, 6, 7, 90, 90, 90),
        ),
        (
            SpaceGroupInfo("F m -3 m", 225, "cubic"),
            "c",
            UnitCell(7, 7, 7, 90, 90, 90),
        ),
    ],
)
def test_space_group_constrains_lattice_parameters(
    symmetry: SpaceGroupInfo,
    changed: str,
    expected: UnitCell,
) -> None:
    constrained = constrain_unit_cell(
        UnitCell(5, 6, 7, 80, 100, 110),
        symmetry,
        changed_parameter=changed,
    )
    assert constrained == expected


def test_cif_symmetry_is_extracted(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    symmetry = extract_cif_symmetry(load_cif(source))
    assert symmetry.number == 225
    assert symmetry.crystal_system == "cubic"


def test_default_profile_uses_high_resolution_step(tmp_path: Path) -> None:
    source = tmp_path / "NaCl.cif"
    source.write_text(NACL_CIF)
    pattern = simulate_cif(
        load_cif(source),
        wavelength_a=1.540598,
        two_theta_range=(20, 80),
        fwhm_deg=0.15,
    )
    assert pattern.profile_x is not None
    assert len(pattern.profile_x) == 6001
    assert np.diff(pattern.profile_x).mean() == pytest.approx(0.01)


def test_existing_sticks_can_be_rebroadened_without_diffraction_simulation() -> None:
    sticks = SimulatedPattern(
        np.array([20.0, 40.0]),
        np.array([100.0, 50.0]),
        hkl_labels=["(1 0 0)", "(2 0 0)"],
        d_spacing_a=np.array([2.0, 1.0]),
    )
    narrow = broaden_pattern(
        sticks,
        two_theta_range=(10.0, 50.0),
        fwhm_deg=0.1,
        profile_step_deg=0.01,
    )
    wide = broaden_pattern(
        sticks,
        two_theta_range=(10.0, 50.0),
        fwhm_deg=0.5,
        profile_step_deg=0.01,
    )
    assert narrow.profile_y is not None and wide.profile_y is not None
    assert np.count_nonzero(wide.profile_y > 50) > np.count_nonzero(
        narrow.profile_y > 50
    )
    np.testing.assert_array_equal(wide.two_theta_deg, sticks.two_theta_deg)
    assert wide.hkl_labels == sticks.hkl_labels


def test_live_cell_preview_moves_reflections_from_reciprocal_metric() -> None:
    sticks = SimulatedPattern(
        np.array([17.7, 35.9]),
        np.array([100.0, 40.0]),
        hkl_labels=["(1 0 0)", "(2 0 0)"],
        d_spacing_a=np.array([5.0, 2.5]),
    )
    expanded = preview_pattern_for_cell(
        sticks,
        cell=UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0),
        wavelength_a=1.540598,
        two_theta_range=(1.0, 90.0),
    )
    expected = np.rad2deg(2.0 * np.arcsin(1.540598 / (2.0 * 10.0)))
    assert expanded.two_theta_deg[0] == pytest.approx(expected)
    assert expanded.two_theta_deg[0] < sticks.two_theta_deg[0]
    np.testing.assert_array_equal(expanded.intensity, sticks.intensity)
    assert expanded.hkl_labels == sticks.hkl_labels


@pytest.mark.parametrize("split_uncertainty", [False, True])
def test_metadata_blocks_and_cell_uncertainties_share_structure_selection(tmp_path, split_uncertainty):
    import gemmi

    structure = NACL_CIF
    if split_uncertainty:
        structure = structure.replace("5.6402", "5.6402    (3)")
    header = "data_global\n_audit_creation_method 'Synthetic export'\n_note\n;\n_cell_length_a 99 (7)\n;\n"
    tail = "\ndata_measurement\nloop_\n_observation_x\n_observation_y\n1 2\n3 4\n"
    text = header + structure + tail
    path = tmp_path / "multiblock.cif"
    path.write_text(text)
    original_bytes = path.read_bytes()
    document = load_cif(path)
    validate_cif_text(document.edited_text)
    cell, sites, _warnings = extract_cif_tables(document)
    assert cell.a == pytest.approx(5.6402)
    assert len(sites) == 2
    symmetry = extract_cif_symmetry(document)
    assert symmetry.number == 225
    metadata = cif_metadata(text, "synthetic", "User CIF")
    assert metadata.space_group_number == 225
    expected_path = tmp_path / "single.cif"
    expected_path.write_text(NACL_CIF)
    expected = simulate_cif(load_cif(expected_path), wavelength_a=1.540598, two_theta_range=(20, 80))
    actual = simulate_cif(document, wavelength_a=1.540598, two_theta_range=(20, 80))
    np.testing.assert_allclose(actual.two_theta_deg, expected.two_theta_deg)
    np.testing.assert_allclose(actual.intensity, expected.intensity)
    cell.a = cell.b = cell.c = 5.7
    update_cif_tables(document, cell, sites, symmetry)
    saved = tmp_path / "edited.cif"
    save_cif_as(document, saved)
    parsed = gemmi.cif.read_file(str(saved))
    assert len(parsed) == 3
    assert "99 (7)" in parsed[0].find_value("_note")
    assert list(parsed[2].find_loop("_observation_y")) == ["2", "4"]
    assert extract_cif_tables(load_cif(saved))[0].a == pytest.approx(5.7)
    assert path.read_bytes() == original_bytes
    assert document.original_text == text
    document.revert()
    assert extract_cif_tables(document)[0].a == pytest.approx(5.6402)


def test_multiple_structures_are_rejected_as_ambiguous():
    with pytest.raises(CifValidationError, match="unambiguous"):
        validate_cif_text(NACL_CIF + NACL_CIF.replace("data_NaCl", "data_second"))


def test_unrelated_invalid_cif_is_not_repaired(tmp_path):
    from materials_data_studio.core.exceptions import DataImportError

    path = tmp_path / "invalid.cif"
    path.write_text(NACL_CIF.replace("_cell_length_a 5.6402", "_cell_length_a 5.6402 junk"))
    with pytest.raises(DataImportError, match="Could not parse"):
        load_cif(path)


@pytest.mark.parametrize("name", ["α-synthetic", "Î±-synthetic"])
def test_unquoted_unicode_chemical_name_preserves_text_and_simulation(tmp_path, name):
    import gemmi

    note = "_chemical_name_common β-inside-note"
    text = (NACL_CIF + f"_chemical_name_structure_type {name} # exported name\n"
            + "_chemical_name_common 'γ quoted name'\n_note\n;\n" + note + "\n;\n")
    path = tmp_path / "unicode.cif"
    path.write_text(text, encoding="utf-8")
    original_bytes = path.read_bytes()
    document = load_cif(path)
    validate_cif_text(document.edited_text)
    cell, sites, _warnings = extract_cif_tables(document)
    assert len(sites) == 2
    assert extract_cif_symmetry(document).number == 225
    assert cif_metadata(text, "synthetic", "User CIF").space_group_number == 225
    reference = tmp_path / "reference.cif"
    reference.write_text(NACL_CIF)
    expected = simulate_cif(load_cif(reference), wavelength_a=1.540598, two_theta_range=(20, 80))
    actual = simulate_cif(document, wavelength_a=1.540598, two_theta_range=(20, 80))
    np.testing.assert_allclose(actual.two_theta_deg, expected.two_theta_deg)
    np.testing.assert_allclose(actual.intensity, expected.intensity)
    update_cif_tables(document, cell, sites)
    saved = tmp_path / "edited.cif"
    save_cif_as(document, saved)
    block = gemmi.cif.read_file(str(saved)).sole_block()
    assert gemmi.cif.as_string(block.find_value("_chemical_name_structure_type")) == name
    assert gemmi.cif.as_string(block.find_value("_chemical_name_common")) == "γ quoted name"
    assert note in block.find_value("_note")
    assert document.original_text == text
    assert path.read_bytes() == original_bytes


def test_unicode_comment_is_not_interpreted_as_missing_chemical_name(tmp_path):
    from materials_data_studio.core.exceptions import DataImportError

    path = tmp_path / "missing-name.cif"
    path.write_text(NACL_CIF + "_chemical_name_common #α-comment\n", encoding="utf-8")
    with pytest.raises(DataImportError):
        load_cif(path)

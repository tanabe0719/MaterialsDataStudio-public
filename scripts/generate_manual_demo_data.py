"""Generate anonymous synthetic data used only by the screenshot-based manual."""

# ruff: noqa: E402

from __future__ import annotations

import math
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from materials_data_studio.modules.ppms.heat_capacity import debye_einstein_model
from materials_data_studio.modules.xrd.peak_fitting.profile import pseudo_voigt_profile
from materials_data_studio.modules.xrd.peak_fitting.reflections import shifted_two_theta

OUTPUT = ROOT / "docs" / "demo_data"
WAVELENGTH_A = 1.540598


def _write(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _cubic_d(a: float, h: int, k: int, hkl_l: int) -> float:
    return a / math.sqrt(h * h + k * k + hkl_l * hkl_l)


def _xrd_pattern(a: float, *, shift: float, fwhm: float, scale: float) -> tuple[np.ndarray, np.ndarray]:
    x = np.linspace(15.0, 82.0, 2681)
    background = 18.0 + 0.10 * (x - 15.0) + 0.0015 * (x - 47.0) ** 2
    y = background + 0.35 * np.sin(2.3 * x) + 0.18 * np.cos(0.71 * x)
    hkls = (
        (1, 0, 0),
        (1, 1, 0),
        (1, 1, 1),
        (2, 0, 0),
        (2, 1, 0),
        (2, 1, 1),
        (2, 2, 0),
        (2, 2, 1),
        (3, 0, 0),
        (3, 1, 0),
        (3, 1, 1),
        (2, 2, 2),
        (3, 2, 0),
    )
    areas = np.asarray(
        [250, 1250, 360, 820, 280, 940, 620, 210, 430, 760, 260, 370, 230],
        dtype=float,
    )
    for hkl, area in zip(hkls, areas, strict=True):
        position = shifted_two_theta(_cubic_d(a, *hkl), WAVELENGTH_A)
        if x[0] <= position <= x[-1]:
            y += scale * area * pseudo_voigt_profile(x, position + shift, fwhm, 0.34)
    return x, y


def generate_xrd() -> None:
    x, y = _xrd_pattern(4.1000, shift=0.075, fwhm=0.26, scale=1.0)
    _write(
        OUTPUT / "xrd_observed.xy",
        "# Synthetic cubic powder pattern; wavelength 1.540598 A\n"
        + "\n".join(f"{xv:.5f}\t{yv:.8f}" for xv, yv in zip(x, y, strict=True)),
    )
    x2, y2 = _xrd_pattern(4.1350, shift=-0.025, fwhm=0.34, scale=0.72)
    _write(
        OUTPUT / "xrd_comparison.xy",
        "# Synthetic comparison pattern; wavelength 1.540598 A\n"
        + "\n".join(f"{xv:.5f}\t{yv:.8f}" for xv, yv in zip(x2, y2, strict=True)),
    )
    for filename, x_values, y_values in (
        ("xrd_rasx_demo.rasx", x2, y2),
        ("xrd_rasx_demo_secondary.rasx", x, y),
    ):
        profile = "\n".join(
            f"{xv:.5f}\t{yv:.8f}\t1"
            for xv, yv in zip(x_values, y_values, strict=True)
        )
        with zipfile.ZipFile(
            OUTPUT / filename,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for name, payload in (
                ("Data0/Profile0.txt", profile),
                ("Data0/MesurementConditions0.xml", "<conditions synthetic='true'/>"),
                ("root.xml", "<rasx synthetic='true'/>"),
            ):
                member = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                member.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(member, payload)
    _write(
        OUTPUT / "demo_phase.cif",
        """data_synthetic_demo_phase
_audit_creation_method 'Materials Data Studio documentation generator'
_chemical_name_common 'Synthetic cubic demo phase'
_symmetry_space_group_name_H-M 'P m -3 m'
_space_group_IT_number 221
_cell_length_a 4.1000
_cell_length_b 4.1000
_cell_length_c 4.1000
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
Cs1 Cs 0 0 0 1
Cl1 Cl 0.5 0.5 0.5 1
""",
    )


def _mpms_header() -> list[str]:
    return [
        "[Header]",
        "INFO, WEIGHT, 12.0",
        "INFO, TITLE, Synthetic documentation sample",
        "[Data]",
        "Time Stamp (sec),Magnetic Field (Oe),Temperature (K),Long Moment (emu)",
    ]


def generate_mpms() -> None:
    molar_mass = 58.44277
    moles = 0.012 / molar_mass
    field = 1000.0
    increasing = np.linspace(5.0, 300.0, 121)
    decreasing = np.linspace(299.5, 5.0, 121)
    rows: list[str] = _mpms_header()
    elapsed = 0
    for branch, temperatures in (("zfc", increasing), ("fc", decreasing)):
        for temperature in temperatures:
            chi = 0.00042 + 1.82 / (temperature + 24.0)
            if branch == "fc":
                chi *= 1.0 + 0.025 * math.exp(-temperature / 45.0)
            moment = chi * field * moles
            rows.append(f"{elapsed},{field:.3f},{temperature:.6f},{moment:.12g}")
            elapsed += 1
    _write(OUTPUT / "mpms_mt_demo.dc.dat", "\n".join(rows))

    fields = np.concatenate(
        (
            np.linspace(-50000.0, 50000.0, 121),
            np.linspace(49166.666667, -50000.0, 120),
        )
    )
    rows = _mpms_header()
    for index, value in enumerate(fields):
        increasing_branch = index < 121
        coercive = 2300.0 if increasing_branch else -2300.0
        moment = 0.0105 * math.tanh((value - coercive) / 6500.0) + 2.5e-8 * value
        rows.append(f"{index},{value:.6f},10.000000,{moment:.12g}")
    _write(OUTPUT / "mpms_mh_demo.dc.dat", "\n".join(rows))


def generate_heat_capacity() -> None:
    sample_mass_mg = 9.5
    molar_mass = 58.44277
    moles = sample_mass_mg * 1e-3 / molar_mass
    temperatures = np.concatenate(
        (np.linspace(2.0, 15.0, 30), np.linspace(18.0, 120.0, 35))
    )
    rows = [
        "[Header]",
        "INFO,9.5,SAMPLE MASS: Sample Mass (mg)",
        "INFO,0.02,SAMPLE MASS ERROR: Sample Mass Error (mg)",
        "INFO,58.44277,MOLWGHT: Formula Weight (g/mole)",
        "INFO,2,ATOMS: Atoms per Formula Unit",
        "INFO,Synthetic heat-capacity demonstration,TITLE",
        "INFO,Heat Capacity,APPNAME",
        "[Data]",
        (
            "Time Stamp (sec),Field (Oersted),Sample Temp (Kelvin),"
            "Samp HC (uJ/K),Samp HC Err (uJ/K),Fit Deviation,Sample Coupling,"
            "System Status,Comment"
        ),
    ]
    timestamp = 0
    for field in (0.0, 50000.0):
        molar_cp = debye_einstein_model(
            temperatures,
            theta_d=230.0,
            weight_d=0.85,
            gamma=0.006,
            theta_e=(410.0, 790.0),
            weight_e=(0.70, 0.45),
        )
        if field:
            molar_cp += 0.18 * np.exp(-((temperatures - 18.0) / 7.5) ** 2)
        for temperature, cp_value in zip(temperatures, molar_cp, strict=True):
            for delta, scale in ((-0.018, 0.9985), (0.018, 1.0015)):
                cp_uj_k = cp_value * moles * 1e6 * scale
                error = max(cp_uj_k * 0.008, 0.002)
                rows.append(
                    f"{timestamp},{field:.1f},{temperature + delta:.6f},"
                    f"{cp_uj_k:.9g},{error:.9g},0.001,0.995,1,"
                )
                timestamp += 1
    _write(OUTPUT / "ppms_heat_capacity_demo.dat", "\n".join(rows))


def generate_resistivity() -> None:
    warming = np.linspace(30.0, 300.0, 91)
    cooling = np.linspace(297.0, 30.0, 90)
    up_field = np.linspace(-50000.0, 50000.0, 121)
    down_field = np.linspace(49166.666667, -50000.0, 120)
    temperature = np.concatenate(
        (warming, cooling, np.full(len(up_field) + len(down_field), 50.0))
    )
    field = np.concatenate(
        (
            np.zeros(len(warming) + len(cooling)),
            up_field,
            down_field,
        )
    )
    k_b = 8.617333262e-5
    bridge_1 = 0.020 * np.exp(0.018 / (k_b * temperature))
    bridge_2 = 0.005 * np.exp((8000.0 / temperature) ** 0.25)
    sweep_mask = np.arange(len(temperature)) >= len(warming) + len(cooling)
    bridge_1[sweep_mask] *= 1.0 + 0.22 * (field[sweep_mask] / 50000.0) ** 2
    bridge_2[sweep_mask] *= 1.0 + 0.08 * (field[sweep_mask] / 50000.0) ** 2
    rows = [
        "[Header]",
        "INFO,Synthetic Arrhenius bridge,Sample1 Name",
        "INFO,Ohm,Sample1 Units",
        "INFO,Synthetic VRH bridge,Sample2 Name",
        "INFO,Ohm,Sample2 Units",
        "[Data]",
        (
            "Time Stamp (sec),Temperature (K),Magnetic Field (Oe),"
            "Bridge 1 Resistance (Ohms),Bridge 1 Std Dev (Ohms),"
            "Bridge 2 Resistance (Ohms),Bridge 2 Std Dev (Ohms)"
        ),
    ]
    for index, values in enumerate(
        zip(temperature, field, bridge_1, bridge_2, strict=True)
    ):
        temp, magnetic_field, first, second = values
        rows.append(
            f"{index},{temp:.7f},{magnetic_field:.7f},{first:.12g},"
            f"{first * 0.006:.12g},{second:.12g},{second * 0.006:.12g}"
        )
    _write(OUTPUT / "ppms_resistivity_demo.dat", "\n".join(rows))


def generate_custom() -> None:
    temperature = np.linspace(5.0, 300.0, 80)
    signal = 1.25 + 2.40 * temperature + 0.5 * np.sin(temperature / 18.0)
    auxiliary = np.sqrt(temperature) + 0.02 * temperature
    rows = [
        "[Header]",
        "INFO,7.5,Sample Weight (mg)",
        "INFO,Synthetic custom-column demonstration,Title",
        "[Data]",
        "Time Stamp (sec),Temperature (K),Magnetic Field (Oe),Custom Signal,Aux Signal,Comment",
    ]
    for index, (temp, primary, aux) in enumerate(
        zip(temperature, signal, auxiliary, strict=True)
    ):
        rows.append(
            f"{index},{temp:.7f},{1000 * math.sin(index / 10):.7f},"
            f"{primary:.9g},{aux:.9g},synthetic"
        )
    _write(OUTPUT / "ppms_custom_demo.dat", "\n".join(rows))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    generate_xrd()
    generate_mpms()
    generate_heat_capacity()
    generate_resistivity()
    generate_custom()
    print(f"Generated 10 synthetic documentation files in {OUTPUT}")


if __name__ == "__main__":
    main()

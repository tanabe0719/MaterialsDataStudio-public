"""Validate manual sources, screenshots, generated HTML, and PDF."""

from __future__ import annotations

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "screenshot_plan.md"
MANUALS = (
    (
        ROOT / "docs" / "user_manual_en.md",
        ROOT / "dist_manual" / "Materials_Data_Studio_User_Manual_EN.html",
        ROOT / "dist_manual" / "Materials_Data_Studio_User_Manual_EN.pdf",
    ),
    (
        ROOT / "docs" / "user_manual.md",
        ROOT / "dist_manual" / "Materials_Data_Studio_User_Manual.html",
        ROOT / "dist_manual" / "Materials_Data_Studio_User_Manual.pdf",
    ),
    (
        ROOT / "docs" / "graph_viewer_manual_en.md",
        ROOT / "dist_manual" / "Materials_Data_Studio_Graph_Viewer_Manual_EN.html",
        ROOT / "dist_manual" / "Materials_Data_Studio_Graph_Viewer_Manual_EN.pdf",
    ),
    (
        ROOT / "docs" / "graph_viewer_manual.md",
        ROOT / "dist_manual" / "Materials_Data_Studio_Graph_Viewer_Manual.html",
        ROOT / "dist_manual" / "Materials_Data_Studio_Graph_Viewer_Manual.pdf",
    ),
)
EXPECTED_PREFIXES = tuple(f"{index:02d}_" for index in range(1, 35))


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a valid PNG: {path}")
    return struct.unpack(">II", header[16:24])


def main() -> None:
    errors: list[str] = []
    plan_text = PLAN.read_text(encoding="utf-8")
    manual_image_counts: list[int] = []
    for manual, _html, _pdf in MANUALS:
        manual_text = manual.read_text(encoding="utf-8")
        image_refs = re.findall(
            r"!\[[^\]]*\]\((screenshots/[^)]+\.png)\)", manual_text
        )
        manual_image_counts.append(len(image_refs))
        if len(image_refs) < 15:
            errors.append(
                f"Only {len(image_refs)} screenshots are referenced in {manual.name}."
            )
        for reference in image_refs:
            image = manual.parent / reference
            if not image.is_file():
                errors.append(f"Missing referenced screenshot: {reference}")
                continue
            width, height = png_size(image)
            is_dialog = reference.startswith(
                (
                    "screenshots/19_",
                    "screenshots/20_",
                    "screenshots/21_",
                    "screenshots/24_",
                    "screenshots/26_",
                    "screenshots/30_",
                    "screenshots/31_",
                    "screenshots/32_",
                    "screenshots/33_",
                    "screenshots/38_",
                    "screenshots/41_",
                    "screenshots/72_",
                    "screenshots/74_",
                    "screenshots/75_",
                    "screenshots/76_",
                    "screenshots/42_",
                    "screenshots/43_",
                    "screenshots/44_",
                    "screenshots/45_",
                    "screenshots/46_",
                    "screenshots/47_",
                    "screenshots/50_",
                    "screenshots/53_",
                    "screenshots/51_",
                    "screenshots/63_",
                    "screenshots/64_",
                    "screenshots/65_",
                    "screenshots/66_",
                    "screenshots/67_",
                    "screenshots/68_",
                    "screenshots/69_",
                    "screenshots/82_",
                    "screenshots/83_",
                    "screenshots/84_",
                    "screenshots/89_",
                )
            )
            minimum = (450, 160) if is_dialog else (900, 500)
            if width < minimum[0] or height < minimum[1]:
                errors.append(
                    f"Screenshot is too small ({width}x{height}): {reference}"
                )

    screenshots = sorted((ROOT / "docs" / "screenshots").glob("*.png"))
    names = [path.name for path in screenshots]
    for prefix in EXPECTED_PREFIXES:
        if not any(name.startswith(prefix) for name in names):
            errors.append(f"No screenshot with required prefix {prefix}")
    if "06b_peak_fitting_progress.png" not in names:
        errors.append("Missing peak-fitting progress screenshot.")
    if "20b_session_open_dialog.png" not in names:
        errors.append("Missing Open Session dialog screenshot.")

    plan_names = set(re.findall(r"`([0-9][0-9a-z]?_[^`]+\.png)`", plan_text))
    for name in plan_names:
        if not (ROOT / "docs" / "screenshots" / name).is_file():
            errors.append(f"Planned screenshot was not captured: {name}")

    required_labels = (
        "New Session",
        "Open Session",
        "Save Session",
        "Import Data",
        "Export Data",
        "Export Figure",
        "Send to Igor Pro",
        "File",
        "Redo",
        "Preferences",
        "User Manual",
        "About",
        "Help",
        "Tools",
        "Molecular Weight Calculator",
        "Shannon Radii",
        "RASX Converter",
        "Igor PXP Viewer",
        "Open experiment / graph (.pxp / .mdsg / .h5xp)",
        "Open Graph Document (.mdsg / .h5xp)",
        "Save Graph Document",
        "Apply properties",
        "Convert Igor / IPF",
        "Analyze / Convert",
        "Entry routine",
        "Insert into Python (not run)",
        "Delete Selected",
        "Clear History",
        "Pauling",
        "Datasets",
        "Estimate Background",
        "Apply Background",
        "CIF Editor",
        "Apply and Simulate",
        "Run Fit",
        "Stop Fit",
        "Undo Last Fit",
        "Heat Cap.",
        "Resistivity",
        "Custom Plot",
        "Run selected linearized fit",
    )
    forbidden = (
        r"C:\\Users\\",
        r"/Users/",
        r"\bTODO\b",
        r"\bTBD\b",
        r"\bPLACEHOLDER\b",
    )
    for manual, html_path, pdf_path in MANUALS:
        manual_text = manual.read_text(encoding="utf-8")
        graph_labels = ("Open Graph Document (.mdsg / .h5xp)", "Save Graph Document",
                        "Apply properties", "Convert Igor / IPF", "Analyze / Convert",
                        "Entry routine", "Insert into Python (not run)")
        is_graph = manual.name.startswith("graph_viewer_")
        labels = (*graph_labels, "Graph Viewer Manual", "Python / mds Help", "Load General Text") if is_graph else tuple(label for label in required_labels if label not in graph_labels and label != "Open experiment / graph (.pxp / .mdsg / .h5xp)")
        for label in labels:
            if label not in manual_text:
                errors.append(
                    f"Critical GUI label is not documented in {manual.name}: {label}"
                )
        if not is_graph and ("ModifyGraph tickEnab" in manual_text or "scope()" in manual_text):
            errors.append(f"Detailed Graph Viewer documentation leaked into {manual.name}")
        for pattern in forbidden:
            if re.search(pattern, manual_text, flags=re.IGNORECASE):
                errors.append(f"Forbidden pattern in {manual.name}: {pattern}")

        if not html_path.is_file() or html_path.stat().st_size < 100_000:
            errors.append(f"Manual HTML is missing or too small: {html_path.name}")
        if not pdf_path.is_file() or pdf_path.stat().st_size < 100_000:
            errors.append(f"Manual PDF is missing or too small: {pdf_path.name}")
        elif not pdf_path.read_bytes().startswith(b"%PDF-"):
            errors.append(f"Manual PDF signature is invalid: {pdf_path.name}")

    if errors:
        raise SystemExit("Manual validation failed:\n- " + "\n- ".join(errors))
    print(
        "Manual validation passed: "
        f"{manual_image_counts} referenced images, {len(screenshots)} PNG files, "
        f"{len(MANUALS)} localized PDFs."
    )


if __name__ == "__main__":
    main()

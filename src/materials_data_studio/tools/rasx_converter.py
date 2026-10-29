"""Batch conversion of Rigaku RASX profiles to two-column text files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from materials_data_studio.modules.xrd.parser import parse_xrd_rasx

SUPPORTED_OUTPUT_FORMATS = ("xy", "dat")


@dataclass(frozen=True, slots=True)
class RasxConversion:
    """One completed RASX conversion."""

    source: Path
    destination: Path
    point_count: int


def convert_rasx_file(
    source: Path,
    *,
    output_format: str = "xy",
    output_directory: Path | None = None,
    overwrite: bool = False,
) -> RasxConversion:
    """Convert one RASX profile using the same parser as the XRD workspace."""
    normalized_format = output_format.lower().lstrip(".")
    if normalized_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError("Output format must be 'xy' or 'dat'.")
    source = Path(source)
    destination_directory = source.parent if output_directory is None else Path(output_directory)
    if not destination_directory.is_dir():
        raise NotADirectoryError(
            f"Output directory does not exist: {destination_directory}"
        )
    destination = destination_directory / f"{source.stem}.{normalized_format}"
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists: {destination}")

    parsed = parse_xrd_rasx(source)
    lines = (
        f"{two_theta:.12g}\t{intensity:.12g}\n"
        for two_theta, intensity in zip(parsed.x, parsed.intensity, strict=True)
    )
    destination.write_text("".join(lines), encoding="utf-8", newline="\n")
    return RasxConversion(source, destination, parsed.valid_rows)


def convert_rasx_files(
    sources: list[Path],
    *,
    output_format: str = "xy",
    output_directory: Path | None = None,
    overwrite: bool = False,
) -> list[RasxConversion]:
    """Convert multiple profiles, preserving each source directory by default."""
    return [
        convert_rasx_file(
            source,
            output_format=output_format,
            output_directory=output_directory,
            overwrite=overwrite,
        )
        for source in sources
    ]

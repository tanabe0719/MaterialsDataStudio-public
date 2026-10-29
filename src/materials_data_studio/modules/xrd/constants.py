"""Laboratory X-ray wavelength presets in angstroms.

Values are representative reference wavelengths. Instrument-specific effective
wavelengths should be entered with the Custom option when appropriate.
"""

WAVELENGTH_PRESETS_A: dict[str, float | None] = {
    "Cu Kα1/2": 1.540_598,
    "Cu Kα1": 1.540_598,
    "Cu Kα average": 1.541_874,
    "Mo Kα1": 0.709_317,
    "Co Kα1": 1.788_965,
    "Fe Kα1": 1.936_042,
    "Cr Kα1": 2.289_700,
    "Ag Kα1": 0.559_421,
    "Custom": None,
}


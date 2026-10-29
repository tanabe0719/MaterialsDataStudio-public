# Toolbar icon assets

The `light/` and `dark/` directories contain matching 64 × 64 RGBA PNG
icons for the compact quick-action toolbar.

The source sprite was created with the built-in image-generation workflow as
a strict 4 × 2 grid of modern rounded outline glyphs. The Light set uses
deep slate-navy strokes with cyan action accents. The Dark set preserves the
same geometry and uses cool off-white strokes with brighter cyan accents.
The flat chroma-key background was removed locally, each cell was cropped,
centered on a common transparent canvas, and downsampled with Lanczos
filtering.

The icon order and filenames are:

1. `new.png`
2. `open.png`
3. `save.png`
4. `import.png`
5. `export_data.png`
6. `export_figure.png`
7. `undo.png`
8. `redo.png`
9. `send_to_igor.svg` (deterministic theme-matched transfer glyph)

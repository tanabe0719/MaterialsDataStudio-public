# Materials Project reference database: data, build and validation

## Existing implementation and reuse

The existing `QualXCODInorganicBackend` reads four separately installed SQLite
files; `UserCIFBackend` caches only changed user structures. Both use the indexed
`peaks(d, phase_id, rank)` lookup in `reference_index.py`, followed by
`match_peaks` in `phase_models.py`. `MaterialsProjectBackend` implements those
same metadata, reference-peak and retrieval interfaces. COD extraction, its
installed files and its score weights are preserved. No MP-specific search engine
is introduced. `ReferenceSearchEngine` owns cancellation, bounded retrieval,
detailed assignment, sorting and database provenance for all three sources.

## Observed public source, 2026-10-25 JST

AWS CLI was absent. Equivalent anonymous, read-only S3 ListObjectsV2 requests
were used; no bucket-wide recursive download was performed.

- Bucket: `materialsproject-build`, region `us-east-1`.
- Root categories observed: `collections`, `current-build`, `images`, `objects`,
  `static-collections`, `validation`, plus the browser index.
- Both `collections/summary` and `collections/materials` are Delta tables.
- Both logs contain transaction versions 0 and 1. Version 1 includes partitions
  `2026-04-13` and `2026-09-28`; the builder pins transaction **1** and partition
  **2026-09-28**, rather than mixing the two releases.
- Selected summary partition has **280,159** records in **one 414,503,512-byte**
  Parquet object:
  `version=2026-09-28/part-00000-2cc5f2cc-2a79-4051-b013-2f9dec6a302e-c000.zstd.parquet`.
- The Delta library, not an arbitrary recursive Parquet listing, resolves the
  committed file inventory. Its full identity, selected columns and schema are
  saved in the local index manifest.
- The observed `builder_meta` struct contains `license`, `batch_id`,
  `database_version`, `run_id`, software versions and build date.
- Actual license codes are **BY-C** and **BY-NC**, not the guessed spelling BY.
  Official emmet defines those two codes and uses BY-C for commercial licensing.
  **116,948** records have BY-NC / `gnome_r2scan_statics`; **163,211** have BY-C.
  GNoME, unknown/null licenses, wrong versions and deprecated records are excluded.
  An optional explicit site-count restriction is recorded, never silently applied.

### Evaluated acquisition choices

1. **Delta/Parquet — selected.** `deltalake` resolves the transaction, Arrow
   selects only required columns and yields batches of 64 records. Readahead is
   bounded. Anonymous S3 needs neither MP API keys nor AWS credentials. Network
   reads occur only in the developer builder.
2. **MPDataset — reviewed in official documentation.** Its `pyarrow_dataset`
   supports the same batch/column approach, but mp-api and its model dependencies
   are unnecessary for the selected public data. It was not installed or
   represented as execution-tested.
3. **Versioned JSONL — fallback supported.** Old release directories including
   2025-09-25 were observed. Local JSONL/GZIP exports with explicit version and
   license provenance can be streamed; old releases lacking those fields are
   deliberately not assumed safe. A JSONL SHA-256 pins resume identity.
4. **Per-material REST API — not used.** It would require user credentials and
   many requests. Coordinates for selected candidates are read from the local
   index and can be exported as CIF, so no credential storage is needed.

## Build, resume and install

Builder dependencies are optional and excluded from the normal EXE environment:

```powershell
uv sync --extra dev --extra mp-builder
uv run python scripts/build_mp_index.py inspect --uri s3://materialsproject-build/collections/summary --delta-version 1 --database-version 2026-09-28
uv run python scripts/build_mp_index.py build --uri s3://materialsproject-build/collections/summary --delta-version 1 --database-version 2026-09-28 --engine batch --cache-dir C:/MP/source-cache --limit 1000 --workers 8 --output C:/MP/mp.sqlite
```

Run the same build again to process the next records; `--limit 10000` processes
up to 10,000 **additional input records**, including exclusions. Omit `--limit`
for the complete eligible partition. `--retry-failed` retries only failures and
then continues unprocessed records. `--max-sites` selects an explicit subset;
default 0 imposes no site-count exclusion. `--formulas Si ZnO` selects a separate
formula-scoped dataset, with that selection included in snapshot identity.
Never resume an existing output with different source/selection/calculator settings.

For high-latency S3 links, the optional cache downloads **only the committed
objects selected by Delta and the pinned partition**, not the bucket or all
Parquet files. In this release that is one 414,503,512-byte summary object.
Eight bounded 8 MiB HTTP ranges are in flight at most; responses must match their
range and length. Free space is checked, completed files have SHA-256 checksums,
and changed cache files are rejected. Local Arrow scanning still selects only
required columns and eligible records. The source cache includes the original
file's other summary columns and restricted-license rows; those are not copied
into the derived index and the cache is not a distributable dataset.
Snapshot file checksums are retained in the manifest. Direct projected S3
scanning remains available by omitting `--cache-dir`.

`--prioritize-formulas Si ZnO TiO2 Fe2O3 BaTiO3` changes processing order, not
dataset membership. Duplicates are skipped. Unlike `--formulas`, it can be used
when resuming the same full dataset.

```powershell
uv run python scripts/build_mp_index.py verify --output C:/MP/mp.sqlite
```

In MDS, open **Reference Databases → Import MP Index…** and choose the completed
SQLite file. The adjacent `.sqlite.sha256`, when present, is checked. Verification
also checks SQLite integrity, format/license, completed-build flag, record counts
and finite positive reference arrays. Registration does not copy or delete source
files. **Remove MP registration** leaves the database on disk.

Index building must finish before registering it. Ctrl+C preserves committed
checkpoints; an interrupted-build flag prevents importing an unfinished snapshot.
Each completed material is committed individually. Worker submission is bounded
to twice the configured process count (1–16); structures are never all loaded as
Python objects. Arrow still decompresses selected Parquet column chunks, so
builder memory is larger than normal search memory and must be measured separately.

## Stored representation and calculation

A single portable SQLite uses the existing neutral peak-index tables plus
`cards` (metadata, full reference arrays, compressed structure, origin details),
`checkpoint` (status/reason), and a JSON manifest. This avoids a second binary
format and enables read-only SQLite access in Windows EXE and future services.
Legacy uncompressed structure rows are also readable.
Reference arrays and hkl information use lossless zlib-compressed JSON. Older
plain JSON patterns remain readable. No peak, coordinate or intensity is removed
or rounded by this storage change. Existing completed indexes can be copied into
a new compressed file with `scripts/compact_mp_index.py SOURCE OUTPUT`; the source
is retained, and the new file gets its own checksum. The full validated release
index contains 154,369 structures in 4,156,522,496 bytes, down from 14,279,819,264
bytes before pattern compression; it is intentionally not bundled in the EXE.

The standard path uses existing pymatgen `XRDCalculator`: Cu Kα1, 1.540598 Å,
1–150° 2θ, maximum intensity 100. Stored detailed reflections include d in Å,
intensity and hkl; the strongest 16 populate the shared inverted index. Weak
reflections retained by pymatgen are preserved for detailed matching. The same
calculation version is required when resuming. The original structure hash,
MP ID, formula/elements, cell, symmetry, energy above hull (nullable), theoretical
flag, builder provenance, source license and database version are retained.
Non-diffraction site properties such as magnetic moments, forces and velocities
are omitted from the stored structure; atomic coordinates and occupancies remain.

The optional `--engine batch` evaluates **the same** pymatgen scattering model
in bounded NumPy blocks, reusing its reciprocal-lattice API, coefficient data,
angular/intensity tolerances and hkl-family convention. It does not refine symmetry
or substitute an alternative physical model. It avoids the standard loop's
repeated scans over all previously found reflections. The independent builder
version `mds-mp-batch-xrd-1` is recorded alongside pymatgen's version, and mixing
standard/batch paths within one resumed index is rejected. The batch path was
compared with stored standard results for 138 real MP standard-material structures
and the 100 most reflection-rich available records from the initial broad build;
all 238 had identical peak counts and d values, with intensity differences below
1e-6. Synthetic cubic, hexagonal, monoclinic and mixed-occupancy tests also check
angles, intensities, d and hkl families. The parity report is
`mp_batch_parity_v0140.json`. A one-million reciprocal-vector safety limit rejects
extreme cells as explicit failed records instead of exhausting memory.

The d-based lookup permits changing display wavelength, but does not recompute
anomalous scattering, wavelength-dependent intensities or reflections outside
the stored reciprocal-space range. Neither COD nor MP scores are calibrated
phase-presence probabilities or equivalent to QualX's FOM.

## Combined sources, ranking and duplicate candidates

- The default is **COD + MP + User CIF**. COD, MP and User CIF can also be selected
  individually. A database is never required merely to start MDS.
- **Search candidates · score and rank** in Step 2 searches all selected installed
  sources under the Step 1 peak-confirmation gate and merges by MDS Match Score.
  The former fallback checkbox and separate MP search action are absent.
- Missing selected sources are reported while available sources continue. If fewer
  than ten assigned positive-score results meet the cutoff, the display cutoff is
  lowered to retain up to ten available candidates. Actual scores and chemical
  filters are unchanged, and the adjustment is displayed explicitly.
- MP retrieval expands indexed d intervals by the selected cell-scale range;
  detailed matching searches a bounded scale grid and refines around its best
  interval. Default range is ±1%, selectable 0–3%. Zero shift stays a separate
  fixed instrumental setting. The chosen scale and penalty are saved.
- Scale penalty is explicitly a heuristic: **2 score points per 1% absolute
  cell scaling**. Both raw and penalized scores are stored. This discourages
  flexible accidental matches; it is not a physical strain probability model.
- Detailed ranking still reports position agreement, intensity/peak coverage,
  intensity similarity and unmatched observations/predictions. MP results are
  labelled **DFT-relaxed**. Energy above hull is shown without filtering out
  metastable candidates.
- Unified score order is accompanied by source-specific ranks. Cross-database
  numeric calibration is not claimed. Possible groups require equal reduced
  formula and space-group number, cell lengths within 2% and angles within 0.5°.
  These are **possible equivalents**, not proven structure matches. No identities,
  polymorphs or provenance are deleted; unrelated cells/space groups stay separate.
- Source page, local structure/CIF view and selected-CIF export are available.
  The exported CIF is the original DFT structure; search-scale correction is
  recorded separately and does not silently alter atomic coordinates.

## Sessions and compatibility

Existing COD/local-CIF sessions remain readable. New optional form fields have
backward-compatible defaults. `.mdss` stores only retained candidate arrays,
assignments, accepted-phase flags, chosen scale, scores, source ranks and database
snapshot/hash. It does not embed the full MP dataset or API keys. Saved MP sticks
remain visible without the original database; fetching structure information
requires the matching original installed index. Runtime checks changed file size
and mtime and requests Verify before searching a modified index.

## Licensing and publication boundary

The official access-control documentation identifies GNoME by batch ID. The
actual BY-C field additionally permits a positive inclusion rule. All BY-NC,
unknown and mixed/unverified records are excluded from this index implementation.
Standard MP data is attributed as CC BY 4.0; cite Jain et al. (2013),
DOI [10.1063/1.4812323](https://doi.org/10.1063/1.4812323), the accessed release,
and the AWS registry. The full terms page returned HTTP 403 in this environment;
this does not establish permission to redistribute bulk derived datasets.
Accordingly only builder/reader code and synthetic fixtures are published.
No MP database, structures, credentials or derived bulk index enter Git/EXE.
Public repackaging remains subject to review of current MP terms and any required
consultation with MP. User-contributed data is not assumed independently licensed:
it is included only when the selected official record explicitly carries BY-C.

## Sources

- [AWS data organization](https://docs.materialsproject.org/downloading-data/aws-opendata)
- [Public registry and buckets](https://registry.opendata.aws/materials-project/)
- [Supported Delta products](https://docs.materialsproject.org/materials-project-data-lakehouse/supported-data-products)
- [MPDataset / column projection](https://docs.materialsproject.org/materials-project-data-lakehouse/arrow-datasets)
- [GNoME filtering](https://docs.materialsproject.org/materials-project-data-lakehouse/access-controlled-data)
- [Official license schema](https://github.com/materialsproject/emmet/blob/main/emmet-core/emmet/core/base.py)
- [Official commercial/noncommercial mapping](https://github.com/materialsproject/emmet/blob/main/emmet-core/emmet/core/vasp/material.py)
- [Terms](https://materialsproject.org/about/terms)

Actual counts, performance, independent-standard validation, Windows tests and
remaining limitations are recorded in `validation_v0140.md` and `validation_v0141.md`.

## v0.14.1 distribution and search optimization

`compact_mp_index.py --binary` creates `mds-mp-reference-2`: lossless float64
byte shuffling, zlib, separate variable-integer hkl groups and compressed details.
Each converted record is decoded and compared with the original before commit.
The original index remains unchanged. Plain/v1 indexes remain readable.

```powershell
uv run python scripts/compact_mp_index.py C:/MP/mp.sqlite C:/MP/mp-distribution.sqlite --binary --workers 8
uv run python scripts/package_mp_index.py C:/MP/mp-distribution.sqlite C:/MP/MaterialsProject.zip
```

The ZIP contains the SQLite, checksum, manifest and notice. Extract it before
choosing **Import MP Index…**. The builder verifies the complete archive round trip.
It neither splits nor uploads files. This generated ZIP exceeds GitHub's 2 GiB
per-asset limit; no claim is made that it can be uploaded unchanged as one asset.

Existing installations gain a disposable hash-linked metadata index on first
search. Chemical SQL prefilters and batch reads preserve global d-vote weights
and final predicates. Prepared lattice scoring keeps the same 21-point grid,
local refinement, grouping and assignment formula. `scale_seconds` now records
the optimization separately from final ranking. Phase preview markers stay in
the search window; selection/settings no longer refresh the entire XRD workspace.

## v0.14.2 distribution and combined search

MP is searched alongside COD and User CIF, with one descending MDS Match Score ranking.
The default source is COD + MP + User CIF. The former fallback checkbox is removed.
`scripts/package_reference_distributions.py` creates two independently readable ZIP archives,
each below the GitHub asset limit. Both contain an identical manifest describing the exact
two part names, lengths, per-part SHA-256 and complete SQLite SHA-256, plus attribution.
Download both into one directory and select either ZIP in Import MP Index. MDS streams the
parts into a temporary database, checks all hashes and database contents, then registers it.
Legacy single ZIPs and direct SQLite registration remain supported. Data are not downsampled.

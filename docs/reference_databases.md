# Reference databases: install and licenses

[日本語](reference_databases_ja.md) | [Documentation](index.md)

Databases are optional and persist across application upgrades. Setup does not
contain these large datasets. Use **XRD → Open Phase Search–Match → Reference
Databases…**. The default **COD + MP + User CIF** combines installed sources.

## COD Inorganic

Download `COD-Inorganic-2026-07.zip` and choose **Install / Import ZIP…**.
MDS checks the four companion databases before registering them. Removing a
registration does not remove your original downloaded ZIP.

Source: Cuocci, Corrado; Moliterni, Anna; Altomare, Angela,
*QualX reference databases from the Crystallography Open Database (COD) for
powder diffraction phase identification*, version 2026-07, COD snapshot 2026-07-02,
[DOI 10.5281/zenodo.23297507](https://zenodo.org/records/23297507).
The processed QualX reference database is **CC BY 4.0**; original COD is CC0.
The MDS distribution adds bilingual notices without changing the database payload.
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Please retain credits and the license link when sharing. Cite QualX
[Altomare et al., 2015](https://doi.org/10.1107/S1600576715002319), COD
[Gražulis et al., 2009](https://doi.org/10.1107/S0021889809016690), and
[Vaitkus et al., 2021](https://doi.org/10.1107/S1600576721000418).
MDS uses its independent reader, not QualX program code. MDS Match Score is not
QualX FOM or a phase-presence probability.

## Materials Project

Download both `MaterialsProject-2026-09-28-part1-of2.zip` and
`MaterialsProject-2026-09-28-part2-of2.zip` to the same folder without renaming.
Use **Import MP Index…**, selecting either ZIP. MDS checks both manifests,
reconstructs the database and verifies its payload hash. Manual extraction or
concatenation is unnecessary. The two parts belong to one distribution.

Source: [Materials Project AWS Open Data](https://registry.opendata.aws/materials-project/),
summary Delta transaction 1, snapshot 2026-09-28. The distributed index contains
154,369 records whose original `builder_meta.license` is BY-C; BY-NC, unknown
licenses and the GNoME restricted batch are excluded. Data are attributed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). MDS derives diffraction
peaks and a lossless local index from the selected DFT-relaxed structures; it
adds no additional restriction to the data license.
Cite [Jain et al., 2013](https://doi.org/10.1063/1.4812323).

DFT-relaxed structures are not experimental confirmation. Intensities use the
pymatgen XRD model. See each ZIP's NOTICE and
[MP Terms of Use](https://materialsproject.org/about/terms). At this private
preparation stage, final review of the current Terms remains pending because
the automated retrieval returned HTTP 403. No new public redistribution is
approved by merely creating this private repository.

## User CIF and sharing

Import only data you are entitled to use and share. MDS being MIT licensed does
not grant rights to third-party CIFs, ICSD/ICDD/PDF databases or research sessions.
Keep the relevant source, license and change notices with exported references.

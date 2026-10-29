# Synthetic Igor Pro 10.03 references

Captured using Igor Pro 10.03 on Windows during the P0/P1 implementation session
(2026-10-22 JST). Inputs are generated arithmetic/sinusoidal waves only.
`manifest.json` contains SHA-256 hashes; these are independent native outputs,
not regenerated from the MDS implementation.

| Files | Native operation / input |
|---|---|
| coeff3/5/21.tsv | `Make/D/O/N=128 w; Resample/UP=2/N=n/WINF=window/COEF w`; n=3/None, 5/Hanning, 21/Hanning. Coefficients from native W_ResampleCoefs |
| coeff_down.tsv | native /DOWN=3, default N=21/Hanning coefficient output |
| coeff_mix.tsv | native /UP=3/DOWN=2/N=5, Hanning coefficient output |
| up/down/mix.tsv | `input[p]=sin(p*.31)+.2*cos(p*1.7)`, 128 double points; UP=2, DOWN=3, UP=3/DOWN=2/N=5 respectively |
| edge0..3.tsv | same input, /UP=2/N=5/E=0..3 |
| quartile0..3.tsv | `StatsQuantiles/ALL/Q/QM=i` on {1,2,3,4,5}; Q25,Q50,Q75 |
| note.txt | raw text bytes after `Note w,"first";Note w,"second"`: CR separator |
| labels.h5xp | dataset-only extraction from native H5XP; 5×3 values p+10q, dim0 name rowName, dim1 labels channelA/empty/channelC; note "native synthetic" |

The native session inherited Repeat edge handling for the omitted /E cases;
tests explicitly select E=3. MDS defaults to documented Zero (E=2), independent
of local Igor preferences. Explicit E fixtures determine the boundary contract.
Numeric TSV exports use native double-precision output. The H5XP fixture keeps
only the generated p1Labels dataset and its three native attributes, without
history, procedure source, original experiment paths or MDS editing metadata.
The temporary whole experiment is excluded from the repository. Saving that
one synthetic experiment through the Igor 9+ compatibility warning was expressly
authorized by the user; warning preferences were not changed.

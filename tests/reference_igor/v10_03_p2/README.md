# Igor Pro 10.03 P2 references

Collected on Windows on 2026-10-23, using newly generated waves only.
Igor GUI command line was used; no research procedure was opened or executed.
Native global procedure files are environment-owned and were not distributed.

## Numeric inputs

- a={1,2,3,4,5}; b={1,3,5,7,9}.
- StatsTTest /MEAN=2/CI, independent Welch without CI, and /PAIR.
  Result arrays and native dimension labels are exported with Save/J/O.
- The two-sample /CI output is retained as exploratory evidence only; its
  pooled CI profile is explicitly unsupported in MDS.
- Rotate input 0..4, offset=2, delta=.5, shift=7. Output offset=-1.5.
- Unwrap period=10 on {0,8,6,NaN,4,2,0}; correction persists across NaN.
- StatsKDE /H=1/KT=5/S={0,.5,6} on a: 12 points, upper bound exclusive.
- Threshold input im[p][q]=p+4*q (4x4), im[0][0]=NaN.
  ROI uint8 q>1; ImageThreshold/T=7/R={roi,1}. Output remains float64,
  including the selected NaN. Native blank TSV cells represent NaN.
  M=1 threshold=7.75; M=7 threshold=7.972549019607843. These observations
  exposed the iterative split and last-maximum Otsu tie conventions.

## Native H5XP reopen

build_mds_fixture.py generates the public synthetic experiment. mds_generated.h5xp
is the exact input successfully opened in Igor. mds_native_reopen.txt was written
by Igor fprintf directly from WinRecreation for three graphs and one table.
The input is an MDS-generated H5XP, not an Igor-authored numeric oracle.

Native GUI observation: MDSTable, MDSImage, MDSViolin and MDSNative all opened.
Readback confirms simultaneous asymmetric XY waves, line width 2/cap 3/blue,
attached Tag, cursor A at point 2, violin bandwidth 1/mean/median,
image Grays and ColorScale width12/height70%/nticks3/minor/font10,
table digits7/width90. No full-desktop screenshots or private history are included.

The first draft opened only one graph because initialization calls were
interleaved with definitions. Moving every call before the first Window fixes it.
Two consecutive ErrorBars X then Y replace the native specification; one XY
operation is required. TextBox-only Tag flags were removed from native output.

Native reopen covers the listed settings, not every option, Contour/Layout/inset,
locale-dependent date rendering, all KDE automatic bandwidth profiles or all GUI
styles. Numeric assertions use independent native arrays, not MDS round trips.

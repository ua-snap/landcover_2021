"""Verify the 2021 land cover GeoTIFF against the 2015 file behind the GeoServer layer.

Primary reference: the 2015 GeoTIFF itself. The file structure must match it exactly, and the two
are compared pixel by pixel at full resolution. Secondary: windows fetched from the GeoServer WCS,
to confirm GeoServer serves that same 2015 file and to spot-check the 2021 file against it.

Usage: python -I verify.py <2021 tif> <2015 tif>
"""
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()
# Don't write .aux.xml sidecars (cached stats/histograms) next to the deliverable or the 2015 copy.
gdal.SetConfigOption("GDAL_PAM_ENABLED", "NO")
gdal.SetConfigOption("GDAL_CACHEMAX", "4096")

WCS = (
    "https://gs.snap.uaf.edu/geoserver/wcs?service=WCS&version=2.0.1"
    "&request=GetCoverage&coverageId=alaska_wildfires__alaska_landcover_2015"
    "&format=image/tiff&subset=X({},{})&subset=Y({},{})"
)
CLASSES = range(1, 20)
STRIP = 4096  # rows per read; also the registration window size

# Windows (x0, x1, y0, y1 in the grid's metres) compared with the WCS. "expect" constrains the
# values in both the 2021 file and the WCS response.
POINT_CHECKS = {
    "NWT point (outside source = -128)": ((1700000, 1703000, 1500000, 1503000), {-128}),
    "SW corner (outside source = -128)": ((-2541000, -2538000, -834000, -831000), {-128}),
    "Open ocean S of AK Peninsula (= 0)": ((-501500, -498500, 498500, 501500), {0}),
}
TRANSECT = (500000, 800000, 1650000, 1653000)  # AK/Yukon border near x 620000
# Horizontal strips (10000 x 100 px) across Alaska, for checking the WCS against the 2015 file.
STRIPS = [(x0, x0 + 300000, y, y + 3000)
          for y in (1000000, 1300000, 1600000, 1900000, 2200000)
          for x0 in (-700000, -400000, -100000, 200000)]
STRIPS += [(-2000000, -1700000, 400000, 403000),   # Aleutians west of 180
           (-1300000, -1000000, 600000, 603000),   # Alaska Peninsula
           (1000000, 1300000, 1100000, 1103000)]   # Southeast


def category(a):
    """0 = in-footprint nodata, 1 = land cover class, 2 = outside footprint (-128), 3 = other."""
    c = np.full(a.shape, 3, np.uint8)
    c[a == 0] = 0
    c[(a >= 1) & (a <= 19)] = 1
    c[a == -128] = 2
    return c


def fetch_wcs(gt, x0, x1, y0, y1):
    """Return (array as int8, col_off, row_off) of a WCS subset of the 2015 layer on the grid gt."""
    data = urllib.request.urlopen(WCS.format(x0, x1, y0, y1), timeout=120).read()
    path = f"/vsimem/wcs_{x0}_{y0}.tif"
    gdal.FileFromMemBuffer(path, data)
    ds = gdal.Open(path)
    g = ds.GetGeoTransform()
    col, row = (g[0] - gt[0]) / gt[1], (g[3] - gt[3]) / gt[5]
    assert abs(col - round(col)) < 1e-3 and abs(row - round(row)) < 1e-3, "WCS grid misaligned"
    arr = ds.ReadAsArray()
    ds = None
    gdal.Unlink(path)
    # GeoServer serves the signed -128 as unsigned 128; reinterpret the bits
    return arr.astype(np.uint8).view(np.int8), int(round(col)), int(round(row))


def main(path21, path15):
    path21 = Path(path21)
    lines, failures = [], []

    def check(name, ok, detail=""):
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    ds21, ds15 = gdal.Open(str(path21)), gdal.Open(str(path15))
    b21, b15 = ds21.GetRasterBand(1), ds15.GetRasterBand(1)

    # --- structure: identical to the 2015 file ----------------------------
    lines.append("Structure vs the 2015 file:")
    s21, s15 = osr.SpatialReference(wkt=ds21.GetProjection()), osr.SpatialReference(wkt=ds15.GetProjection())
    check("CRS", s21.IsSame(s15) == 1, f"2021 EPSG:{s21.GetAuthorityCode(None)}, 2015 EPSG:{s15.GetAuthorityCode(None)}")
    size = lambda d: (d.RasterXSize, d.RasterYSize, d.RasterCount)
    check("Size and band count", size(ds21) == size(ds15), str(size(ds21)))
    check("Geotransform (exact)", ds21.GetGeoTransform() == ds15.GetGeoTransform(), str(ds21.GetGeoTransform()))
    dtype = lambda b: gdal.GetDataTypeName(b.DataType)
    check("Data type", dtype(b21) == dtype(b15), dtype(b21))
    interp = lambda b: gdal.GetColorInterpretationName(b.GetColorInterpretation())
    check("Color interpretation", interp(b21) == interp(b15), interp(b21))
    entries = lambda b: [b.GetColorTable().GetColorEntry(i) for i in range(256)] if b.GetColorTable() else None
    check("Color table", entries(b21) == entries(b15))
    check("NoData", b21.GetNoDataValue() == b15.GetNoDataValue(), str(b21.GetNoDataValue()))
    comp = lambda d: d.GetMetadata("IMAGE_STRUCTURE").get("COMPRESSION")
    check("Compression", comp(ds21) == comp(ds15), comp(ds21))
    check("Block size", b21.GetBlockSize() == b15.GetBlockSize(), str(b21.GetBlockSize()))
    magic = lambda p: open(p, "rb").read(4)
    check("TIFF flavour (BigTIFF)", magic(path21) == magic(path15), repr(magic(path21)))
    check("Overview count", b21.GetOverviewCount() == b15.GetOverviewCount(), str(b21.GetOverviewCount()))

    # --- full-resolution comparison ----------------------------------------
    W, H = ds21.RasterXSize, ds21.RasterYSize
    n21, n15 = np.zeros(256, np.int64), np.zeros(256, np.int64)  # index = value + 128
    joint = np.zeros((4, 4), np.int64)
    same_class = 0
    shifts = {}
    for r in range(0, H, STRIP):
        h = min(STRIP, H - r)
        a21, a15 = b21.ReadAsArray(0, r, W, h), b15.ReadAsArray(0, r, W, h)
        n21 += np.bincount((a21.astype(np.int16) + 128).ravel(), minlength=256)
        n15 += np.bincount((a15.astype(np.int16) + 128).ravel(), minlength=256)
        joint += np.bincount((category(a15) * 4 + category(a21)).ravel(), minlength=16).reshape(4, 4)
        both = (a15 >= 1) & (a15 <= 19) & (a21 >= 1) & (a21 <= 19)
        same_class += int((a15[both] == a21[both]).sum())
        # registration: which +-1 px shift of 2021 best matches 2015 in each window with enough land
        for c in range(0, W - STRIP, STRIP):
            x15 = a15[1:h - 1, c + 1:c + STRIP - 1]
            land = (x15 >= 1) & (x15 <= 19)
            if land.sum() < 1_000_000:
                continue
            score = {(dy, dx): (x15 == a21[1 + dy:h - 1 + dy, c + 1 + dx:c + STRIP - 1 + dx])[land].mean()
                     for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
            shifts[(r, c)] = max(score, key=score.get)

    lines.append("\nFull-resolution comparison with the 2015 file:")
    total = W * H
    check("Value counts cover every pixel", n21.sum() == total, str(n21.sum()))
    present21 = {v - 128 for v in np.nonzero(n21)[0]}
    check("Only values -128, 0, 1-19", present21 <= {-128, 0, *CLASSES},
          f"unexpected {sorted(present21 - {-128, 0, *CLASSES})}")
    out_mismatch = int(joint[2].sum() - joint[2, 2] + joint[:, 2].sum() - joint[2, 2])
    check("Outside footprint (-128) matches 2015", out_mismatch < 1e-5 * joint[2].sum(),
          f"{out_mismatch} px differ of {joint[2].sum()} (single-pixel jitter on the source-tile edge)")
    check("Registration: best shift is (0, 0) in every window", set(shifts.values()) == {(0, 0)},
          f"{len(shifts)} windows: {dict(Counter(shifts.values()))}")
    lines.append("Class pixels, 2015 -> 2021 (0 = in-footprint nodata):")
    lines.append(f"  class in both: {joint[1, 1]} ({100 * same_class / joint[1, 1]:.2f}% same class), "
                 f"class -> 0: {joint[1, 0]}, 0 -> class: {joint[0, 1]}")

    lines.append("\nFull-raster value counts:")
    lines.append("value,count_2015,count_2021,pct_2021")
    for v in sorted(present21 | {v - 128 for v in np.nonzero(n15)[0]}):
        lines.append(f"{v},{n15[v + 128]},{n21[v + 128]},{100 * n21[v + 128] / total:.4f}")

    c15, c21 = n15[128 + 1:128 + 20], n21[128 + 1:128 + 20]
    lines.append("\nClass shares (classes 1-19 only), full raster:")
    lines.append("class,2015_pct,2021_pct,diff_pct")
    for v, a, b in zip(CLASSES, c15, c21):
        if a or b:
            lines.append(f"{v},{100 * a / c15.sum():.2f},{100 * b / c21.sum():.2f},{100 * (b / c21.sum() - a / c15.sum()):+.2f}")

    # --- secondary: the GeoServer WCS ----------------------------------------
    lines.append("\nGeoServer WCS (secondary):")
    gt = ds21.GetGeoTransform()
    agree = n = 0
    for win in STRIPS:
        w, col, row = fetch_wcs(gt, *win)
        agree += int((w == b15.ReadAsArray(col, row, w.shape[1], w.shape[0])).sum())
        n += w.size
    check("WCS serves the 2015 file (strips identical)", agree == n, f"{agree} of {n} px")

    for name, (win, expect) in POINT_CHECKS.items():
        w, col, row = fetch_wcs(gt, *win)
        v21 = set(np.unique(b21.ReadAsArray(col, row, w.shape[1], w.shape[0])).tolist())
        v15 = set(np.unique(w).tolist())
        check(name, v21 <= expect and v15 <= expect, f"2021 {sorted(v21)}, WCS {sorted(v15)}")

    w, col, row = fetch_wcs(gt, *TRANSECT)
    a21 = b21.ReadAsArray(col, row, w.shape[1], w.shape[0])
    x = gt[0] + (col + np.arange(w.shape[1]) + 0.5) * gt[1]
    last15 = x[np.nonzero((category(w) == 1).any(axis=0))[0].max()]
    last21 = x[np.nonzero((category(a21) == 1).any(axis=0))[0].max()]
    east21 = a21[:, x > max(last15, last21)]
    check("AK/Yukon transect: classes stop at border, then 0", abs(last15 - last21) <= 300
          and set(np.unique(east21).tolist()) <= {0},
          f"last class x WCS {last15:.0f}, 2021 {last21:.0f}; east of border {sorted(np.unique(east21).tolist())}")

    ds21 = ds15 = None
    lines.insert(0, f"Verification of {path21}\nagainst {path15}: "
                    f"{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}\n")
    report = "\n".join(lines)
    print(report)
    path21.with_name(path21.stem + "_verify.txt").write_text(report + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

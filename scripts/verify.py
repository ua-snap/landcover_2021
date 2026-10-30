"""Verify the 2021 land cover GeoTIFF against the 2015 GeoServer layer.

Checks the file structure against the 2015 spec, counts every value in the full
raster, and compares windows of the output with the same windows fetched from
the 2015 layer over WCS. Writes a report and a quick-look PNG next to the output.

Usage: python -I verify.py <output tif>
"""
import csv
import sys
import urllib.request
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()
# Don't write .aux.xml sidecars (cached stats/histograms) next to the deliverable.
gdal.SetConfigOption("GDAL_PAM_ENABLED", "NO")

WCS = (
    "https://gs.snap.uaf.edu/geoserver/wcs?service=WCS&version=2.0.1"
    "&request=GetCoverage&coverageId=alaska_wildfires__alaska_landcover_2015"
    "&format=image/tiff&subset=X({},{})&subset=Y({},{})"
)
SIZE = (144162, 126857)
GT = (-2542313.2452769727, 30.0, 0.0, 2969617.768867274, 0.0, -30.0)
CSV = Path(__file__).with_name("colortable_2015.csv")

# Windows (EPSG:3338 x0, x1, y0, y1) compared with 2015. "expect" constrains
# the 2021 values; every window is also compared pixel-by-pixel against 2015.
POINT_CHECKS = {
    "NWT point (outside source = 128)": ((1700000, 1703000, 1500000, 1503000), {128}),
    "SW corner (outside source = 128)": ((-2541000, -2538000, -834000, -831000), {128}),
    "Open ocean S of AK Peninsula (= 0)": ((-501500, -498500, 498500, 501500), {0}),
}
TRANSECT = (500000, 800000, 1650000, 1653000)  # AK/Yukon border near x 620000
# Horizontal strips (10000 x 100 px) across Alaska for the class-share table.
STRIPS = [(x0, x0 + 300000, y, y + 3000)
          for y in (1000000, 1300000, 1600000, 1900000, 2200000)
          for x0 in (-700000, -400000, -100000, 200000)]
STRIPS += [(-2000000, -1700000, 400000, 403000),   # Aleutians west of 180
           (-1300000, -1000000, 600000, 603000),   # Alaska Peninsula
           (1000000, 1300000, 1100000, 1103000)]   # Southeast


def category(a):
    """0 = in-footprint nodata, 1 = land cover class, 2 = outside footprint (128), 3 = other."""
    c = np.full(a.shape, 3, np.uint8)
    c[a == 0] = 0
    c[(a >= 1) & (a <= 19)] = 1
    c[a == 128] = 2
    return c


def fetch_2015(x0, x1, y0, y1):
    """Return (array, col_off, row_off) of a 2015 WCS subset on the output grid."""
    data = urllib.request.urlopen(WCS.format(x0, x1, y0, y1), timeout=120).read()
    path = f"/vsimem/wcs_{x0}_{y0}.tif"
    gdal.FileFromMemBuffer(path, data)
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    col = (gt[0] - GT[0]) / GT[1]
    row = (gt[3] - GT[3]) / GT[5]
    assert abs(col - round(col)) < 1e-3 and abs(row - round(row)) < 1e-3, "WCS grid misaligned"
    arr = ds.ReadAsArray()
    ds = None
    gdal.Unlink(path)
    return arr, int(round(col)), int(round(row))


def main(out_path):
    out_path = Path(out_path)
    lines, failures = [], []

    def check(name, ok, detail=""):
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    ds = gdal.Open(str(out_path))
    band = ds.GetRasterBand(1)

    # --- structure -------------------------------------------------------
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    srs.AutoIdentifyEPSG()
    check("CRS EPSG:3338", srs.GetAuthorityCode(None) == "3338", srs.GetAuthorityCode(None))
    check("Size 144162 x 126857", (ds.RasterXSize, ds.RasterYSize) == SIZE,
          f"{ds.RasterXSize} x {ds.RasterYSize}")
    check("Geotransform", np.allclose(ds.GetGeoTransform(), GT, rtol=0, atol=1e-6),
          str(ds.GetGeoTransform()))
    check("Byte, 1 band", ds.RasterCount == 1 and band.DataType == gdal.GDT_Byte)
    check("ColorInterp=Palette", band.GetColorInterpretation() == gdal.GCI_PaletteIndex)

    expected_ct = {i: (0, 0, 0, 255) for i in range(256)}
    with open(CSV) as f:
        for r in csv.DictReader(f):
            expected_ct[int(r["value"])] = (int(r["r"]), int(r["g"]), int(r["b"]), 255)
    ct = band.GetColorTable()
    got_ct = {i: ct.GetColorEntry(i) for i in range(ct.GetCount())} if ct else {}
    check("Color table identical to 2015", got_ct == expected_ct)

    check("NoData tag -128", band.GetNoDataValue() == -128, str(band.GetNoDataValue()))
    structure = ds.GetMetadata("IMAGE_STRUCTURE")
    check("DEFLATE", structure.get("COMPRESSION") == "DEFLATE", str(structure))
    check("Tiled 512x512", band.GetBlockSize() == [512, 512], str(band.GetBlockSize()))
    with open(out_path, "rb") as f:
        magic = f.read(4)
    check("BigTIFF", magic in (b"II+\x00", b"MM\x00+"), repr(magic))
    check("Internal overviews (7)", band.GetOverviewCount() == 7
          and not Path(str(out_path) + ".ovr").exists(), str(band.GetOverviewCount()))

    # --- values present (full resolution) --------------------------------
    # Counted with numpy: GDAL's GetHistogram casts the -128 tag to Byte (128) and skips most 128 pixels.
    hist = np.zeros(256, np.int64)
    for row in range(0, ds.RasterYSize, 512):
        strip = band.ReadAsArray(0, row, ds.RasterXSize, min(512, ds.RasterYSize - row))
        hist += np.bincount(strip.ravel(), minlength=256)
    check("Value counts cover every pixel", hist.sum() == ds.RasterXSize * ds.RasterYSize, str(hist.sum()))
    present = {v: int(n) for v, n in enumerate(hist) if n}
    allowed = {0, 128, *range(1, 20)}
    check("Only values 0, 1-19, 128", set(present) <= allowed,
          f"unexpected {sorted(set(present) - allowed)}")
    total = hist.sum()
    lines.append("\nFull-raster value counts:")
    lines.append("value,count,percent")
    lines += [f"{v},{n},{100 * n / total:.4f}" for v, n in present.items()]
    lines.append("")

    def read_2021(col, row, shape):
        return band.ReadAsArray(col, row, shape[1], shape[0])

    # --- point / footprint checks vs 2015 --------------------------------
    for name, (win, expect) in POINT_CHECKS.items():
        a15, col, row = fetch_2015(*win)
        a21 = read_2021(col, row, a15.shape)
        vals21 = set(np.unique(a21).tolist())
        vals15 = set(np.unique(a15).tolist())
        check(name, vals21 <= expect and vals15 <= expect, f"2021 {sorted(vals21)}, 2015 {sorted(vals15)}")

    a15, col, row = fetch_2015(*TRANSECT)
    a21 = read_2021(col, row, a15.shape)
    x = GT[0] + (col + np.arange(a15.shape[1]) + 0.5) * GT[1]
    last15 = x[np.nonzero((category(a15) == 1).any(axis=0))[0].max()]
    last21 = x[np.nonzero((category(a21) == 1).any(axis=0))[0].max()]
    east21 = a21[:, x > max(last15, last21)]
    check("AK/Yukon transect: classes stop at border, then 0", abs(last15 - last21) <= 300
          and set(np.unique(east21).tolist()) <= {0},
          f"last class x 2015 {last15:.0f}, 2021 {last21:.0f}; east of border {sorted(np.unique(east21).tolist())}")

    # --- no CONUS: no classes south of 50N (quick look at overview level) --
    ovr = band.GetOverview(band.GetOverviewCount() - 1)
    small = ovr.ReadAsArray()
    oy, ox = np.mgrid[0:ovr.YSize, 0:ovr.XSize]
    px = GT[0] + (ox + 0.5) * ds.RasterXSize / ovr.XSize * GT[1]
    py = GT[3] + (oy + 0.5) * ds.RasterYSize / ovr.YSize * GT[5]
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(srs, wgs)
    lonlat = np.array(tr.TransformPoints(np.c_[px.ravel(), py.ravel()]))
    lat = lonlat[:, 1].reshape(small.shape)
    n_south = int(((small >= 1) & (small <= 19) & (lat < 50)).sum())
    check("No CONUS (no classes south of 50N, coarsest overview)", n_south == 0, f"{n_south} pixels")

    # --- 2015 vs 2021 on matching strips ---------------------------------
    c15 = np.zeros(256, np.int64)
    c21 = np.zeros(256, np.int64)
    agree = n = 0
    for win in STRIPS:
        a15, col, row = fetch_2015(*win)
        a21 = read_2021(col, row, a15.shape)
        agree += int((category(a15) == category(a21)).sum())
        n += a15.size
        c15 += np.bincount(a15.ravel(), minlength=256)
        c21 += np.bincount(a21.ravel(), minlength=256)
    check("Footprint (0 / class / 128) agrees with 2015 on strips", agree / n > 0.999,
          f"{100 * agree / n:.4f}% of {n} px")
    lines.append(f"\nClass shares on {len(STRIPS)} strips ({n} px), classes 1-19 only:")
    lines.append("class,2015_pct,2021_pct,diff_pct")
    s15, s21 = c15[1:20].sum(), c21[1:20].sum()
    for v in range(1, 20):
        if c15[v] or c21[v]:
            p15, p21 = 100 * c15[v] / s15, 100 * c21[v] / s21
            lines.append(f"{v},{p15:.2f},{p21:.2f},{p21 - p15:+.2f}")
    lines.append("")

    # --- quick-look PNG ---------------------------------------------------
    png = out_path.with_name(out_path.stem + "_quicklook.png")
    gdal.Translate(str(png), ds, format="PNG", width=ds.RasterXSize // 50, height=0,
                   resampleAlg="nearest", noData="none")
    lines.append(f"Quick-look PNG: {png}")

    ds = None
    lines.insert(0, f"Verification of {out_path}: {'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}\n")
    report = "\n".join(lines)
    print(report)
    out_path.with_name(out_path.stem + "_verify.txt").write_text(report + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

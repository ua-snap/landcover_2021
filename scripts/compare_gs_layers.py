"""Check that the 2021 GeoServer layer behaves like the 2015 one, request for request.

Both layers are queried the way their clients use them, and each response is checked against
the GeoTIFF the layer is supposed to serve:
  - WCS DescribeCoverage: same grid, CRS parameters, band, nodata
  - WCS GetCoverage: full-resolution strips identical to the source file
  - WMS GetFeatureInfo, with the data API's exact URL (data-api routes/fire.py): same JSON shape,
    and PALETTE_INDEX equals the source pixel, including pixels that changed 2015 -> 2021
  - WMS GetMap with each layer's default style (and the 2021 layer with the 2015 style):
    native-resolution tiles decoded and compared with the 2015 SLD colors (classes opaque,
    0 and -128 transparent)

Usage: python -I compare_gs_layers.py <2021 tif> <2015 tif> <compare_k50_full.npz>
"""
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()
gdal.SetConfigOption("GDAL_PAM_ENABLED", "NO")

GS = "https://gs.snap.uaf.edu/geoserver/alaska_wildfires"
LAYERS = {"2015": "alaska_landcover_2015", "2021": "alaska_landcover_2021"}
# data-api generate_base_wms_url(), with bbox_offset = 1e-9
GFI = (GS + "/wms?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetFeatureInfo&FORMAT=image/jpeg&TRANSPARENT=true"
       "&QUERY_LAYERS=alaska_wildfires:{0}&STYLES&LAYERS=alaska_wildfires:{0}"
       "&exceptions=application/vnd.ogc.se_inimage&INFO_FORMAT=application/json&FEATURE_COUNT=50"
       "&X=1&Y=1&SRS=EPSG:4326&WIDTH=1&HEIGHT=1&BBOX={1},{2},{3},{4}")
GETMAP = (GS + "/wms?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=alaska_wildfires:{0}&STYLES={1}"
          "&SRS=EPSG:3338&BBOX={2},{3},{4},{5}&WIDTH=512&HEIGHT=512&FORMAT=image/png&TRANSPARENT=true")
WCS = (GS + "/wcs?service=WCS&version=2.0.1&request=GetCoverage&coverageId=alaska_wildfires__{0}"
       "&format=image/tiff&subset=X({1},{2})&subset=Y({3},{4})")
DESCRIBE = GS + "/wcs?service=WCS&version=2.0.1&request=DescribeCoverage&coverageId=alaska_wildfires__{0}"
# GetMap tiles, as (col, row) of the top-left pixel; each tile is 512 x 512 native pixels
TILES = {"Cook Inlet coast (0 + classes)": (60000, 80000),
         "Interior, Fairbanks": (94400, 43200),
         "AK/Yukon border (classes -> 0)": (104700, 43800),
         "Footprint corner (0 / -128)": (139000, 30000)}
N_RANDOM, N_CHANGED = 200, 100


def get(url):
    return urllib.request.urlopen(url, timeout=120).read()


def main(path21, path15, cache):
    rng = np.random.default_rng(0)
    ds = {"2021": gdal.Open(path21), "2015": gdal.Open(path15)}
    band = {k: d.GetRasterBand(1) for k, d in ds.items()}
    gt = ds["2021"].GetGeoTransform()
    pixel = lambda k, c, r: int(band[k].ReadAsArray(c, r, 1, 1)[0, 0])
    lines, failures = [], []

    def check(name, ok, detail=""):
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    # --- WCS DescribeCoverage ------------------------------------------------
    lines.append("WCS DescribeCoverage:")
    desc = {}
    for k, lyr in LAYERS.items():
        x = get(DESCRIBE.format(lyr)).decode()
        x = x.replace(f"alaska_wildfires__{lyr}", "LAYER")
        x = re.sub(r"<gml:name>.*?</gml:name>|<ows:Keyword>land_cover_20\d\d_albers</ows:Keyword>", "", x)
        desc[k] = x
    crs = {k: sorted(set(re.findall(r"EPSG/0/(\d+)", x))) for k, x in desc.items()}
    lines.append(f"  declared CRS: 2015 EPSG:{crs['2015']}, 2021 EPSG:{crs['2021']}")
    same = desc["2015"] == desc["2021"].replace("EPSG/0/6393", "EPSG/0/3338")
    check("Grid, extent, offsets, band and nodata identical (ignoring 3338/6393 label)", same)

    # --- WCS GetCoverage -----------------------------------------------------
    lines.append("\nWCS GetCoverage (full resolution):")
    for k, lyr in LAYERS.items():
        agree = n = 0
        for y in (1000000, 1600000, 2200000):
            for x0 in (-700000, -100000, 500000):
                path = f"/vsimem/wcs_{k}_{x0}_{y}.tif"
                gdal.FileFromMemBuffer(path, get(WCS.format(lyr, x0, x0 + 300000, y, y + 3000)))
                w = gdal.Open(path)
                g = w.GetGeoTransform()
                c, r = round((g[0] - gt[0]) / gt[1]), round((g[3] - gt[3]) / gt[5])
                a = w.ReadAsArray().astype(np.uint8).view(np.int8)  # served as unsigned
                w = None
                gdal.Unlink(path)
                agree += int((a == band[k].ReadAsArray(c, r, a.shape[1], a.shape[0])).sum())
                n += a.size
        check(f"{k} layer serves its file unchanged", agree == n, f"{agree} of {n} px")

    # --- WMS GetFeatureInfo, as the data API calls it ------------------------
    lines.append("\nWMS GetFeatureInfo (data API request):")
    z = np.load(cache)
    K = int(round(ds["2021"].RasterXSize / z["land"].shape[1]))
    pts = []
    land = np.argwhere(z["land"] > 0)
    for i, j in land[rng.choice(len(land), N_RANDOM, replace=False)]:
        pts.append(("random", int(j * K + rng.integers(K)), int(i * K + rng.integers(K))))
    hot = np.argwhere(z["changed"] > 0)
    for i, j in hot[rng.choice(len(hot), N_CHANGED, replace=False)]:
        a21 = band["2021"].ReadAsArray(int(j * K), int(i * K), K, K)
        a15 = band["2015"].ReadAsArray(int(j * K), int(i * K), K, K)
        r, c = np.argwhere(a21 != a15)[0]
        pts.append(("changed", int(j * K + c), int(i * K + r)))
    for name, (x, y) in {"ocean": (-500000, 500000), "Yukon": (700000, 1650000),
                         "outside (NWT)": (1701500, 1501500)}.items():
        pts.append((name, int((x - gt[0]) / gt[1]), int((y - gt[3]) / gt[5])))

    src = osr.SpatialReference()
    src.ImportFromWkt(ds["2021"].GetProjection())
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(4326)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_ll = osr.CoordinateTransformation(src, dst)

    def query(args):
        k, (kind, c, r) = args
        lon, lat, _ = to_ll.TransformPoint(gt[0] + (c + 0.5) * gt[1], gt[3] + (r + 0.5) * gt[5])
        resp = json.loads(get(GFI.format(LAYERS[k], lon, lat, lon + 1e-9, lat + 1e-9)))
        return k, kind, c, r, resp

    jobs = [(k, p) for k in LAYERS for p in pts]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(query, jobs))
    shape = {k: set() for k in LAYERS}
    by_kind = {}
    for k, kind, c, r, resp in results:
        shape[k].add(json.dumps({kk: sorted(v[0]["properties"]) if kk == "features" and v else type(v).__name__
                                 for kk, v in resp.items() if kk != "timeStamp"}, sort_keys=True))
        got = resp["features"][0]["properties"]["PALETTE_INDEX"] if resp["features"] else None
        want = pixel(k, c, r) & 0xFF  # GeoServer serves -128 as unsigned 128, as it does over WCS
        by_kind.setdefault((k, kind), []).append((got, want))
    check("Same JSON shape from both layers", shape["2015"] == shape["2021"] and len(shape["2015"]) == 1,
          "; ".join(sorted(shape["2015"] | shape["2021"])))
    for k in LAYERS:
        for kind in dict.fromkeys(p[0] for p in pts):
            pairs = by_kind[(k, kind)]
            ok = sum(g == w for g, w in pairs)
            detail = f"{ok} of {len(pairs)} match the file"
            if len(pairs) == 1:
                detail = f"PALETTE_INDEX {pairs[0][0]}, file {pairs[0][1]}"
            check(f"{k} {kind}", ok == len(pairs), detail)
    changed_21 = [g for g, _ in by_kind[("2021", "changed")]]
    changed_15 = [g for g, _ in by_kind[("2015", "changed")]]
    check("Changed pixels return different classes from the two layers",
          all(a != b for a, b in zip(changed_15, changed_21)), f"{len(changed_21)} points")

    # --- WMS GetMap, default styles --------------------------------------------
    lines.append("\nWMS GetMap, native-resolution tiles (vs 2015 SLD colors):")
    sld = get(GS + "/wms?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetStyles&LAYERS=alaska_wildfires:alaska_landcover_2015").decode()
    colors = {int(q): tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
              for h, q in re.findall(r'ColorMapEntry color="#(\w{6})" quantity="(\d+)"', sld)}
    caps = get(GS + "/wms?service=WMS&version=1.1.1&request=GetCapabilities").decode()
    style = {k: re.search(rf"<Name>{lyr}</Name>.*?<Style>\s*<Name>([^<]+)", caps, re.S).group(1)
             for k, lyr in LAYERS.items()}
    lines.append(f"  default style: 2015 '{style['2015']}', 2021 '{style['2021']}'")
    for k, st in [("2015", ""), ("2021", ""), ("2021", "alaska_landcover")]:
        for name, (c, r) in TILES.items():
            x0, y1 = gt[0] + c * gt[1], gt[3] + r * gt[5]
            path = f"/vsimem/map_{k}_{st}_{c}.png"
            gdal.FileFromMemBuffer(path, get(GETMAP.format(LAYERS[k], st, x0, y1 - 512 * 30, x0 + 512 * 30, y1)))
            m = gdal.Open(path)
            if m.GetRasterBand(1).GetColorTable():
                ct = m.GetRasterBand(1).GetColorTable()
                lut = np.array([ct.GetColorEntry(i) for i in range(ct.GetCount())], np.uint8)
                rgba = lut[m.ReadAsArray()]
            else:
                rgba = np.dstack([m.GetRasterBand(b + 1).ReadAsArray() for b in range(m.RasterCount)])
            m = None
            gdal.Unlink(path)
            a = band[k].ReadAsArray(c, r, 512, 512)
            cls = np.isin(a, list(colors))
            want = np.zeros((512, 512, 3), np.uint8)
            for v, rgb in colors.items():
                want[a == v] = rgb
            opaque = rgba[..., 3] > 0
            ok_alpha = (opaque == cls).mean()
            ok_rgb = (rgba[..., :3][cls] == want[cls]).all(axis=1).mean() if cls.any() else 1.0
            vals = ", ".join(f"{v}: {tuple(int(x) for x in rgba[a == v][0])}" for v in (0, -128) if (a == v).any())
            check(f"{k} ({st or 'default'} style) {name}", ok_alpha == 1 and ok_rgb == 1,
                  f"{100 * ok_rgb:.2f}% class colors match, {100 * ok_alpha:.2f}% transparency matches"
                  + (f"; drawn as {vals}" if vals else ""))

    lines.insert(0, f"GeoServer layer comparison ({GS}): "
                    f"{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}\n")
    print("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))

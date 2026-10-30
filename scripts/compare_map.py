"""Map 2015 vs 2021 land cover and where they differ, for the README.

Compares the 2015 file behind the GeoServer layer with the 2021 output at full 30 m resolution.
The 2015 and 2021 panels show the top-left pixel of every 50 x 50 block (1.5 km); the difference
panel shows the share of land pixels (a class in either year) whose class differs, per block.
Scattered single-pixel changes put almost every block above 0%, so blocks under 5% are gray.

Usage: python -I compare_map.py <2021 tif> <2015 tif> <png>
"""
import csv
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()
gdal.SetConfigOption("GDAL_PAM_ENABLED", "NO")
gdal.SetConfigOption("GDAL_CACHEMAX", "2048")

K = 50        # block size in pixels
ROWS = 20     # blocks per read
CSV = Path(__file__).with_name("colortable_2015.csv")
NAMES = {
    1: "Temperate/subpolar needleleaf forest", 2: "Subpolar taiga needleleaf forest",
    5: "Temperate/subpolar broadleaf deciduous forest", 6: "Mixed forest",
    8: "Temperate/subpolar shrubland", 10: "Temperate/subpolar grassland",
    11: "Subpolar/polar shrubland-lichen-moss", 12: "Subpolar/polar grassland-lichen-moss",
    13: "Subpolar/polar barren-lichen-moss", 14: "Wetland", 15: "Cropland", 16: "Barren land",
    17: "Urban and built-up", 18: "Water", 19: "Snow and ice",
}
BINS = [0.05, 0.25, 0.5, 1.0]  # share of land pixels changed per block; below the first is shown as gray


def is_class(a):
    return (a >= 1) & (a <= 19)


def summarize(path21, path15):
    """Per K x K block: top-left pixel of each year, land pixel count, changed pixel count."""
    ds21, ds15 = gdal.Open(path21), gdal.Open(path15)
    b21, b15 = ds21.GetRasterBand(1), ds15.GetRasterBand(1)
    ny, nx = ds21.RasterYSize // K, ds21.RasterXSize // K
    s21, s15 = np.zeros((ny, nx), np.int8), np.zeros((ny, nx), np.int8)
    land, changed = np.zeros((ny, nx), np.int32), np.zeros((ny, nx), np.int32)
    for i in range(0, ny, ROWS):
        n = min(ROWS, ny - i)
        a21 = b21.ReadAsArray(0, i * K, nx * K, n * K).reshape(n, K, nx, K)
        a15 = b15.ReadAsArray(0, i * K, nx * K, n * K).reshape(n, K, nx, K)
        s21[i:i + n], s15[i:i + n] = a21[:, 0, :, 0], a15[:, 0, :, 0]
        m = is_class(a21) | is_class(a15)
        land[i:i + n] = m.sum(axis=(1, 3))
        changed[i:i + n] = (m & (a21 != a15)).sum(axis=(1, 3))
    return s15, s21, land, changed


def plot(s15, s21, land, changed, png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    pct = 100 * changed.sum() / land.sum()

    # crop to the land cover plus a margin
    rows, cols = np.nonzero(land)
    sl = (slice(max(rows.min() - 20, 0), rows.max() + 20), slice(max(cols.min() - 20, 0), cols.max() + 20))
    s15, s21, land, changed = s15[sl], s21[sl], land[sl], changed[sl]

    bg, unchanged = "#f4f4f2", "#d4d4d0"
    ramp = ["#f768a1", "#c51b8a", "#7a0177"]  # one hue, light -> dark
    colors = np.tile(np.array([0xf4, 0xf4, 0xf2]) / 255, (256, 1))  # 0 / -128 / unused -> background
    with open(CSV) as f:
        for row in csv.DictReader(f):
            colors[int(row["value"])] = [int(row[k]) / 255 for k in "rgb"]
    cmap = ListedColormap(colors)

    # size each panel to the crop; sum the counts over f x f blocks so the difference panel has
    # about one cell per screen pixel and no changed block is dropped by downsampling
    dpi, panel_w = 150, 6.8
    h, w = land.shape
    panel_h = panel_w * h / w
    f = int(np.ceil(w / (panel_w * dpi)))
    pool = lambda a: np.pad(a, ((0, -h % f), (0, -w % f))).reshape(-(-h // f), f, -(-w // f), f).sum(axis=(1, 3))
    pl, pc = pool(land), pool(changed)
    # -1 = no land, otherwise the share of land pixels changed
    diff = np.full(pl.shape, -1.0)
    diff[pl > 0] = pc[pl > 0] / pl[pl > 0]
    diff_cmap = ListedColormap([bg, unchanged] + ramp)
    diff_norm = BoundaryNorm([-1.5, -0.5] + BINS, diff_cmap.N)

    fig, axes = plt.subplots(2, 2, figsize=(2 * panel_w + 0.4, 2 * panel_h + 1.0), facecolor="white")
    kw = dict(interpolation="nearest")
    axes[0, 0].imshow(s15.view(np.uint8), cmap=cmap, vmin=0, vmax=255, **kw)
    axes[0, 0].set_title("2015 (current GeoServer layer)", loc="left")
    axes[0, 1].imshow(s21.view(np.uint8), cmap=cmap, vmin=0, vmax=255, **kw)
    axes[0, 1].set_title("2021 (NALCMS 2020v2, this repo)", loc="left")
    axes[1, 0].imshow(diff, cmap=diff_cmap, norm=diff_norm, **kw)
    axes[1, 0].set_title(f"Different class in 2021: {pct:.1f}% of land pixels", loc="left")
    for ax in axes.flat:
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)

    present = sorted(v for v in NAMES if (s15 == v).any() or (s21 == v).any())
    blank = Patch(facecolor="none", edgecolor="none", label="")
    handles = [Patch(facecolor=colors[v], label=f"{v}  {NAMES[v]}") for v in present]
    handles += [blank, Patch(facecolor=unchanged, label=f"Under {100 * BINS[0]:.0f}% of land pixels changed")]
    handles += [Patch(facecolor=c, label=f"{100 * lo:.0f}–{100 * hi:.0f}% of land pixels changed")
                for c, lo, hi in zip(ramp, BINS[:-1], BINS[1:])]
    axes[1, 1].legend(handles=handles, loc="center left", frameon=False, fontsize=10, title="Land cover class (NALCMS)",
                      title_fontsize=11, alignment="left")
    fig.text(0.01, 0.005, f"Top panels: one 30 m pixel per {K * 30 / 1000:.1f} km block. Difference panel: full 30 m comparison of the "
             f"2015 GeoServer file and the 2021 output, summarized over {f * K * 30 / 1000:.1f} km blocks.\n"
             "Land = a class in either year; 0 ↔ class switches (mostly coastline) count as changed.",
             fontsize=9, color="#555")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(png, dpi=dpi)
    return pct


def main(path21, path15, png):
    cache = Path(path21).parent.parent / "work" / f"compare_k{K}_full.npz"
    if not cache.exists():
        np.savez_compressed(cache, **dict(zip(("s15", "s21", "land", "changed"), summarize(path21, path15))))
    z = np.load(cache)
    pct = plot(z["s15"], z["s21"], z["land"], z["changed"], png)
    print(f"{png}: {pct:.2f}% of land pixels differ (full resolution)")


if __name__ == "__main__":
    main(*sys.argv[1:4])

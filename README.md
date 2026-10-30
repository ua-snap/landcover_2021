# landcover_2021

Drop-in replacement for the GeoServer layer `alaska_wildfires:alaska_landcover_2015`, built from the
CEC **NALCMS 2020 v2** 30 m land cover. The Alaska part of that release uses 2021 imagery (CONUS is 2019, Canada and Mexico
are 2020), so the result is named `land_cover_2021_albers.tif`. Only the data changes. The grid, encoding,
nodata conventions and color table all match the 2015 file, so the layer's store can point at the new file with no
style or layer changes.

Data lives outside the repo, in `/import/beegfs/CMIP6/jdpaul3/landcover_2021_data/{raw,work,output,logs}`.

## Source

- `https://www.cec.org/files/atlas_layers/1_terrestrial_ecosystems/1_01_0_land_cover_2020_30m/usa_land_cover_2020v2_30m_tif.zip`
  (1,688,931,815 bytes, sha256 `f8308b40a2b0ced8fda2759f5b4066c4e8d80bf23dcf90b99da16e8557fa1508`), downloaded 2026-10-30
- `metadata.zip` from the same directory (69,371 bytes, sha256 `6f10971d076d4661bcc4ded89a3026907ae0345fb8bd652c09152fc519186a40`)
- File used: `ASK_NALCMS_landcover_2020v2_30m.tif`, the Alaska tile, which is separate from CONUS. Its CRS is LAEA (WGS84, lat 45 / lon -100),
  it is 30 m Byte with an LZW-compressed palette, and its nodata tag is 127 (no pixels actually hold 127). Ocean, Canada and Russia inside the tile are 0.
  Classes present: 0, 1, 2, 5, 6, 8, 10, 11, 12, 14–19. The codes use the standard NALCMS 19-class legend, the same as 2015.

## Target: the 2015 layer

Read from the GeoServer WCS (DescribeCoverage plus GetCoverage subsets), because no local copy of the 2015 file was available:

| | |
|---|---|
| CRS | EPSG:3338 |
| Grid | 144162 × 126857 px at 30 m; extent `-2542313.2452769727 -836092.2311327262 1782546.7547230273 2969617.768867274` (not integer-aligned) |
| Type | Byte, 1 band, paletted (color table in `scripts/colortable_2015.csv`) |
| Values | 1–19 classes, **0** = no data inside the source tile (ocean/Canada), **128** = outside the source tile |
| NoData tag | **-128** (out of range for Byte, so in practice no value is masked) |

## Processing (`scripts/`)

1. `download.sh`: downloads with curl, checks byte counts, writes sha256 and unzips.
2. `warp.slurm` (Slurm, t1small, 24 cores): runs `gdalwarp` to EPSG:3338 on the exact 2015 grid (`-te` as above, `-tr 30 30`,
   no `-tap`), with nearest neighbour, `-srcnodata 127 -dstnodata 128`, tiled 512, DEFLATE and BigTIFF. Source 0 stays 0, and everything
   outside the source tile becomes 128. That reproduces the 2015 0/128 layout to within about 1 px along the tile edge.
   The job then runs steps 3 and 4.
3. `finalize.sh` runs in this order:
   1. Copies the warped file to `output/`.
   2. Writes the 2015 color table (`set_colortable.py`), which is byte-identical to the color table from WCS.
   3. Builds internal overviews (nearest, 2–128).
   4. Last: `gdal_edit.py -a_nodata -128`. This has to be the final step, because `gdalwarp`/`gdal_translate` silently clamp -128 to 0 on Byte.
4. `verify.py`: checks the structure and value set, and compares 2021 windows with the same windows fetched from the 2015 WCS. It writes
   `output/land_cover_2021_albers_verify.txt` and a quick-look PNG.

Environment: the conda env `snap-geo` on Chinook (GDAL 3.7.0). The scripts activate it, and `PROJ_DATA` is only set once it is active.

## Verification

Run 2026-10-30, Slurm job 738172 (warp + finalize took about 2.5 min). Every check passed. Full report: `output/land_cover_2021_albers_verify.txt`.

- The grid, CRS, type, color table, nodata tag (-128), tiling, DEFLATE, BigTIFF and 7 internal overviews all match the 2015 spec.
- The only values present are 0 (41.2%), 1–19 (9.2%) and 128 (49.6%).
- The 0/128 footprint matches 2015 at all sampled points. On 23 matching 10000 × 100 px strips, 99.9945% of pixels fall in the
  same category (0 / class / 128). At full resolution, the edges of the source tile line up with 2015 to within 0–3 px per 500k-px window.
- AK/Yukon border transect: the last class pixel is at x 625492 in 2021 and x 625522 in 2015. East of the border is 0 in both.
- There are no CONUS pixels, because the Alaska tile is a separate file.
- Class shares on the strips, 2015 → 2021, all changed by less than 1 percentage point. The largest changes are needleleaf forest (1) at −0.88 and
  shrubland (8) at +0.56. No class codes are mismatched.

Note: GDAL's `GetHistogram` undercounts 128 on this file, because it casts the -128 tag to Byte, so `verify.py` counts values with numpy.
The 2015 file most likely has the same quirk.

## Notes

- rasterio reports the `-128` nodata as `None`. That is expected.
- The grid crosses the antimeridian (the western Aleutians). The data are only warped from one projected CRS to another and never pass through EPSG:4326.
- Out of scope: the GeoServer swap itself, and updating the layer's title or abstract to say the data are 2021 (NALCMS 2020v2).

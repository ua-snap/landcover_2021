#!/bin/bash
# Turn the warped intermediate into the final drop-in GeoTIFF, laid out like the 2015 file:
# signed 8-bit (Int8), palette, nodata -128, LZW, 128x128 tiles, BigTIFF, no overviews.
# GDAL only writes a palette on signed 8-bit data through gdal_translate with the deprecated
# PIXELTYPE=SIGNEDBYTE option (SetColorTable on an Int8 band is refused), so the color table and
# the -128 tag are set on the unsigned intermediate first and the copy reinterprets 128 as -128.
# Usage: DATA_DIR=/path/to/data scripts/finalize.sh
set -eo pipefail

source /home/jdpaul3/miniconda3/etc/profile.d/conda.sh
conda activate snap-geo  # before set -u: the env's activate scripts read unset vars
set -u
PY=$CONDA_PREFIX/bin/python
SCRIPTS=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

DATA_DIR=${DATA_DIR:-/import/beegfs/CMIP6/jdpaul3/landcover_2021_data}
SRC=$DATA_DIR/work/land_cover_2021_albers_warped.tif
OUT=$DATA_DIR/output/land_cover_2021_albers.tif
mkdir -p "$DATA_DIR/output"
rm -f "$OUT"

$PY -I "$SCRIPTS/set_colortable.py" "$SRC"
# gdal_edit, not -a_nodata: gdal_translate/gdalwarp clamp -128 to 0 on Byte
gdal_edit.py -a_nodata -128 "$SRC"
gdal_translate -co TILED=YES -co BLOCKXSIZE=128 -co BLOCKYSIZE=128 -co COMPRESS=LZW \
  -co BIGTIFF=YES -co PIXELTYPE=SIGNEDBYTE "$SRC" "$OUT"
gdalinfo "$OUT" | grep -vE '^ +[0-9]+: '

#!/bin/bash
# Turn the warped intermediate into the final drop-in GeoTIFF: color table, overviews, nodata tag.
# The -128 nodata tag MUST be the last step: gdalwarp/gdal_translate clamp -128 to 0 on Byte data.
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

cp "$SRC" "$OUT"
$PY -I "$SCRIPTS/set_colortable.py" "$OUT"
gdaladdo -r nearest --config COMPRESS_OVERVIEW DEFLATE --config GDAL_NUM_THREADS ALL_CPUS \
  "$OUT" 2 4 8 16 32 64 128
gdal_edit.py -a_nodata -128 "$OUT"
gdalinfo "$OUT" | grep -vE '^ +[0-9]+: '

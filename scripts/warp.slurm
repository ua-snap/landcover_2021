#!/bin/bash
#SBATCH --job-name=landcover_2021
#SBATCH --account=snap
#SBATCH --partition=t1small
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=64G
#SBATCH --time=08:00:00
#SBATCH --output=/import/beegfs/CMIP6/jdpaul3/landcover_2021_data/logs/%x_%j.out
# Warp the NALCMS 2020v2 Alaska tile onto the exact 2015 GeoServer grid, then finalize and verify.
# Submit from the repo root: sbatch scripts/warp.slurm
set -eo pipefail

source /home/jdpaul3/miniconda3/etc/profile.d/conda.sh
conda activate snap-geo  # before set -u: the env's activate scripts read unset vars
set -u
PY=$CONDA_PREFIX/bin/python
SCRIPTS=$SLURM_SUBMIT_DIR/scripts

export DATA_DIR=${DATA_DIR:-/import/beegfs/CMIP6/jdpaul3/landcover_2021_data}
SRC=$DATA_DIR/raw/usa/usa_land_cover_2020v2_30m_tif/ASK_NALCMS_landcover_2020v2_30m/data/ASK_NALCMS_landcover_2020v2_30m.tif
WARPED=$DATA_DIR/work/land_cover_2021_albers_warped.tif
mkdir -p "$DATA_DIR/work"

# Source nodata is 127 (outside the source rectangle) -> 128, the 2015 "outside" value.
# Source 0 (ocean, Canada, Russia inside the rectangle) stays 0, as in 2015.
# -te/-tr reproduce the 2015 grid exactly: no -tap, no rounding.
gdalwarp -overwrite -t_srs EPSG:3338 \
  -te -2542313.2452769727 -836092.2311327262 1782546.7547230273 2969617.768867274 \
  -tr 30 30 -r near -ot Byte -ovr NONE \
  -srcnodata 127 -dstnodata 128 \
  -multi -wo NUM_THREADS=ALL_CPUS -wm 4096 --config GDAL_CACHEMAX 8192 \
  -co TILED=YES -co BLOCKXSIZE=512 -co BLOCKYSIZE=512 \
  -co COMPRESS=DEFLATE -co BIGTIFF=YES -co NUM_THREADS=ALL_CPUS \
  "$SRC" "$WARPED"

"$SCRIPTS/finalize.sh"
$PY -I "$SCRIPTS/verify.py" "$DATA_DIR/output/land_cover_2021_albers.tif"

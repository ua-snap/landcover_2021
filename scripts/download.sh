#!/bin/bash
# Download the NALCMS 2020v2 USA land cover zip + metadata from CEC, checksum, and unzip.
# Usage: DATA_DIR=/path/to/data scripts/download.sh
set -euo pipefail

DATA_DIR=${DATA_DIR:-/import/beegfs/CMIP6/jdpaul3/landcover_2021_data}
BASE=https://www.cec.org/files/atlas_layers/1_terrestrial_ecosystems/1_01_0_land_cover_2020_30m
RAW=$DATA_DIR/raw
mkdir -p "$RAW"

# file name -> expected byte count (from HTTP HEAD, 2026-10-30)
declare -A EXPECTED=(
  [usa_land_cover_2020v2_30m_tif.zip]=1688931815
  [metadata.zip]=69371
)

for f in "${!EXPECTED[@]}"; do
  curl -fL -C - -o "$RAW/$f" "$BASE/$f"
  size=$(stat -c %s "$RAW/$f")
  if [[ $size -ne ${EXPECTED[$f]} ]]; then
    echo "Size mismatch for $f: got $size, expected ${EXPECTED[$f]}" >&2
    exit 1
  fi
done

(cd "$RAW" && sha256sum *.zip > SHA256SUMS && cat SHA256SUMS)

unzip -o -q -d "$RAW/usa" "$RAW/usa_land_cover_2020v2_30m_tif.zip"
unzip -o -q -d "$RAW/metadata" "$RAW/metadata.zip"
find "$RAW/usa" "$RAW/metadata" -type f | sort

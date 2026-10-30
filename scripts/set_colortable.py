"""Write the 2015 layer's color table (scripts/colortable_2015.csv) into a GeoTIFF in place.

Every index not listed in the CSV is set to 0,0,0,255, as in the 2015 file.
Usage: python set_colortable.py <tif>
"""
import csv
import sys
from pathlib import Path

from osgeo import gdal

gdal.UseExceptions()

CSV = Path(__file__).with_name("colortable_2015.csv")

ct = gdal.ColorTable()
for i in range(256):
    ct.SetColorEntry(i, (0, 0, 0, 255))
with open(CSV) as f:
    for row in csv.DictReader(f):
        ct.SetColorEntry(int(row["value"]), (int(row["r"]), int(row["g"]), int(row["b"]), 255))

ds = gdal.Open(sys.argv[1], gdal.GA_Update)
band = ds.GetRasterBand(1)
band.SetColorTable(ct)
band.SetColorInterpretation(gdal.GCI_PaletteIndex)
ds = None

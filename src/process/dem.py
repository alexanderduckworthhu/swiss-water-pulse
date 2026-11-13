"""Mosaic the terrain tiles into one LV95 elevation grid."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from ..errors import DataSourceError
from ..settings import processed


def build_dem(cfg: dict, bounds: tuple[float, float, float, float]) -> Path:
    zoom = int(cfg["terrain"]["zoom"])
    folder = Path(cfg["_root"]) / "data" / "raw" / "terrain" / f"z{zoom}"
    tiles = sorted(folder.glob("*.tif")) if folder.exists() else []
    if not tiles:
        raise DataSourceError(
            f"No terrain tiles in {folder}. "
            "DHM25 is not in the STAC catalogue, so the fallback is Mapzen/AWS. "
            "Run `uv run python -m src.cli download --only terrain`."
        )
    sources = [rasterio.open(tile) for tile in tiles]
    try:
        mosaic, transform = merge(sources, nodata=np.nan)
        src_crs = sources[0].crs
        arr = mosaic[0].astype(np.float32)
        arr[(arr < -100) | (arr > 5000)] = np.nan
        left, bottom, right, top = bounds
        res = float(cfg["terrain"]["resolution_m"])
        width = max(2, int(round((right - left) / res)))
        height = max(2, int(round((top - bottom) / res)))
        dst_transform = from_origin(left, top, res, res)
        dest = np.full((height, width), np.nan, dtype=np.float32)
        reproject(
            arr,
            dest,
            src_transform=transform,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=cfg["crs"],
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    finally:
        for source in sources:
            source.close()
    out = processed(cfg, "dem.tif")
    profile = {
        "driver": "GTiff",
        "height": dest.shape[0],
        "width": dest.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": cfg["crs"],
        "transform": dst_transform,
        "nodata": np.nan,
        "compress": "deflate",
    }
    with rasterio.open(out, "w", **profile) as handle:
        handle.write(dest, 1)
    finite = np.isfinite(dest).mean()
    print(f"  elevation grid {dest.shape[1]}×{dest.shape[0]} at {res:.0f} m, {finite:.0%} filled")
    if finite < 0.5:
        raise DataSourceError(
            "Less than half of the Swiss elevation grid has data. "
            "The terrain tiles may be the wrong zoom or the wrong CRS. "
            f"Source CRS was {src_crs}."
        )
    return out

"""Snap the configured border outlets and sum the water leaving the country."""

from __future__ import annotations

import numpy as np
from shapely.geometry import Point


def choose_outlet(reaches, spec: dict, to_wgs) -> int:
    """Pick the reach whose outlet is near the anchor and whose area is close.

    `reaches` is a GeoDataFrame in EPSG:2056 with area_km2 and geometry.
    `to_wgs` maps an LV95 point to (lon, lat).
    """
    from pyproj import Transformer

    to_local = Transformer.from_crs("EPSG:4326", reaches.crs, always_xy=True)
    anchor = Point(to_local.transform(spec["lon"], spec["lat"]))
    hint = float(spec["area_km2"])
    best = None
    for index, row in reaches.iterrows():
        line = row.geometry
        if line is None or line.is_empty:
            continue
        # Distance to the line, not the downstream end. A reach can leave the
        # country and finish many kilometres past the gauge.
        distance = line.distance(anchor) / 1000.0
        if distance > 15:
            continue
        area = float(row.area_km2)
        if area <= 0:
            continue
        score = abs(np.log(area / hint)) + distance / 40.0
        if best is None or score < best[0]:
            best = (score, int(index) if not isinstance(index, int) else int(index), distance, area)
    if best is None:
        raise RuntimeError(
            f"No reach within 25 km of the {spec['label']} outlet at "
            f"({spec['lon']}, {spec['lat']}) with a usable upstream area. "
            "Move the anchor in config.yaml or lower min_upstream_km2."
        )
    return best[1]


def sum_outlets(q: np.ndarray, indices: list[int]) -> np.ndarray:
    """q is (reach, time). Duplicate indices are counted once."""
    unique = list(dict.fromkeys(indices))
    block = q[unique, :]
    with np.errstate(invalid="ignore"):
        total = np.nansum(block, axis=0)
    total = np.where(np.isfinite(block).any(axis=0), total, np.nan)
    return total.astype(np.float32)

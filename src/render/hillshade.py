"""Soft multi-direction hillshade, tinted like warm paper."""

from __future__ import annotations

import numpy as np

from .colors import mix, rgb


def _shade(elev: np.ndarray, resolution: float) -> tuple[np.ndarray, np.ndarray]:
    filled = np.where(np.isfinite(elev), elev, np.nanmedian(elev))
    dy, dx = np.gradient(filled, resolution, resolution)
    slope = np.arctan(np.hypot(dx, dy) * 2.8)
    aspect = np.arctan2(-dx, dy)
    # A lower sun than a midsummer noon, so valleys and the Alps separate.
    zenith = np.radians(90 - 38)
    total = np.zeros(filled.shape, dtype=np.float64)
    for azimuth, weight in ((315, 0.55), (25, 0.25), (250, 0.20)):
        az = np.radians(360.0 - azimuth + 90.0)
        shaded = np.cos(zenith) * np.cos(slope) + np.sin(zenith) * np.sin(slope) * np.cos(az - aspect)
        total += weight * np.clip(shaded, 0, 1)
    lo, hi = np.percentile(total, (3, 97))
    if hi > lo:
        total = (total - lo) / (hi - lo)
    return np.clip(total, 0, 1), filled


def colorize(elev: np.ndarray, resolution: float) -> np.ndarray:
    shaded, heights = _shade(elev, resolution)
    shadow = np.array(rgb("#6e5138"), dtype=np.float64)
    mid = np.array(rgb("#e4d0b4"), dtype=np.float64)
    light = np.array(rgb("#f6f1e7"), dtype=np.float64)
    cool = np.array(rgb("#c9d4d0"), dtype=np.float64)
    image = np.zeros(shaded.shape + (3,), dtype=np.float64)
    low = shaded <= 0.48
    span = np.where(low, shaded / 0.48, (shaded - 0.48) / 0.52)
    start = np.where(low[..., None], shadow, mid)
    end = np.where(low[..., None], mid, light)
    image[:] = start + (end - start) * span[..., None]
    high = np.clip((heights - 1400) / 1800, 0, 1) * 0.38
    image = image * (1 - high[..., None]) + cool * high[..., None]
    rgba = np.zeros(shaded.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = np.clip(image, 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    rgba[~np.isfinite(elev)] = (243, 238, 230, 255)
    return rgba

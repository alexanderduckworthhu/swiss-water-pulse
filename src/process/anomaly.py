"""Log anomaly of discharge against a per-reach normal."""

from __future__ import annotations

import numpy as np
import pandas as pd


def log_anomaly(q: np.ndarray, normal: np.ndarray, clip: float = 1.0) -> np.ndarray:
    """q and normal are (reach, time) and (reach,) or (reach, time).

    Returns log10(Q / normal), clipped to ±clip. Non-positive flows become NaN
    and are stored as 0 so a dry cell does not paint as a flood or a drought
    by accident; the ribbon width is what shows the low flow.
    """
    q = np.asarray(q, dtype=np.float64)
    normal = np.asarray(normal, dtype=np.float64)
    if normal.ndim == 1:
        normal = normal[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = q / normal
        anomaly = np.log10(ratio)
    bad = ~np.isfinite(anomaly) | (q <= 0) | (normal <= 0)
    anomaly = np.clip(anomaly, -clip, clip)
    anomaly = np.where(bad, 0.0, anomaly)
    return anomaly.astype(np.float32)


def year_median(q: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        med = np.nanmedian(q, axis=1)
    med = np.where(np.isfinite(med), med, np.nan)
    return med.astype(np.float64)


def day_of_year_median(q: np.ndarray, times: pd.DatetimeIndex, window: int = 15) -> np.ndarray:
    """Smoothed day-of-year median. q is (reach, time) and may span many years."""
    doy = np.asarray(times.dayofyear)
    n_reach = q.shape[0]
    normal = np.full_like(q, np.nan, dtype=np.float64)
    # Build a 366-day climatology per reach, then smooth.
    clim = np.full((n_reach, 366), np.nan, dtype=np.float64)
    for day in range(1, 367):
        mask = doy == day
        if not mask.any():
            continue
        with np.errstate(invalid="ignore"):
            clim[:, day - 1] = np.nanmedian(q[:, mask], axis=1)
    if window > 0:
        pad = window
        extended = np.concatenate([clim[:, -pad:], clim, clim[:, :pad]], axis=1)
        kernel = np.ones(2 * window + 1) / (2 * window + 1)
        smoothed = np.vstack([np.convolve(row, kernel, mode="valid") for row in extended])
        clim = smoothed
    for index, day in enumerate(doy):
        normal[:, index] = clim[:, int(day) - 1]
    return normal

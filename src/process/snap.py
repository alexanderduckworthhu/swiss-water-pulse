"""Match each reach to the GloFAS cell with the closest upstream area."""

from __future__ import annotations

import math

import numpy as np


def nearest_cell(lons: np.ndarray, lats: np.ndarray, grid_lons: np.ndarray, grid_lats: np.ndarray):
    j = np.abs(grid_lons[None, :] - lons[:, None]).argmin(axis=1).astype(np.int32)
    i = np.abs(grid_lats[None, :] - lats[:, None]).argmin(axis=1).astype(np.int32)
    return i, j


def snap_reaches(
    lons: np.ndarray,
    lats: np.ndarray,
    areas: np.ndarray,
    grid_lons: np.ndarray,
    grid_lats: np.ndarray,
    grid_area: np.ndarray,
    max_ratio: float = 2.0,
    window: int = 5,
) -> dict[str, np.ndarray]:
    """Pick a cell inside a 3×3, then a 5×5, whose area is within max_ratio.

    grid_area is shaped (latitude, longitude) and is NaN off the river network.
    Areas are in the same units on both sides (km²).
    """
    if window % 2 == 0 or window < 3:
        raise ValueError("snap window must be an odd integer of at least 3")
    n = len(lons)
    nlat, nlon = grid_area.shape
    cell_i = np.full(n, -1, dtype=np.int32)
    cell_j = np.full(n, -1, dtype=np.int32)
    cell_area = np.full(n, np.nan, dtype=np.float64)
    ratio = np.full(n, np.nan, dtype=np.float64)
    matched = np.zeros(n, dtype=bool)
    base_i, base_j = nearest_cell(lons, lats, grid_lons, grid_lats)
    log_limit = math.log(max_ratio)
    radii = list(range(1, window // 2 + 1))

    for reach in range(n):
        area = float(areas[reach])
        if not np.isfinite(area) or area <= 0:
            continue
        i0 = int(base_i[reach])
        j0 = int(base_j[reach])
        accepted = False
        for radius in radii:
            best_score = None
            best = None
            for di in range(-radius, radius + 1):
                ii = i0 + di
                if ii < 0 or ii >= nlat:
                    continue
                for dj in range(-radius, radius + 1):
                    jj = j0 + dj
                    if jj < 0 or jj >= nlon:
                        continue
                    candidate = float(grid_area[ii, jj])
                    if not np.isfinite(candidate) or candidate <= 0:
                        continue
                    score = abs(math.log(candidate / area))
                    if best_score is None or score < best_score:
                        best_score = score
                        best = (ii, jj, candidate, candidate / area)
            if best is not None and best_score is not None and best_score <= log_limit:
                cell_i[reach], cell_j[reach], cell_area[reach], ratio[reach] = best
                matched[reach] = True
                accepted = True
                break
            if accepted:
                break
    return {
        "matched": matched,
        "cell_i": cell_i,
        "cell_j": cell_j,
        "cell_area": cell_area,
        "ratio": ratio,
    }


def donor_reaches(matched: np.ndarray, next_down: np.ndarray, limit: int = 80) -> np.ndarray:
    """For an unmatched reach, walk downstream to the first matched reach.

    Matched reaches donate to themselves. Unroutable reaches stay at -1.
    """
    n = len(matched)
    donor = np.full(n, -1, dtype=np.int32)
    next_down = np.asarray(next_down, dtype=np.int32)
    for i in range(n):
        if matched[i]:
            donor[i] = i
            continue
        j = int(next_down[i])
        seen: set[int] = set()
        steps = 0
        while j >= 0 and steps < limit and j not in seen:
            if matched[j]:
                donor[i] = j
                break
            seen.add(j)
            j = int(next_down[j])
            steps += 1
    return donor


def apply_discharge(
    cell_q: np.ndarray,
    cell_i: np.ndarray,
    cell_j: np.ndarray,
    donor: np.ndarray,
    areas: np.ndarray,
    matched: np.ndarray,
) -> np.ndarray:
    """cell_q is (time, lat, lon) or (lat, lon) broadcast over time.

    Returns Q shaped (n_reach, n_time). Interpolated reaches are scaled by
    the ratio of upstream areas. Matched reaches keep the cell discharge.
    """
    squeezed = False
    if cell_q.ndim == 2:
        cell_q = cell_q[None, ...]
        squeezed = True
    n_time = cell_q.shape[0]
    n = len(donor)
    out = np.full((n, n_time), np.nan, dtype=np.float32)
    finite = donor >= 0
    for reach in np.flatnonzero(finite):
        src = int(donor[reach])
        ii = int(cell_i[src])
        jj = int(cell_j[src])
        if ii < 0 or jj < 0:
            continue
        series = cell_q[:, ii, jj]
        if matched[reach]:
            out[reach] = series
        else:
            src_area = float(areas[src])
            if src_area <= 0 or not np.isfinite(src_area):
                continue
            out[reach] = series * (float(areas[reach]) / src_area)
    if squeezed:
        return out[:, 0]
    return out


def snap_summary(matched: np.ndarray, donor: np.ndarray, ratio: np.ndarray) -> dict:
    n = len(matched)
    interpolated = (~matched) & (donor >= 0) & (donor != np.arange(n))
    # donor == index for matched; interpolated have a different donor
    interpolated = (~matched) & (donor >= 0)
    unmatched = donor < 0
    good = ratio[matched]
    good = good[np.isfinite(good)]
    def pct(values, p):
        if len(values) == 0:
            return None
        return float(np.percentile(values, p))

    return {
        "reaches": int(n),
        "matched": int(matched.sum()),
        "interpolated": int(interpolated.sum()),
        "unmatched": int(unmatched.sum()),
        "matched_fraction": float(matched.mean()) if n else 0.0,
        "ratio_p50": pct(good, 50),
        "ratio_p90": pct(good, 90),
    }

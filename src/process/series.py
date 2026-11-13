"""Daily discharge per reach, from GloFAS or from the FOEN mean as a preview."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from ..errors import DataSourceError


def _data_array(dataset: xr.Dataset) -> xr.DataArray:
    skip = {"latitude", "longitude", "lat", "lon", "time", "valid_time", "step"}
    candidates = []
    for name, array in dataset.data_vars.items():
        lowered = name.lower()
        if any(token in lowered for token in ("dis", "q_", "discharge", "avg")):
            candidates.append(array)
        elif not set(array.dims) <= skip and array.ndim >= 2:
            candidates.append(array)
    if not candidates:
        raise DataSourceError(
            f"No discharge variable in {getattr(dataset, 'encoding', {}).get('source', 'the GloFAS file')}. "
            f"Variables: {list(dataset.data_vars)}."
        )
    # Prefer a name that looks like discharge.
    named = [array for array in candidates if any(token in array.name.lower() for token in ("dis", "discharge"))]
    return named[0] if named else candidates[0]


def _rename_coords(array: xr.DataArray) -> xr.DataArray:
    rename = {}
    for dim in array.dims:
        lowered = dim.lower()
        if lowered in {"lat", "latitude", "y"}:
            rename[dim] = "latitude"
        elif lowered in {"lon", "longitude", "x"}:
            rename[dim] = "longitude"
        elif lowered in {"time", "valid_time"}:
            rename[dim] = "time"
    array = array.rename(rename)
    if "time" not in array.dims:
        array = array.expand_dims(time=[np.datetime64("NaT")])
    return array.transpose("time", "latitude", "longitude")


def _stamp_as_accumulation_day(array: xr.DataArray) -> xr.DataArray:
    """GloFAS writes the 24-hour mean at 00:00 on the following day.

    A request for 15 June comes back as valid_time 16 June 00:00. The film's
    date counter should name the day the water actually flowed.
    """
    times = pd.to_datetime(array.time.values)
    if len(times) == 0 or any(t.hour or t.minute or t.second for t in times):
        return array
    return array.assign_coords(time=times - pd.Timedelta(days=1))


def _drop_duplicate_times(array: xr.DataArray) -> xr.DataArray:
    times = pd.to_datetime(array.time.values)
    _, index = np.unique(times, return_index=True)
    return array.isel(time=np.sort(index))


def load_glofas(paths: list[Path], bbox: dict) -> xr.DataArray:
    if not paths:
        raise DataSourceError("No GloFAS NetCDF files were passed in.")
    pieces = []
    for path in paths:
        try:
            dataset = xr.open_dataset(path)
        except Exception as exc:  # noqa: BLE001
            raise DataSourceError(
                f"Could not open {path.name} as NetCDF. If the EWDS returned GRIB, "
                "the request asked for netcdf and the API ignored it. "
                f"Underlying error: {exc}"
            ) from exc
        array = _rename_coords(_data_array(dataset))
        array = array.sel(
            longitude=slice(bbox["west"] - 0.1, bbox["east"] + 0.1),
            latitude=slice(bbox["south"] - 0.1, bbox["north"] + 0.1)
            if float(array.latitude[0]) < float(array.latitude[-1])
            else slice(bbox["north"] + 0.1, bbox["south"] - 0.1),
        )
        pieces.append(array)
        dataset.close()
    merged = xr.concat(pieces, dim="time").sortby("time")
    merged = _stamp_as_accumulation_day(merged)
    merged = _drop_duplicate_times(merged)
    # A zero or a huge sentinel is not a discharge.
    values = merged.values
    bad = ~np.isfinite(values) | (values < 0) | (values > 1e6)
    merged = merged.copy(data=np.where(bad, np.nan, values).astype(np.float32))
    return merged


def sample_reaches(discharge: xr.DataArray, cell_lat: np.ndarray, cell_lon: np.ndarray) -> np.ndarray:
    """Nearest-cell sample. Returns (reach, time)."""
    out = np.full((len(cell_lat), int(discharge.sizes["time"])), np.nan, dtype=np.float32)
    ok = np.isfinite(cell_lat) & np.isfinite(cell_lon)
    if not ok.any():
        return out
    lat = xr.DataArray(cell_lat[ok], dims="reach")
    lon = xr.DataArray(cell_lon[ok], dims="reach")
    sampled = discharge.sel(latitude=lat, longitude=lon, method="nearest")
    out[ok] = np.asarray(sampled.transpose("reach", "time").values, dtype=np.float32)
    return out


def discharge_on_reaches(reaches, discharge: xr.DataArray) -> tuple[np.ndarray, pd.DatetimeIndex]:
    donor = reaches["donor"].to_numpy(dtype=int)
    cell_lat = reaches["cell_lat"].to_numpy(dtype=float)
    cell_lon = reaches["cell_lon"].to_numpy(dtype=float)
    src_lat = np.full(len(reaches), np.nan)
    src_lon = np.full(len(reaches), np.nan)
    for index, source in enumerate(donor):
        if source < 0:
            continue
        src_lat[index] = cell_lat[source]
        src_lon[index] = cell_lon[source]
    sampled = sample_reaches(discharge, src_lat, src_lon)
    areas = reaches["area_km2"].to_numpy(dtype=float)
    matched = reaches["matched"].to_numpy(dtype=bool)
    for index, source in enumerate(donor):
        if source < 0 or matched[index] or source == index:
            continue
        src_area = areas[source]
        if not np.isfinite(src_area) or src_area <= 0:
            sampled[index] = np.nan
            continue
        sampled[index] = sampled[index] * (areas[index] / src_area)
    times = pd.to_datetime(discharge.time.values)
    return sampled, pd.DatetimeIndex(times)


def preview_series(reaches, days: pd.DatetimeIndex) -> np.ndarray:
    """Constant FOEN (or HydroRIVERS) mean, repeated across days. Not a forecast."""
    mean = reaches["q_mean"].to_numpy(dtype=float)
    mean = np.where(np.isfinite(mean) & (mean > 0), mean, np.nan)
    return np.repeat(mean[:, None], len(days), axis=1).astype(np.float32)

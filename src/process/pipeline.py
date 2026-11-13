"""Turn the raw downloads into the tables the renderer reads."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from pyproj import Transformer
from shapely.geometry import Point

from ..errors import DataSourceError
from ..settings import processed
from .dem import build_dem
from .network import (
    build_reaches,
    connect_rivers_to_lakes,
    dissolve_chains,
    load_country,
    load_glaciers,
    load_lakes,
    save_static,
)
from .outlets import choose_outlet
from .series import discharge_on_reaches, load_glofas, preview_series
from .snap import donor_reaches, snap_reaches, snap_summary


def _year_days(year: int) -> list[date]:
    day = date(year, 1, 1)
    out = []
    while day.year == year:
        out.append(day)
        day += timedelta(days=1)
    return out


def _uparea_grid(cfg: dict):
    path = Path(cfg["_root"]) / "data" / "raw" / "glofas" / "uparea_glofas_v4_0.nc"
    if not path.exists() or path.stat().st_size < 1_000_000:
        raise DataSourceError(
            f"GloFAS upstream area is not at {path}. "
            "The file is a direct download (no API key) from the CEMS auxiliary-data page. "
            "Run `uv run python -m src.cli download --only uparea`."
        )
    try:
        dataset = xr.open_dataset(path)
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(
            f"{path.name} is not a NetCDF grid. The wiki may have returned a login page. "
            f"Underlying error: {exc}"
        ) from exc
    names = {name.lower(): name for name in list(dataset.data_vars) + list(dataset.coords)}
    var_name = None
    for key in ("uparea", "upstream_area", "area"):
        if key in names and names[key] in dataset.data_vars:
            var_name = names[key]
            break
    if var_name is None:
        var_name = list(dataset.data_vars)[0]
        print(f"  upstream-area variable name is {var_name!r}, not 'uparea'")
    array = dataset[var_name]
    rename = {}
    for dim in array.dims:
        if dim.lower() in {"lat", "latitude", "y"}:
            rename[dim] = "latitude"
        elif dim.lower() in {"lon", "longitude", "x"}:
            rename[dim] = "longitude"
    array = array.rename(rename)
    bbox = cfg["bbox"]
    lat = array.latitude
    if float(lat[0]) < float(lat[-1]):
        lat_slice = slice(bbox["south"] - 0.3, bbox["north"] + 0.3)
    else:
        lat_slice = slice(bbox["north"] + 0.3, bbox["south"] - 0.3)
    array = array.sel(longitude=slice(bbox["west"] - 0.3, bbox["east"] + 0.3), latitude=lat_slice)
    values = np.asarray(array.values, dtype=np.float64)
    # The file is in m². A sentinel fill is a large negative or an absurd area.
    values[(values <= 0) | (values > 1e14)] = np.nan
    values = values / 1e6
    dataset.close()
    return array.longitude.values.astype(float), array.latitude.values.astype(float), values


def _sample_elevation(cfg: dict, reaches: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    import rasterio

    reaches = reaches.copy()
    with rasterio.open(processed(cfg, "dem.tif")) as src:
        band = src.read(1)
        mids = [line.interpolate(0.5, normalized=True) for line in reaches.geometry]
        rows, cols = rasterio.transform.rowcol(src.transform, [point.x for point in mids], [point.y for point in mids])
        elev = np.full(len(reaches), np.nan)
        for index, (row, col) in enumerate(zip(rows, cols)):
            if 0 <= row < band.shape[0] and 0 <= col < band.shape[1]:
                elev[index] = band[row, col]
    reaches["elev_m"] = elev
    reaches["alpine"] = np.isfinite(elev) & (elev >= float(cfg["alpine_elevation_m"]))
    return reaches


def _tag(cfg: dict, reaches: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, list[dict]]:
    reaches = reaches.reset_index(drop=True)
    to_wgs = Transformer.from_crs(cfg["crs"], "EPSG:4326", always_xy=True).transform
    mids = [line.interpolate(0.5, normalized=True) for line in reaches.geometry]
    lons, lats = to_wgs([point.x for point in mids], [point.y for point in mids])
    lons = np.asarray(lons)
    lats = np.asarray(lats)
    reaches["label"] = ""
    reaches["place"] = ""
    used: set[int] = set()
    for spec in cfg["river_labels"]:
        distance = np.hypot((lons - spec["lon"]) * 78.0, (lats - spec["lat"]) * 111.0)
        distance[reaches["area_km2"].to_numpy(dtype=float) < float(spec["min_area_km2"])] = 1e9
        for index in used:
            distance[index] = 1e9
        choice = int(np.argmin(distance))
        if distance[choice] < 40:
            reaches.at[choice, "label"] = spec["name"]
            used.add(choice)
        else:
            print(f"  no reach for the label {spec['name']!r} within 40 km of the anchor")
    for index in range(len(reaches)):
        best = None
        for place in cfg["places"]:
            distance = float(np.hypot((lons[index] - place["lon"]) * 78.0, (lats[index] - place["lat"]) * 111.0))
            if best is None or distance < best[0]:
                best = (distance, place["name"])
        if best and best[0] < 18:
            reaches.at[index, "place"] = best[1]
    outlets = []
    reaches["outlet_id"] = ""
    reaches["outlet_rhone"] = False
    seen = set()
    for spec in cfg["outlets"]:
        index = choose_outlet(reaches, spec, to_wgs)
        if index in seen:
            print(f"  outlet {spec['id']} landed on a reach already used; it will not be double-counted")
        seen.add(index)
        reaches.at[index, "outlet_id"] = spec["id"]
        if spec["id"] == "rhone":
            reaches.at[index, "outlet_rhone"] = True
        outlets.append({"id": spec["id"], "label": spec["label"], "place": spec["place"], "reach": int(index)})
        print(
            f"  outlet {spec['label']} at {spec['place']}: reach {index}, "
            f"{reaches.at[index, 'area_km2']:.0f} km²"
        )
    return reaches, outlets


def build_static(cfg: dict, require_snap: bool = True) -> gpd.GeoDataFrame:
    print("process: country, rivers, lakes, glaciers")
    country, cantons = load_country(cfg)
    reaches = build_reaches(cfg)
    lakes = load_lakes(cfg, country)
    print("process: connecting river mouths to lake shores")
    reaches = connect_rivers_to_lakes(reaches, lakes)
    reaches = dissolve_chains(reaches)
    glaciers = load_glaciers(cfg, country)
    minx, miny, maxx, maxy = country.bounds
    pad = 12_000
    print("process: elevation")
    build_dem(cfg, (minx - pad, miny - pad, maxx + pad, maxy + pad))
    reaches = _sample_elevation(cfg, reaches)
    print("process: snap reaches to GloFAS cells")
    try:
        grid_lons, grid_lats, grid_area = _uparea_grid(cfg)
    except DataSourceError:
        if require_snap:
            raise
        print("  upstream-area file is not ready, so this preview is not snapped.")
        print("  Ribbon height still uses the mean-flow column. Re-run process once uparea_glofas_v4_0.nc has finished downloading.")
        reaches["matched"] = False
        reaches["donor"] = -1
        reaches["cell_lat"] = np.nan
        reaches["cell_lon"] = np.nan
        reaches["area_ratio"] = np.nan
    else:
        ends = [Point(line.coords[-1]) for line in reaches.geometry]
        to_wgs = Transformer.from_crs(cfg["crs"], "EPSG:4326", always_xy=True).transform
        end_lon, end_lat = to_wgs([point.x for point in ends], [point.y for point in ends])
        snapped = snap_reaches(
            np.asarray(end_lon, dtype=float),
            np.asarray(end_lat, dtype=float),
            reaches["area_km2"].to_numpy(dtype=float),
            grid_lons,
            grid_lats,
            grid_area,
            max_ratio=float(cfg["snap_max_area_ratio"]),
            window=int(cfg["snap_window"]),
        )
        donor = donor_reaches(snapped["matched"], reaches["next_down"].to_numpy(dtype=int))
        reaches["matched"] = snapped["matched"]
        reaches["donor"] = donor
        cell_lat = np.full(len(reaches), np.nan)
        cell_lon = np.full(len(reaches), np.nan)
        ok = snapped["matched"]
        cell_lat[ok] = grid_lats[snapped["cell_i"][ok]]
        cell_lon[ok] = grid_lons[snapped["cell_j"][ok]]
        reaches["cell_lat"] = cell_lat
        reaches["cell_lon"] = cell_lon
        reaches["area_ratio"] = snapped["ratio"]
        summary = snap_summary(snapped["matched"], donor, snapped["ratio"])
        processed(cfg, "snap_stats.json").write_text(json.dumps(summary, indent=2))
        print(
            f"  matched {summary['matched']:,} / {summary['reaches']:,} "
            f"({summary['matched_fraction']:.0%}), interpolated {summary['interpolated']:,}, "
            f"unmatched {summary['unmatched']:,}"
        )
        if summary["ratio_p50"] is not None:
            print(f"  area ratio of accepted matches: median {summary['ratio_p50']:.2f}, p90 {summary['ratio_p90']:.2f}")
    reaches, outlets = _tag(cfg, reaches)
    processed(cfg, "outlets.json").write_text(json.dumps(outlets, indent=2))
    save_static(cfg, country, cantons, reaches, lakes, glaciers)
    return reaches


def write_series(cfg: dict, reaches: gpd.GeoDataFrame, q: np.ndarray, times: pd.DatetimeIndex, source: str) -> None:
    from .anomaly import log_anomaly, year_median

    normal = year_median(q)
    # A single repeated mean has no anomaly. Leave it at zero and say so.
    if source == "foen_mean":
        anomaly = np.zeros_like(q, dtype=np.float32)
    else:
        anomaly = log_anomaly(q, normal, clip=float(cfg["anomaly_clip"]))
    dataset = xr.Dataset(
        {
            "Q": (("reach", "time"), q.astype(np.float32)),
            "anomaly": (("reach", "time"), anomaly.astype(np.float32)),
        },
        coords={"reach": np.arange(q.shape[0]), "time": times},
        attrs={"source": source, "baseline": cfg["baseline"] if source != "foen_mean" else "foen_mean"},
    )
    dataset.to_netcdf(processed(cfg, "series.nc"))
    outlets = json.loads(processed(cfg, "outlets.json").read_text())
    indices = [item["reach"] for item in outlets]
    block = q[indices, :]
    total = np.nansum(np.where(np.isfinite(block), block, 0.0), axis=0)
    table = pd.DataFrame({"time": times, "total_m3s": total})
    for item, row in zip(outlets, block):
        table[item["id"]] = row
    table.to_csv(processed(cfg, "outlets.csv"), index=False)
    print(f"  series source={source}, days={q.shape[1]}, reaches={q.shape[0]}")


def process(cfg: dict, preview: bool = False) -> None:
    reaches = build_static(cfg, require_snap=not preview)
    glofas_files = sorted((Path(cfg["_root"]) / "data" / "raw" / "glofas").glob("discharge_*.nc"))
    if glofas_files and not preview:
        print("process: GloFAS discharge")
        discharge = load_glofas(glofas_files, cfg["bbox"])
        q, times = discharge_on_reaches(reaches, discharge)
        write_series(cfg, reaches, q, times, source="glofas")
        return
    if preview:
        print("process: preview series from the mean-flow column (not a day of GloFAS)")
        days = pd.DatetimeIndex(_year_days(int(cfg["year"])))
        q = preview_series(reaches, days)
        source = "foen_mean" if str(reaches["source"].iloc[0]) == "foen" else "hydrorivers_mean"
        write_series(cfg, reaches, q, days, source=source)
        return
    from ..download.glofas import CDSAPI_HELP

    raise DataSourceError(
        "The map geometry is built, and there is no daily discharge to colour it with.\n\n" + CDSAPI_HELP
    )

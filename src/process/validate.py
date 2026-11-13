"""Compare GloFAS with FOEN gauge files dropped in data/raw/foen/."""

from __future__ import annotations

import numpy as np
import pandas as pd
from shapely.geometry import Point

from ..download.foen import load_dropped_series
from ..errors import DataSourceError


def validation_table(cfg: dict, reaches, q: np.ndarray, times: pd.DatetimeIndex, stations) -> pd.DataFrame:
    series = load_dropped_series(cfg)
    if series.empty:
        print(
            "validate: no CSVs in data/raw/foen/. "
            "Station locations are saved so you can order the series. "
            "Nothing was compared, and no other discharge was treated as FOEN data."
        )
        return pd.DataFrame()
    discharge = series[series["parameter"].str.startswith("q")]
    if discharge.empty:
        raise DataSourceError(
            "FOEN CSVs are present but none are discharge files named {id}_q.csv."
        )
    rows = []
    for station_id, group in discharge.groupby("station_id"):
        station = stations[stations["station_id"].astype(str) == str(station_id)]
        if station.empty:
            print(f"  station {station_id} is not in the FOEN station shapefile; skipped")
            continue
        point = station.geometry.iloc[0]
        distances = reaches.geometry.distance(point)
        nearby = distances <= 3000
        if not nearby.any():
            print(f"  station {station_id} is more than 3 km from every reach")
            continue
        candidates = reaches.loc[nearby].copy()
        candidates["distance_m"] = distances[nearby]
        # Prefer a reach with a similar upstream area when the station file has one.
        reach_index = int(candidates.sort_values("distance_m").index[0])
        modelled = pd.Series(q[reach_index], index=times).resample("D").mean()
        observed = group.set_index("date")["value"].resample("D").mean()
        both = pd.concat([modelled.rename("glofas"), observed.rename("foen")], axis=1).dropna()
        if len(both) < 10:
            print(f"  station {station_id} overlaps GloFAS on only {len(both)} days")
            continue
        bias = float((both["glofas"] - both["foen"]).mean())
        corr = float(both["glofas"].corr(both["foen"]))
        denom = float(((both["foen"] - both["foen"].mean()) ** 2).sum())
        nse = float(1 - ((both["glofas"] - both["foen"]) ** 2).sum() / denom) if denom else np.nan
        rows.append(
            {
                "station_id": station_id,
                "name": station["name"].iloc[0] if "name" in station.columns else "",
                "days": int(len(both)),
                "foen_mean": round(float(both["foen"].mean()), 1),
                "glofas_mean": round(float(both["glofas"].mean()), 1),
                "bias_m3s": round(bias, 1),
                "correlation": round(corr, 2),
                "nse": round(nse, 2) if np.isfinite(nse) else None,
                "distance_m": round(float(candidates["distance_m"].min()), 0),
            }
        )
    table = pd.DataFrame(rows)
    if table.empty:
        print("validate: CSVs were read, but none overlapped a reach and the GloFAS dates.")
    return table


def station_request_list(stations, outlets) -> pd.DataFrame:
    """A short list to paste into the FOEN data request."""
    keep = stations.copy()
    if "name" not in keep.columns:
        keep["name"] = ""
    points = []
    for spec in outlets:
        points.append(Point(spec["lon"], spec["lat"]))
    # Stations are in LV95. Compare after the caller has reprojected.
    return keep

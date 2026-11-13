from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..errors import DataSourceError
from ..settings import raw
from .http import download
from .stac import collection_items, latest_item, pick_asset, require_collection


def download_stations(cfg: dict) -> Path:
    print("foen: gauging-station locations")
    collection = require_collection(cfg["stac"]["collections"], cfg["stac"]["foen_stations"])
    item = latest_item(collection_items(collection))
    href = pick_asset(item, "shp")
    dest = raw(cfg, "foen", Path(href.split("?")[0]).name)
    print(f"  item {item.get('id')}")
    return download(href, dest)


def _column(frame: pd.DataFrame, options: tuple[str, ...]) -> str | None:
    lookup = {str(column).lower(): column for column in frame.columns}
    for option in options:
        if option in lookup:
            return lookup[option]
    for column in frame.columns:
        lowered = str(column).lower()
        if any(option in lowered for option in options):
            return column
    return None


def read_series_csv(path: Path) -> pd.DataFrame:
    """Read one FOEN CSV. See data/raw/foen/README.md for the contract."""
    last_error: Exception | None = None
    frame = None
    for encoding in ("utf-8", "cp1252", "latin-1"):
        for sep in (";", ","):
            try:
                frame = pd.read_csv(path, sep=sep, encoding=encoding, engine="python")
            except Exception as exc:  # noqa: BLE001 - try the next dialect
                last_error = exc
                continue
            if frame.shape[1] >= 2:
                break
            frame = None
        if frame is not None and frame.shape[1] >= 2:
            break
    if frame is None or frame.shape[1] < 2:
        raise DataSourceError(
            f"Could not parse {path.name} as a FOEN CSV ({last_error}). "
            "Expected a ';' or ',' file with a date column and a value column. "
            "See data/raw/foen/README.md."
        )
    date_col = _column(frame, ("date", "datum", "time", "zeit"))
    value_col = _column(
        frame,
        ("value", "wert", "discharge", "abfluss", "wasserstand", "pegel", "level"),
    )
    if date_col is None or value_col is None:
        raise DataSourceError(
            f"{path.name} columns are {list(frame.columns)}. "
            "Need a date (date/datum) and a value (value/wert/q/level). "
            "See data/raw/foen/README.md."
        )
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_col], dayfirst=True, errors="coerce"),
            "value": pd.to_numeric(frame[value_col], errors="coerce"),
        }
    )
    out = out.dropna(subset=["date"]).sort_values("date")
    stem = path.stem
    station, _, parameter = stem.partition("_")
    if not station.isdigit():
        raise DataSourceError(
            f"{path.name} should be named {{station_id}}_{{q|level}}.csv, "
            "for example 2289_q.csv."
        )
    out["station_id"] = station
    out["parameter"] = parameter or "q"
    out["source_file"] = path.name
    return out


def load_dropped_series(cfg: dict) -> pd.DataFrame:
    folder = Path(cfg["_root"]) / "data" / "raw" / "foen"
    files = sorted(folder.glob("*.csv"))
    if not files:
        return pd.DataFrame(columns=["date", "value", "station_id", "parameter", "source_file"])
    frames = [read_series_csv(path) for path in files]
    return pd.concat(frames, ignore_index=True)

from __future__ import annotations

from pathlib import Path

from ..errors import DataSourceError
from ..settings import raw
from .http import download
from .stac import collection_items, require_collection


def _assets_for_year(cfg: dict, year: int) -> dict[str, str]:
    collection = require_collection(cfg["stac"]["collections"], cfg["stac"]["meteo_grid"])
    features = collection_items(collection)
    archive = next((feature for feature in features if feature.get("id") == "archive-ch"), None)
    if archive is None:
        ids = ", ".join(feature.get("id", "") for feature in features[:15])
        raise DataSourceError(
            "MeteoSwiss gridded collection has no STAC item named 'archive-ch'. "
            f"Items seen: {ids}. "
            "List the collection at "
            f"{cfg['stac']['collections'].rsplit('/collections', 1)[0]}/collections/"
            f"{cfg['stac']['meteo_grid']}/items and update src/download/meteo.py."
        )
    found: dict[str, str] = {}
    for key, asset in (archive.get("assets") or {}).items():
        blob = key.lower()
        href = asset.get("href") or ""
        if str(year) not in blob and str(year) not in href:
            continue
        if "rhiresd" in blob:
            found["precip"] = href
        elif "tabsd" in blob:
            found["temperature"] = href
    if "precip" not in found or "temperature" not in found:
        sample = [key for key in (archive.get("assets") or {}) if str(year) in key.lower()][:8]
        raise DataSourceError(
            f"archive-ch has no RhiresD and TabsD asset for {year}. "
            f"Matching asset names: {sample or '(none)'}. "
            "Daily grids for that year may not be published yet. "
            "Precipitation stays off until the asset name is found; "
            "set show_precip: false, or pick another year."
        )
    return found


def download_meteo(cfg: dict) -> dict[str, Path]:
    print("meteo: RhiresD and TabsD")
    print("  daily precipitation is the 06:00 UTC to 05:50 UTC total (MeteoSwiss definition)")
    hrefs = _assets_for_year(cfg, int(cfg["year"]))
    saved = {}
    for kind, href in hrefs.items():
        dest = raw(cfg, "meteo", Path(href.split("?")[0]).name)
        saved[kind] = download(href, dest, timeout=600)
    return saved

from __future__ import annotations

from pathlib import Path

from ..errors import DataSourceError
from ..settings import raw
from .http import download, head_ok
from .stac import collection_items, latest_item, pick_asset, require_collection

HYDRORIVERS_URL = "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_eu_shp.zip"
HYDRORIVERS_PAGE = "https://www.hydrosheds.org/products/hydrorivers"


def download_foen_rivers(cfg: dict) -> Path:
    print("rivers: FOEN mean-flow network")
    collection = require_collection(cfg["stac"]["collections"], cfg["stac"]["foen_rivers"])
    item = latest_item(collection_items(collection))
    try:
        href = pick_asset(item, "gpkg")
    except DataSourceError:
        href = pick_asset(item, "gdb")
    dest = raw(cfg, "rivers", Path(href.split("?")[0]).name)
    print(f"  item {item.get('id')}")
    return download(href, dest)


def download_hydrorivers(cfg: dict) -> Path:
    print("rivers: HydroRIVERS upstream areas")
    if not head_ok(HYDRORIVERS_URL):
        raise DataSourceError(
            "HydroRIVERS Europe shapefile did not respond at\n"
            f"  {HYDRORIVERS_URL}\n"
            f"Open {HYDRORIVERS_PAGE} and put the current Europe shapefile URL "
            "in src/download/rivers.py (HYDRORIVERS_URL). "
            "Do not swap in a different river dataset quietly."
        )
    dest = raw(cfg, "rivers", "HydroRIVERS_v10_eu_shp.zip")
    return download(HYDRORIVERS_URL, dest, timeout=300)

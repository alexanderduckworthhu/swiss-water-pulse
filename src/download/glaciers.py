from __future__ import annotations

from pathlib import Path

from ..errors import DataSourceError
from ..settings import raw
from .http import download, head_ok

GLACIER_URL = "https://doi.glamos.ch/data/inventory/inventory_sgi2016_r2020.zip"
GLACIER_PAGE = "https://doi.glamos.ch/data/inventory/inventory_sgi2016_r2020.html"


def download_glaciers(cfg: dict) -> Path:
    print("glaciers: Swiss Glacier Inventory 2016")
    if not head_ok(GLACIER_URL):
        raise DataSourceError(
            "The SGI2016 zip did not respond at\n"
            f"  {GLACIER_URL}\n"
            f"Open {GLACIER_PAGE} and update GLACIER_URL in src/download/glaciers.py."
        )
    dest = raw(cfg, "glaciers", "inventory_sgi2016_r2020.zip")
    return download(GLACIER_URL, dest)

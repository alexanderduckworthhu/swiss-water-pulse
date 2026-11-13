from __future__ import annotations

from pathlib import Path

from ..settings import raw
from .http import download
from .stac import collection_items, latest_item, pick_asset, require_collection


def download_boundaries(cfg: dict) -> Path:
    print("boundaries: swissBOUNDARIES3D")
    collection = require_collection(cfg["stac"]["collections"], cfg["stac"]["boundaries"])
    item = latest_item(collection_items(collection))
    href = pick_asset(item, "gpkg")
    dest = raw(cfg, "boundaries", Path(href.split("?")[0]).name)
    print(f"  item {item.get('id')}")
    return download(href, dest)

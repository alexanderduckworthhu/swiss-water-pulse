from __future__ import annotations

from pathlib import Path

from ..errors import DataSourceError
from ..settings import raw
from .http import download
from .stac import collection_items, latest_item, pick_asset, require_collection


def download_lakes(cfg: dict) -> Path:
    source = cfg["lakes"]["source"]
    if source == "tlm3d":
        collection_id = cfg["stac"]["tlm3d"]
        note = "swissTLM3D is about 3 GB"
    elif source == "tlmregio":
        collection_id = cfg["stac"]["tlmregio"]
        note = "swissTLMRegio standing water"
    else:
        raise DataSourceError(
            f"lakes.source is {source!r}. Use 'tlmregio' or 'tlm3d'. "
            "There is no other lake layer wired up."
        )
    print(f"lakes: {note}")
    collection = require_collection(cfg["stac"]["collections"], collection_id)
    item = latest_item(collection_items(collection))
    try:
        href = pick_asset(item, "gpkg")
    except DataSourceError:
        href = pick_asset(item, "shp")
    dest = raw(cfg, "lakes", Path(href.split("?")[0]).name)
    print(f"  item {item.get('id')}")
    if source == "tlm3d":
        print("  this file is about 3 GB; the download will take a while")
    return download(href, dest, timeout=600)

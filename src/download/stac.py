from __future__ import annotations

from ..errors import DataSourceError
from .http import get_json


def iter_collections(collections_url: str):
    url = collections_url
    seen = 0
    while url and seen < 40:
        seen += 1
        payload = get_json(url)
        if "collections" not in payload:
            raise DataSourceError(
                f"{url} is not a STAC collections response (no 'collections' key). "
                "The geo.admin STAC root may have moved. Open "
                "https://data.geo.admin.ch/api/stac/v1/collections and update config.yaml."
            )
        yield from payload["collections"]
        url = None
        for link in payload.get("links", []):
            if link.get("rel") == "next":
                url = link.get("href")


def require_collection(collections_url: str, collection_id: str) -> dict:
    matches = []
    close = []
    needle = collection_id.lower()
    for collection in iter_collections(collections_url):
        cid = collection.get("id", "")
        if cid == collection_id:
            matches.append(collection)
            break
        if needle.split(".")[-1][:12] in cid.lower():
            close.append(cid)
    if not matches:
        hint = ", ".join(close[:12]) if close else "no similar ids"
        raise DataSourceError(
            f"STAC collection {collection_id!r} is not in {collections_url}. "
            f"Nearby ids: {hint}. "
            "The dataset was renamed or pulled. Check SOURCES.md and config.yaml `stac`."
        )
    return matches[0]


def collection_items(collection: dict) -> list[dict]:
    items_url = None
    for link in collection.get("links", []):
        if link.get("rel") == "items":
            items_url = link.get("href")
    if not items_url:
        cid = collection.get("id")
        raise DataSourceError(f"STAC collection {cid} has no items link.")
    features = []
    url = items_url
    pages = 0
    while url and pages < 30:
        pages += 1
        payload = get_json(url if "?" in url else url + "?limit=100")
        if "features" not in payload:
            raise DataSourceError(f"{url} did not return STAC items.")
        features.extend(payload["features"])
        url = None
        for link in payload.get("links", []):
            if link.get("rel") == "next":
                url = link.get("href")
    if not features:
        raise DataSourceError(f"STAC collection {collection.get('id')} returned no items.")
    return features


def latest_item(features: list[dict]) -> dict:
    def sort_key(feature: dict) -> str:
        props = feature.get("properties") or {}
        return str(props.get("datetime") or feature.get("id") or "")

    return sorted(features, key=sort_key)[-1]


def pick_asset(feature: dict, prefer: str) -> str:
    """Pick a download href. prefer is a substring such as 'gpkg' or 'shp'."""
    assets = feature.get("assets") or {}
    ranked = []
    for key, asset in assets.items():
        href = asset.get("href") or ""
        blob = (key + " " + href).lower()
        if prefer and prefer not in blob:
            continue
        score = 0
        if ".gpkg" in blob:
            score += 3
        if ".shp" in blob:
            score += 2
        if ".gdb" in blob:
            score += 1
        ranked.append((score, href))
    if not ranked:
        names = ", ".join(list(assets)[:12]) or "(none)"
        raise DataSourceError(
            f"STAC item {feature.get('id')} has no asset matching {prefer!r}. "
            f"Assets: {names}."
        )
    ranked.sort(reverse=True)
    return ranked[0][1]

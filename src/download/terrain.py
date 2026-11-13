from __future__ import annotations

import math
from pathlib import Path

from ..errors import DataSourceError
from ..settings import raw
from .http import download, session

TILE_HOSTS = (
    "https://s3.amazonaws.com/elevation-tiles-prod/geotiff/{z}/{x}/{y}.tif",
    "https://elevation-tiles-prod.s3.amazonaws.com/geotiff/{z}/{x}/{y}.tif",
)


def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def explain_swisstopo_gap(cfg: dict) -> str:
    """Confirm the preferred DEM collection is absent by requesting it directly."""
    base = cfg["stac"]["collections"].rstrip("/")
    lines = []
    for collection_id in cfg["stac"]["terrain_candidates"]:
        url = f"{base}/{collection_id}"
        response = session().get(url, timeout=40)
        if response.status_code == 200:
            return (
                f"Unexpected: {collection_id} is in the STAC catalogue now. "
                "Wire it into src/download/terrain.py before using the AWS fallback."
            )
        lines.append(f"{collection_id} returned HTTP {response.status_code} at {url}")
    return " ".join(lines)


def download_terrain(cfg: dict) -> Path:
    print("terrain: checking swisstopo DHM25 / swissALTI3D, then Mapzen tiles")
    reason = explain_swisstopo_gap(cfg)
    print("  swisstopo DEM is not a single national download:")
    print("   ", reason)
    print("  swissALTI3D is 1 km tiles at 0.5 m or 2 m (tens of thousands of files).")
    print("  falling back to Mapzen terrain tiles on AWS, as configured.")

    bbox = cfg["bbox"]
    zoom = int(cfg["terrain"]["zoom"])
    x0, y_north = _tile_xy(bbox["north"], bbox["west"], zoom)
    x1, y_south = _tile_xy(bbox["south"], bbox["east"], zoom)
    xs = range(min(x0, x1), max(x0, x1) + 1)
    ys = range(min(y_north, y_south), max(y_north, y_south) + 1)
    folder = raw(cfg, "terrain", f"z{zoom}").parent
    # raw() treats the last part as a file and creates its parent. Make the folder.
    folder = Path(cfg["_root"]) / "data" / "raw" / "terrain" / f"z{zoom}"
    folder.mkdir(parents=True, exist_ok=True)

    saved = 0
    failed = 0
    tiles = [(x, y) for x in xs for y in ys]
    print(f"  {len(tiles)} tiles at zoom {zoom}")
    host = None
    for candidate in TILE_HOSTS:
        probe = candidate.format(z=zoom, x=tiles[0][0], y=tiles[0][1])
        response = session().head(probe, timeout=30, allow_redirects=True)
        if response.status_code < 400:
            host = candidate
            break
    if host is None:
        raise DataSourceError(
            "Mapzen/AWS terrain tiles did not respond at either\n"
            + "\n".join(TILE_HOSTS)
            + "\nDHM25 is also missing from the STAC API, and swissALTI3D is tiled at 1 km. "
            "No elevation grid was substituted. See SOURCES.md."
        )
    for x, y in tiles:
        dest = folder / f"{x}_{y}.tif"
        if dest.exists() and dest.stat().st_size > 1000:
            saved += 1
            continue
        url = host.format(z=zoom, x=x, y=y)
        try:
            download(url, dest, timeout=60)
            saved += 1
        except DataSourceError:
            failed += 1
    if saved == 0:
        raise DataSourceError(
            f"Every terrain tile at zoom {zoom} failed ({failed} tiles). Host was {host}."
        )
    note = folder / "SOURCE.txt"
    note.write_text(
        "Mapzen terrain tiles hosted by AWS Open Data.\n"
        f"Host pattern: {host}\n"
        "Used because DHM25 is not in the live STAC catalogue and swissALTI3D is 1 km tiles.\n"
        f"{reason}\n"
    )
    print(f"  terrain tiles saved {saved}, missing {failed}")
    return folder

"""Unpack archives and pick the layer we actually asked for."""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pyogrio

from ..errors import DataSourceError


def materialise(path: Path) -> Path:
    if path.is_dir() or path.suffix.lower() != ".zip":
        return path
    out = path.parent / f"{path.stem}_unpacked"
    if out.exists() and any(out.rglob("*")):
        return out
    if not path.exists() or path.stat().st_size < 100:
        raise DataSourceError(f"Missing archive {path}. Run the download step first.")
    out.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as archive:
            archive.extractall(out)
    except zipfile.BadZipFile as exc:
        raise DataSourceError(
            f"{path.name} is not a zip. The server may have returned an HTML error page. "
            "Delete the file and download it again."
        ) from exc
    return out


def find_dataset(path: Path) -> Path:
    path = materialise(path)
    if path.is_file():
        return path
    candidates: list[Path] = []
    for pattern in ("*.gpkg", "*.shp"):
        candidates.extend(p for p in path.rglob(pattern) if not p.name.startswith("."))
    candidates.extend(p for p in path.rglob("*.gdb") if p.is_dir())
    if not candidates:
        raise DataSourceError(f"No GeoPackage, shapefile, or FileGDB inside {path}.")
    def weight(candidate: Path) -> int:
        if candidate.is_file():
            return candidate.stat().st_size
        return sum(child.stat().st_size for child in candidate.rglob("*") if child.is_file())
    return max(candidates, key=weight)


def list_layers(path: Path) -> list[tuple[str, str]]:
    try:
        rows = pyogrio.list_layers(path)
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(
            f"Could not open {path}. If this is a FileGDB, the GDAL build needs OpenFileGDB. "
            f"Underlying error: {exc}"
        ) from exc
    return [(str(name), str(geom)) for name, geom in rows]


def read_layer(path: Path, layer: str) -> gpd.GeoDataFrame:
    frame = gpd.read_file(path, layer=layer)
    if frame.crs is None:
        raise DataSourceError(f"{path.name} layer {layer} has no CRS. Refusing to guess.")
    # swisstopo files often arrive as a compound CRS (LV03 or LV95 plus LN02 height).
    # Geometry work uses the horizontal part only.
    if frame.crs.is_compound:
        frame = frame.set_crs(frame.crs.sub_crs_list[0], allow_override=True)
    frame = frame.set_geometry(frame.geometry.force_2d())
    frame = frame[~frame.geometry.is_empty & frame.geometry.notna()].copy()
    return frame


def column(frame: gpd.GeoDataFrame, options: tuple[str, ...]) -> str | None:
    lookup = {str(name).lower(): name for name in frame.columns if name != "geometry"}
    for option in options:
        if option in lookup:
            return lookup[option]
    for name in frame.columns:
        if name == "geometry":
            continue
        lowered = str(name).lower()
        if any(option in lowered for option in options):
            return name
    return None


def pick_layer(layers: list[tuple[str, str]], hints: tuple[str, ...], geom: str) -> str:
    scored = []
    for name, geometry in layers:
        if geom not in geometry.lower():
            continue
        lowered = name.lower()
        score = sum(2 for hint in hints if hint in lowered)
        scored.append((score, name))
    if not scored:
        listing = ", ".join(f"{name} ({geom})" for name, geom in layers)
        raise DataSourceError(
            f"No {geom} layer matching {hints}. Layers in the file: {listing}."
        )
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if scored[0][0] == 0 and len(scored) > 1:
        listing = ", ".join(name for _, name in scored)
        raise DataSourceError(
            f"Several {geom} layers and none of the names match {hints}: {listing}. "
            "The dataset schema changed. Update the hints in src/process/network.py "
            "after looking at this list, rather than picking one silently."
        )
    return scored[0][1]

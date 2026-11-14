"""Draw one editorial frame. Geometry is in EPSG:2056; the page is pixels."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import skia
import xarray as xr
from PIL import Image

from ..credits import footer_line
from ..errors import DataSourceError
from ..settings import load_captions, processed
from .colors import anomaly_rgb, mix, rgb
from .hillshade import colorize

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def layout_spec(cfg: dict, name: str) -> dict:
    spec = cfg["frames"]["draft"] if name == "draft" else cfg["frames"][name]
    width, height = int(spec["width"]), int(spec["height"])
    margin = max(28, int(width * 0.048))
    if height > width * 1.05:
        map_rect = (margin, int(height * 0.155), width - margin, int(height * 0.785))
    else:
        map_rect = (margin, int(height * 0.145), width - margin, int(height * 0.73))
    return {"name": name, "width": width, "height": height, "margin": margin, "map": map_rect}


def _polygons(geom):
    kind = geom.geom_type
    if kind == "Polygon":
        yield geom
    elif kind == "MultiPolygon":
        yield from geom.geoms
    elif kind == "GeometryCollection":
        for part in geom.geoms:
            yield from _polygons(part)


def _projector(bounds, rect):
    minx, miny, maxx, maxy = bounds
    left, top, right, bottom = rect
    span_x = maxx - minx
    span_y = maxy - miny
    # The bounds were expanded to the rectangle's aspect, so the scales match.
    scale = (right - left) / span_x

    def project(x, y):
        return left + (x - minx) * scale, top + (maxy - y) * scale

    return project, scale


def _fit_bounds(country_bounds, rect) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = country_bounds
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    width = (maxx - minx) * 1.08
    height = (maxy - miny) * 1.08
    target = (rect[2] - rect[0]) / (rect[3] - rect[1])
    if width / height < target:
        width = height * target
    else:
        height = width / target
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)


def _path_from_polygons(polygons, project) -> skia.Path:
    path = skia.Path()
    path.setFillType(skia.PathFillType.kEvenOdd)
    for poly in polygons:
        for ring in (poly.exterior, *poly.interiors):
            coords = np.asarray(ring.coords)
            if len(coords) < 3:
                continue
            x, y = project(float(coords[0, 0]), float(coords[0, 1]))
            path.moveTo(x, y)
            for x0, y0 in coords[1:]:
                x, y = project(float(x0), float(y0))
                path.lineTo(x, y)
            path.close()
    return path


def _line_normals(coords: np.ndarray) -> np.ndarray:
    """Unit normals in LV95, one continuous side, biased toward north.

    Flipping each segment on its own makes a hairpin swap sides. The filled
    polygon then covers the whole bend and reads as a blob.
    """
    if len(coords) < 2:
        return np.zeros_like(coords)
    segment = np.diff(coords, axis=0)
    length = np.hypot(segment[:, 0], segment[:, 1])
    length[length == 0] = 1
    normal = np.stack((-segment[:, 1] / length, segment[:, 0] / length), axis=1)
    for index in range(1, len(normal)):
        if float(np.dot(normal[index], normal[index - 1])) < 0:
            normal[index] *= -1
    if float(normal[:, 1].mean()) < 0:
        normal *= -1
    vertex = np.zeros_like(coords)
    vertex[0] = normal[0]
    vertex[-1] = normal[-1]
    if len(coords) > 2:
        vertex[1:-1] = normal[:-1] + normal[1:]
    scale = np.hypot(vertex[:, 0], vertex[:, 1])
    scale[scale == 0] = 1
    return vertex / scale[:, None]


def _subsample(coords: np.ndarray, spacing_m: float = 1600) -> np.ndarray:
    """Keep the bends. A fixed handful of points cuts across a meander and the ribbon fills it."""
    if len(coords) <= 2:
        return coords
    keep = [0]
    anchor = coords[0]
    for index in range(1, len(coords) - 1):
        step = float(np.hypot(coords[index, 0] - anchor[0], coords[index, 1] - anchor[1]))
        if step >= spacing_m:
            keep.append(index)
            anchor = coords[index]
    keep.append(len(coords) - 1)
    return coords[keep]


def _line_length(coords: np.ndarray) -> float:
    if len(coords) < 2:
        return 0.0
    step = np.diff(coords, axis=0)
    return float(np.hypot(step[:, 0], step[:, 1]).sum())


def _merge_continuations(reaches, ribbons: list[dict]) -> list[dict]:
    """Join short pieces of the same river so the width has a length to sit on.

    The lower Rhine is still cut every kilometre or two. Each piece carries the
    whole river's discharge, and a ribbon wider than its piece draws as a blob.
    Pieces continue only while the upstream area barely changes, so a tributary
    stays its own ribbon and the headwaters are not drawn as wide as Basel.
    """
    by_index = {ribbon["index"]: ribbon for ribbon in ribbons}
    next_down = reaches["next_down"].to_numpy(dtype=int)
    area = reaches["area_km2"].to_numpy(dtype=float)

    def continues(current: int, nxt: int) -> bool:
        if nxt < 0 or nxt not in by_index:
            return False
        left, right = float(area[current]), float(area[nxt])
        if left <= 0 or right <= 0 or not np.isfinite(left) or not np.isfinite(right):
            return False
        ratio = right / left
        if ratio < 0.82 or ratio > 1.45:
            return False
        end = by_index[current]["xy"][-1]
        start = by_index[nxt]["xy"][0]
        return float(np.hypot(end[0] - start[0], end[1] - start[1])) < 2500

    continued = set()
    for ribbon in ribbons:
        nxt = int(next_down[ribbon["index"]])
        if continues(ribbon["index"], nxt):
            continued.add(nxt)
    used: set[int] = set()
    merged = []
    for start in [ribbon["index"] for ribbon in ribbons if ribbon["index"] not in continued]:
        if start in used:
            continue
        chain = [start]
        used.add(start)
        cursor = start
        while True:
            nxt = int(next_down[cursor])
            if nxt in used or not continues(cursor, nxt):
                break
            chain.append(nxt)
            used.add(nxt)
            cursor = nxt
        parts = [by_index[index] for index in chain]
        if len(parts) == 1:
            merged.append(parts[0])
            continue
        chunks = []
        owners = []
        shares = []
        for number, part in enumerate(parts):
            xy = part["xy"] if number == 0 else part["xy"][1:]
            share = part.get("share")
            if share is None or len(share) != len(part["xy"]):
                share = np.ones(len(part["xy"]))
            chunks.append(xy)
            owners.append(np.full(len(xy), part["index"], dtype=np.int32))
            shares.append(share if number == 0 else share[1:])
        xy = np.vstack(chunks)
        label = ""
        for part in parts:
            text = str(part.get("label") or "")
            if text and text.lower() != "nan":
                label = text
                break
        downstream = parts[-1]
        merged.append(
            {
                "index": downstream["index"],
                "xy": xy,
                "owners": np.concatenate(owners),
                "share": np.concatenate(shares),
                "normal": _line_normals(xy),
                "area": downstream["area"],
                "length": _line_length(xy),
                "label": label,
            }
        )
    for ribbon in ribbons:
        if ribbon["index"] not in used:
            merged.append(ribbon)
    merged.sort(key=lambda ribbon: ribbon["area"])
    return merged


def _ribbon_width_m(
    value: float,
    area_km2: float,
    length_m: float,
    frame_px: float,
    width_fraction: float,
    reference: float,
    scale: float,
    exponent: float = 0.5,
) -> float:
    """Ribbon width in metres. Each reach uses its own discharge.

    Specific discharge above about 1.2 m³/s per km² is a bad snap, not a flood
    on a river this size. A ribbon wider than the reach itself draws as a spike.
    """
    if not np.isfinite(value) or value <= 0.05 or scale <= 0 or reference <= 0:
        return 0.0
    if area_km2 > 1:
        value = min(value, max(8.0, float(area_km2) * 1.2))
    width_px = width_fraction * frame_px * (value / reference) ** exponent
    width_px = max(width_px, 0.4)
    width_m = width_px / scale
    # Only a reach shorter than its own stroke is capped. That stops a blob
    # without making one river change width where the discharge did not.
    if length_m > 0 and width_m > length_m:
        width_m = length_m * 0.85
    return width_m


def _ribbon_widths_m(values, areas, length_m, frame_px, width_fraction, reference, scale, exponent=0.5):
    """Same width as _ribbon_width_m, for every vertex of one ribbon at once."""
    values = np.asarray(values, dtype=float)
    areas = np.asarray(areas, dtype=float)
    width_m = np.zeros(values.shape, dtype=float)
    if scale <= 0 or reference <= 0:
        return width_m
    ok = np.isfinite(values) & (values > 0.05)
    if not np.any(ok):
        return width_m
    capped = values.copy()
    big = ok & (areas > 1)
    capped[big] = np.minimum(capped[big], np.maximum(8.0, areas[big] * 1.2))
    width_px = np.zeros(values.shape, dtype=float)
    width_px[ok] = width_fraction * frame_px * (capped[ok] / reference) ** exponent
    width_px[ok] = np.maximum(width_px[ok], 0.4)
    width_m = width_px / scale
    if length_m > 0:
        over = width_m > length_m
        width_m[over] = length_m * 0.85
    return width_m


def _font(path: str, size: float) -> skia.Font:
    face = skia.Typeface.MakeFromFile(path)
    if face is None:
        raise DataSourceError(f"Could not load the font at {path}.")
    return skia.Font(face, size)


def _resolve_fonts(cfg: dict) -> dict[str, str]:
    folder = Path(cfg["_root"]) / "data" / "raw" / "fonts"
    system = Path("/System/Library/Fonts/Supplemental")
    options = {
        "serif": [folder / "SourceSerif4-Regular.otf", system / "Georgia.ttf"],
        "serif_bold": [folder / "SourceSerif4-Semibold.otf", system / "Georgia Bold.ttf"],
        "serif_italic": [folder / "SourceSerif4-It.otf", system / "Georgia Italic.ttf"],
        "sans": [folder / "SourceSans3-Regular.otf", system / "Arial.ttf", Path("/System/Library/Fonts/Helvetica.ttc")],
        "sans_bold": [folder / "SourceSans3-Semibold.otf", system / "Arial Bold.ttf", system / "Arial.ttf"],
        "sans_italic": [folder / "SourceSans3-It.otf", system / "Arial Italic.ttf", system / "Georgia Italic.ttf"],
    }
    found = {}
    for key, paths in options.items():
        chosen = next((path for path in paths if path.exists()), None)
        if chosen is None:
            raise DataSourceError(f"No font available for {key}. Tried: {paths}")
        found[key] = str(chosen)
    if "SourceSerif" not in found["serif"]:
        print(f"  title font fell back to {found['serif']}")
    return found


def _hillshade_image(cfg: dict, bounds) -> np.ndarray:
    with rasterio.open(processed(cfg, "dem.tif")) as src:
        elev = src.read(1).astype(np.float32)
        transform = src.transform
    resolution = abs(transform.a)
    left, bottom, right, top = bounds
    # Row 0 is the north edge.
    col0 = int((left - transform.c) / transform.a)
    col1 = int((right - transform.c) / transform.a)
    row0 = int((transform.f - top) / abs(transform.e))
    row1 = int((transform.f - bottom) / abs(transform.e))
    row0, row1 = max(0, row0), min(elev.shape[0], row1)
    col0, col1 = max(0, col0), min(elev.shape[1], col1)
    window = elev[row0:row1, col0:col1]
    if window.size < 16:
        raise DataSourceError("The elevation window for this frame is empty. The DEM extent does not cover the country.")
    return colorize(window, resolution)


def _tracked(canvas, text, x, y, font, paint, tracking):
    cursor = x
    for char in text:
        blob = skia.TextBlob.MakeFromString(char, font)
        canvas.drawTextBlob(blob, cursor, y, paint)
        cursor += font.measureText(char) + tracking
    return cursor


def _wrap(text: str, font: skia.Font, width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if font.measureText(trial) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _overlaps(box, boxes, pad=6) -> bool:
    x, y, w, h = box
    for ox, oy, ow, oh in boxes:
        if x < ox + ow + pad and x + w + pad > ox and y < oy + oh + pad and y + h + pad > oy:
            return True
    return False


def _track_uparea(longitudes, latitudes, grid_lons, grid_lats, grid) -> np.ndarray:
    """Upstream area along a line, staying on the river cell rather than a hillside."""
    longitudes = np.asarray(longitudes, dtype=float)
    latitudes = np.asarray(latitudes, dtype=float)
    columns = np.clip(np.searchsorted(grid_lons, longitudes), 1, len(grid_lons) - 2)
    if grid_lats[0] > grid_lats[-1]:
        rows = len(grid_lats) - 1 - np.searchsorted(grid_lats[::-1], latitudes)
    else:
        rows = np.searchsorted(grid_lats, latitudes)
    rows = np.clip(rows, 1, grid.shape[0] - 2)
    samples = np.full(len(longitudes), np.nan)
    previous = None
    for index, (row, column) in enumerate(zip(rows, columns)):
        window = grid[row - 1 : row + 2, column - 1 : column + 2]
        flat = window[np.isfinite(window) & (window > 30)]
        if flat.size == 0:
            samples[index] = previous if previous is not None else np.nan
            continue
        if previous is None:
            chosen = float(np.max(flat))
        else:
            chosen = float(flat[np.argmin(np.abs(np.log(np.maximum(flat, 1.0) / previous)))])
            if chosen < previous * 0.55:
                kept = flat[flat >= previous * 0.75]
                chosen = float(kept[np.argmin(np.abs(kept - previous))]) if kept.size else previous
        previous = chosen
        samples[index] = chosen
    return samples


def _attach_catchment_share(cfg: dict, ribbons: list[dict]) -> None:
    """Scale each vertex by how much of the reach's catchment is upstream of it.

    A dissolved valley is one discharge at its mouth. Without this, Sion is
    drawn as wide as Lake Geneva, and Chur as wide as the end of its chain.
    """
    from pyproj import Transformer

    from ..process.pipeline import _uparea_grid

    grid_lons, grid_lats, grid = _uparea_grid(cfg)
    to_wgs = Transformer.from_crs(cfg["crs"], "EPSG:4326", always_xy=True)
    for ribbon in ribbons:
        xy = ribbon["xy"]
        lon, lat = to_wgs.transform(xy[:, 0], xy[:, 1])
        samples = _track_uparea(np.asarray(lon), np.asarray(lat), grid_lons, grid_lats, grid)
        finite = np.isfinite(samples)
        if finite.sum() < 2:
            ribbon["share"] = np.ones(len(xy))
            continue
        filled = samples.copy()
        good = np.flatnonzero(finite)
        filled[: good[0]] = samples[good[0]]
        filled[good[-1] + 1 :] = samples[good[-1]]
        gaps = np.flatnonzero(~np.isfinite(filled))
        if gaps.size:
            filled[gaps] = np.interp(gaps, good, samples[good])
        half = max(1, len(filled) // 2)
        downstream = filled[-1] if np.median(filled[half:]) >= np.median(filled[:half]) else filled[0]
        if not np.isfinite(downstream) or downstream <= 0:
            ribbon["share"] = np.ones(len(xy))
            continue
        ribbon["share"] = np.clip(filled / downstream, 0.12, 1.0)


def _swiss_reach_ids(reaches, country) -> set[int]:
    """Reaches on Swiss water, plus the downstream course after it leaves."""
    from shapely.prepared import prep

    border = country.buffer(250)
    country_prep = prep(country)
    border_prep = prep(border)
    next_down = reaches["next_down"].to_numpy(dtype=int)
    swiss: set[int] = set()
    for index, geom in enumerate(reaches.geometry):
        if geom is None or geom.is_empty:
            continue
        if not country_prep.intersects(geom) and not border_prep.intersects(geom):
            continue
        inside = geom.intersection(country)
        near = geom.intersection(border)
        inside_len = 0.0 if inside.is_empty else float(inside.length)
        near_len = 0.0 if near.is_empty else float(near.length)
        if inside_len >= 400 or near_len >= 1500:
            swiss.add(index)
    stack = list(swiss)
    while stack:
        index = stack.pop()
        downstream = int(next_down[index]) if index < len(next_down) else -1
        if downstream >= 0 and downstream not in swiss:
            swiss.add(downstream)
            stack.append(downstream)
    return swiss


def _settle_network(ribbons, reaches, country, lakes) -> list[dict]:
    """Draw each Swiss river along its real course, including past the border.

    Clipping a line on the national outline makes the Rhine, Rhône, Ticino,
    Inn and Doubs stop in the middle of their own water. The continuation is
    kept so the line can leave the frame. Rivers that never enter Switzerland
    are left out, and a mouth that stops just short of a lake is finished on
    the shore.
    """
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    swiss = _swiss_reach_ids(reaches, country)
    shore = lakes.geometry.boundary.union_all()
    water = lakes.geometry.union_all()
    next_down = reaches["next_down"].to_numpy(dtype=int)
    settled = []
    for ribbon in ribbons:
        index = int(ribbon["index"])
        owners = ribbon.get("owners")
        owner_ids = {index}
        if owners is not None:
            owner_ids.update(int(item) for item in owners)
        if owner_ids.isdisjoint(swiss):
            continue
        xy = np.asarray(ribbon["xy"], dtype=float)
        if len(xy) < 2:
            continue
        if 0 <= index < len(next_down) and int(next_down[index]) < 0:
            start_gap = float(Point(float(xy[0, 0]), float(xy[0, 1])).distance(water))
            end_gap = float(Point(float(xy[-1, 0]), float(xy[-1, 1])).distance(water))
            # The Rhône leaves Lake Geneva about 2 km before the mapped line
            # begins. Finish that mouth. Do not pull a headwater sideways.
            extend_start = 40 < start_gap < 4000 and end_gap > max(start_gap * 3, start_gap + 4000)
            extend_end = 40 < end_gap < 4000 and start_gap > max(end_gap * 3, end_gap + 4000)
            if extend_start or extend_end:
                point = Point(float(xy[0, 0]), float(xy[0, 1])) if extend_start else Point(float(xy[-1, 0]), float(xy[-1, 1]))
                snapped = nearest_points(point, shore)[1]
                extra = np.array([[snapped.x, snapped.y]])
                xy = np.vstack([extra, xy] if extend_start else [xy, extra])
                ribbon = {
                    **ribbon,
                    "xy": xy,
                    "normal": _line_normals(xy),
                    "length": _line_length(xy),
                }
                if owners is not None:
                    owned = np.asarray(owners)
                    cap = np.array([index], dtype=owned.dtype)
                    ribbon["owners"] = np.concatenate([cap, owned] if extend_start else [owned, cap])
                if ribbon.get("share") is not None:
                    shared = np.asarray(ribbon["share"], dtype=float)
                    cap = np.array([1.0])
                    ribbon["share"] = np.concatenate([cap, shared] if extend_start else [shared, cap])
        settled.append(ribbon)
    settled.sort(key=lambda item: item["area"])
    return settled


def load_scene(cfg: dict, layout_name: str) -> dict:
    layout = layout_spec(cfg, layout_name)
    country = gpd.read_file(processed(cfg, "country.geojson"))
    shape = country.geometry.iloc[0]
    bounds = _fit_bounds(shape.bounds, layout["map"])
    project, scale = _projector(bounds, layout["map"])
    reaches = gpd.read_parquet(processed(cfg, "reaches.parquet"))
    lakes = gpd.read_parquet(processed(cfg, "lakes.parquet"))
    glaciers = gpd.read_parquet(processed(cfg, "glaciers.parquet"))
    cantons = None
    canton_path = processed(cfg, "cantons.geojson")
    if cfg.get("show_cantons") and canton_path.exists():
        cantons = gpd.read_file(canton_path)
    series_path = processed(cfg, "series.nc")
    if not series_path.exists():
        from ..download.glofas import CDSAPI_HELP
        raise DataSourceError("No series.nc. Daily discharge has not been processed.\n\n" + CDSAPI_HELP)
    dataset = xr.open_dataset(series_path)
    times = pd.to_datetime(dataset.time.values)
    q = np.asarray(dataset.Q.values, dtype=np.float32)
    anomaly = np.asarray(dataset.anomaly.values, dtype=np.float32)
    flow_before, flow_step = _flow_motion(anomaly, int(cfg["frames"]["frames_per_day"]))
    source = str(dataset.attrs.get("source", ""))
    dataset.close()
    outlets = pd.read_csv(processed(cfg, "outlets.csv"), parse_dates=["time"])
    order = np.argsort(reaches["area_km2"].to_numpy(dtype=float))
    ribbons = []
    for index in order:
        line = reaches.geometry.iloc[index]
        if line is None or line.is_empty:
            continue
        coords = _subsample(np.asarray(line.coords, dtype=float)[:, :2])
        if len(coords) < 2:
            continue
        ribbons.append(
            {
                "index": int(index),
                "xy": coords,
                "normal": _line_normals(coords),
                "area": float(reaches["area_km2"].iloc[index]),
                "length": _line_length(coords),
                "label": str(reaches["label"].iloc[index] or "") if "label" in reaches.columns else "",
            }
        )
    _attach_catchment_share(cfg, ribbons)
    ribbons = _merge_continuations(reaches, ribbons)
    ribbons = _settle_network(ribbons, reaches, shape, lakes)
    fonts = _resolve_fonts(cfg)
    print("  hillshade")
    shade = _hillshade_image(cfg, bounds)
    map_w = layout["map"][2] - layout["map"][0]
    map_h = layout["map"][3] - layout["map"][1]
    shade = np.asarray(Image.fromarray(shade, mode="RGBA").resize((map_w, map_h), Image.Resampling.LANCZOS))
    used = ["swisstopo", "glamos", "terrain_aws", "glofas_uparea"]
    if source == "glofas":
        used.insert(0, "glofas")
    if "foen" in set(reaches["source"].astype(str)):
        used.insert(0, "foen")
    if (reaches["source"] == "hydrorivers").any() or True:
        used.append("hydrorivers")
    # HydroRIVERS is always read for upstream area. Keep the credit once.
    used = list(dict.fromkeys(used))
    footer = footer_line(
        used,
        copyright_text=f"© {cfg['copyright_year']} {cfg['author']}",
    )
    return {
        "cfg": cfg,
        "layout": layout,
        "bounds": bounds,
        "project": project,
        "scale": scale,
        "country": shape,
        "cantons": cantons,
        "lakes": lakes,
        "glaciers": glaciers,
        "reaches": reaches,
        "ribbons": ribbons,
        "times": times,
        "q": q,
        "anomaly": anomaly,
        "flow_before": flow_before,
        "flow_step": flow_step,
        "flow_frames": int(cfg["frames"]["frames_per_day"]),
        "outlets": outlets,
        "source": source,
        "shade": shade,
        "fonts": fonts,
        "footer": footer,
        "captions": load_captions(cfg),
        "places": cfg["places"],
    }


def _day_index(times: pd.DatetimeIndex, day: datetime) -> int:
    stamp = pd.Timestamp(day)
    matches = np.where(times.normalize() == stamp.normalize())[0]
    if len(matches) == 0:
        raise DataSourceError(
            f"{stamp.date()} is not in the processed series "
            f"({times.min().date()} to {times.max().date()})."
        )
    return int(matches[0])


def _ribbon_path(ribbon, project, scale, width_m) -> skia.Path | None:
    coords = ribbon["xy"]
    normal = ribbon["normal"]
    width_m = np.asarray(width_m, dtype=float)
    if width_m.ndim == 0:
        width_m = np.full(len(coords), float(width_m))
    # Half the width on each side. A one-sided ribbon puts the Rhine, which
    # is the northern border, entirely outside the country.
    half = normal * (width_m[:, None] / 2)
    left = []
    right = []
    for (x0, y0), (dx, dy) in zip(coords, half):
        left.append(project(x0 + dx, y0 + dy))
        right.append(project(x0 - dx, y0 - dy))
    if len(left) < 2:
        return None
    path = skia.Path()
    path.moveTo(*left[0])
    for point in left[1:]:
        path.lineTo(*point)
    for point in reversed(right):
        path.lineTo(*point)
    path.close()
    return path


def _flow_activity(anomaly: float) -> float:
    """0 for a river far below its normal, 0.5 at normal, 1 well above it.

    Day-to-day anomaly moves are small, so the response is steep: twice the
    usual water already reads as nearly full strength.
    """
    return float(min(1.0, max(0.0, 0.5 + 1.4 * float(anomaly))))


def _flow_motion(anomaly: np.ndarray, frames_per_day: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixels the water has travelled at the start of each day, and per frame.

    A river runs faster on a high day. Summing the speed keeps every dash
    moving smoothly when the day changes, so a pulse never jumps.
    """
    values = np.nan_to_num(np.asarray(anomaly, dtype=np.float64), nan=0.0)
    activity = np.clip(0.5 + 1.4 * values, 0.0, 1.0)
    step = 0.45 + 1.9 * activity
    per_day = step * frames_per_day
    before = np.cumsum(per_day, axis=1) - per_day
    return before.astype(np.float32), step.astype(np.float32)


def _centreline_path(ribbon: dict, project) -> skia.Path:
    cached = ribbon.get("_line")
    if cached is not None:
        return cached
    path = skia.Path()
    first = True
    for x, y in ribbon["xy"]:
        px, py = project(float(x), float(y))
        if first:
            path.moveTo(px, py)
            first = False
        else:
            path.lineTo(px, py)
    ribbon["_line"] = path
    return path


def _blend(today: np.ndarray, tomorrow: np.ndarray | None, fraction: float) -> np.ndarray:
    if tomorrow is None or fraction <= 0:
        return today
    both = np.isfinite(today) & np.isfinite(tomorrow)
    mixed = (1.0 - fraction) * today + fraction * tomorrow
    mixed = np.where(both, mixed, np.where(np.isfinite(today), today, tomorrow))
    return mixed


def paint(scene: dict, day_index: int, output: Path, preview_note: str | None = None, fraction: float = 0.0) -> None:
    from ..process.captions import caption_for_day

    cfg = scene["cfg"]
    layout = scene["layout"]
    width, height = layout["width"], layout["height"]
    margin = layout["margin"]
    project = scene["project"]
    scale = scene["scale"]
    colors = cfg["colors"]
    ink = rgb(colors["ink"])
    muted = rgb(colors["muted"])
    paper = rgb(colors["paper"])
    low, mid, high = rgb(colors["low"]), rgb(colors["mid"]), rgb(colors["high"])
    surface = skia.Surface(width, height)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorSetARGB(255, *paper))

    scale_ui = width / 1080
    fonts = scene["fonts"]
    serif = _font(fonts["serif"], 54 * scale_ui)
    serif_italic = _font(fonts["serif_italic"], 20 * scale_ui)
    sans = _font(fonts["sans"], 15 * scale_ui)
    sans_small = _font(fonts["sans"], 12 * scale_ui)
    sans_bold = _font(fonts["sans_bold"], 13 * scale_ui)
    date_font = _font(fonts["serif"], 46 * scale_ui)
    number_font = _font(fonts["serif"], 32 * scale_ui)

    country_path = _path_from_polygons(_polygons(scene["country"]), project)
    shadow = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(90, *rgb(colors["shadow"])))
    shadow.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 16 * scale_ui))
    canvas.save()
    canvas.translate(0, 12 * scale_ui)
    canvas.drawPath(country_path, shadow)
    canvas.restore()

    canvas.save()
    canvas.clipPath(country_path, doAntiAlias=True)
    shade = scene["shade"]
    image = skia.Image.fromarray(np.ascontiguousarray(shade))
    left, top, right, bottom = layout["map"]
    canvas.drawImage(image, left, top)

    glacier_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(210, *rgb(colors["glacier"])))
    for geom in scene["glaciers"].geometry:
        canvas.drawPath(_path_from_polygons(_polygons(geom), project), glacier_paint)

    if scene["cantons"] is not None:
        canton_paint = skia.Paint(
            AntiAlias=True,
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=max(0.6, 0.7 * scale_ui),
            Color=skia.ColorSetARGB(110, *rgb(colors["canton"])),
        )
        for geom in scene["cantons"].geometry:
            canvas.drawPath(_path_from_polygons(_polygons(geom), project), canton_paint)

    canvas.restore()
    # Lakes are drawn whole, including the part of Geneva, Constance, Maggiore
    # and Lugano that lies across the border. The country clip was cutting them.
    # The map rectangle keeps that water off the title and the date.
    left, top, right, bottom = layout["map"]
    map_clip = skia.Path()
    map_clip.addRect(skia.Rect.MakeLTRB(left, top, right, bottom))
    canvas.save()
    canvas.clipPath(map_clip, doAntiAlias=True)
    lake_low, lake_mid, lake_high = rgb(colors["lake_low"]), rgb(colors["lake_mid"]), rgb(colors["lake_high"])
    shore = skia.Paint(
        AntiAlias=True,
        Style=skia.Paint.kStroke_Style,
        StrokeWidth=max(0.8, 1.1 * scale_ui),
        Color=skia.ColorSetARGB(180, *mix(lake_mid, ink, 0.25)),
    )
    for geom in scene["lakes"].geometry:
        # Lake level anomaly needs the FOEN level CSVs. Until then the lakes
        # sit on the middle of their own ramp, which is the "usual" colour.
        fill = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(235, *lake_mid))
        path = _path_from_polygons(_polygons(geom), project)
        canvas.drawPath(path, fill)
        canvas.drawPath(path, shore)
    canvas.restore()

    # Rivers are clipped to the map only. A country clip was cutting the
    # Rhine, Rhône and the border lakes off in the middle of the water.
    canvas.save()
    canvas.clipPath(map_clip, doAntiAlias=True)

    nxt = day_index + 1 if day_index + 1 < scene["q"].shape[1] else None
    tomorrow_q = scene["q"][:, nxt] if nxt is not None else None
    tomorrow_a = scene["anomaly"][:, nxt] if nxt is not None else None
    q_today = _blend(scene["q"][:, day_index], tomorrow_q, fraction)
    anomaly_today = _blend(scene["anomaly"][:, day_index], tomorrow_a, fraction)
    reference = float(cfg["ribbon"]["reference_m3s"])
    width_fraction = float(cfg["ribbon"]["reference_width_fraction"])
    exponent = float(cfg["ribbon"].get("exponent", 0.5))
    frame_px = float(width)
    reach_area = scene["reaches"]["area_km2"].to_numpy(dtype=float)
    flow_before = scene["flow_before"]
    flow_step = scene["flow_step"]
    flow_frames = int(scene["flow_frames"])
    for ribbon in scene["ribbons"]:
        owners = ribbon.get("owners")
        if owners is None:
            owners = np.full(len(ribbon["xy"]), ribbon["index"], dtype=int)
        share = ribbon.get("share")
        if share is None or len(share) != len(owners):
            share = np.ones(len(owners))
        else:
            share = np.asarray(share, dtype=float)
        owners = np.asarray(owners, dtype=int)
        width_m = _ribbon_widths_m(
            q_today[owners] * share,
            reach_area[owners] * share,
            ribbon["length"],
            frame_px,
            width_fraction,
            reference,
            scale,
            exponent,
        )
        if float(np.nanmax(width_m)) <= 0:
            continue
        path = _ribbon_path(ribbon, project, scale, width_m)
        if path is None:
            continue
        colour_index = int(owners[len(owners) // 2])
        pulse = float(anomaly_today[colour_index])
        if not np.isfinite(pulse):
            pulse = 0.0
        colour = anomaly_rgb(pulse, low, mid, high)
        width_px = float(np.nanmedian(width_m)) * scale
        # A high day lights the river. The core stays the discharge width, so the
        # Rhine does not turn into a band, and a thin stream can still flash.
        if pulse > 0.08:
            strength = min(1.0, (pulse - 0.08) / 0.72)
            colour = mix(colour, (232, 244, 255), 0.34 * strength)
            glow_rgb = mix(high, (214, 236, 255), 0.74)
            glow = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(int(100 + 140 * strength), *glow_rgb))
            glow.setMaskFilter(
                skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, (3.4 + 9.0 * strength) * scale_ui)
            )
            canvas.drawPath(path, glow)
        # The river itself is a pale channel. The water moving in it is the pulse.
        channel = mix(colour, paper, 0.38)
        paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(215, *channel))
        canvas.drawPath(path, paint)

        activity = _flow_activity(pulse)
        on_px = (4.0 + 17.0 * activity) * scale_ui
        off_px = (19.0 - 11.0 * activity) * scale_ui
        period = on_px + off_px
        advance = (
            flow_before[colour_index, day_index]
            + flow_step[colour_index, day_index] * flow_frames * fraction
        ) * scale_ui + (int(ribbon["index"]) * 53 % 41) * scale_ui
        line = _centreline_path(ribbon, project)
        dash = skia.Paint(
            AntiAlias=True,
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=max(width_px * 1.1, (1.35 + 1.0 * activity) * scale_ui),
            Color=skia.ColorSetARGB(int(80 + 175 * activity), *mix(colour, ink, 0.18)),
        )
        dash.setPathEffect(skia.DashPathEffect.Make([on_px, off_px], (-advance) % period))
        canvas.drawPath(line, dash)
    canvas.restore()

    # Labels sit outside the clip so a glyph is never cut by the border.
    placed = []
    label_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(230, *muted))
    city_font = _font(fonts["sans"], 13 * scale_ui)
    river_font = _font(fonts["serif_italic"], 14 * scale_ui)
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:4326", cfg["crs"], always_xy=True)
    for place in scene["places"]:
        x_m, y_m = transformer.transform(place["lon"], place["lat"])
        x, y = project(x_m, y_m)
        text = place["name"]
        text_width = city_font.measureText(text)
        box = (x - text_width / 2, y - 16 * scale_ui, text_width, 16 * scale_ui)
        if _overlaps(box, placed):
            box = (box[0], box[1] - 14 * scale_ui, box[2], box[3])
            y -= 14 * scale_ui
        placed.append(box)
        blob = skia.TextBlob.MakeFromString(text, city_font)
        canvas.drawTextBlob(blob, x - text_width / 2, y, label_paint)

    reaches = scene["reaches"]
    for ribbon in scene["ribbons"]:
        label = str(ribbon.get("label") or reaches.iloc[ribbon["index"]].get("label") or "")
        if not label or label == "nan":
            continue
        value = float(q_today[ribbon["index"]])
        width_px = _ribbon_width_m(
            value, ribbon["area"], ribbon["length"], frame_px, width_fraction, reference, scale, exponent
        ) * scale
        coords = ribbon["xy"]
        normal = ribbon["normal"]
        at = int(len(coords) * 0.58)
        # Opposite the ribbon, so the italic name sits beside the stroke.
        x_m = coords[at, 0] - normal[at, 0] * (width_px / scale + 900)
        y_m = coords[at, 1] - normal[at, 1] * (width_px / scale + 900)
        x, y = project(x_m, y_m)
        text_width = river_font.measureText(label)
        # Source Serif has no problem with the name, but a label parked on the
        # border was sliding off the page. Keep the whole word inside the margin.
        x = min(max(x, margin), width - margin - text_width)
        y = min(max(y, margin + 130 * scale_ui), height - margin - 110 * scale_ui)
        box = (x, y - 14 * scale_ui, text_width, 16 * scale_ui)
        if _overlaps(box, placed, pad=3):
            continue
        placed.append(box)
        blob = skia.TextBlob.MakeFromString(label, river_font)
        canvas.drawTextBlob(blob, x, y, label_paint)

    title_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *ink))
    title = cfg["title"]
    canvas.drawTextBlob(skia.TextBlob.MakeFromString(title, serif), margin, margin + 48 * scale_ui, title_paint)
    sub_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *muted))
    subtitle = cfg["subtitle"].format(year=cfg["year"]).upper()
    _tracked(canvas, subtitle, margin, margin + 74 * scale_ui, sans_bold, sub_paint, 1.6 * scale_ui)

    day = scene["times"][day_index]
    caption = caption_for_day(
        scene["captions"],
        reaches,
        day,
        anomaly_today,
        q_today,
        scene["q"],
    )
    caption_text = preview_note or caption["season"]
    caption_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *rgb(colors["low"] if preview_note else colors["muted"])))
    canvas.drawTextBlob(
        skia.TextBlob.MakeFromString(caption_text, serif_italic),
        margin,
        margin + 102 * scale_ui,
        caption_paint,
    )

    _legend(canvas, layout, sans_small, sans, low, mid, high, muted, ink, scale_ui)
    _date_block(
        canvas, layout, day, scene, date_font, sans_bold, number_font, ink, muted, colors, scale_ui, preview_note, fraction
    )
    footer_font = _font(fonts["sans"], 11 * scale_ui)
    footer_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *rgb(colors["faint"])))
    lines = _wrap(scene["footer"], footer_font, width - 2 * margin)
    for index, line in enumerate(lines[-3:]):
        y = height - margin * 0.45 - (len(lines[-3:]) - 1 - index) * 14 * scale_ui
        canvas.drawTextBlob(skia.TextBlob.MakeFromString(line, footer_font), margin, y, footer_paint)

    if caption["callout"] and not preview_note:
        _callout(canvas, caption["callout"], scene, project, serif_italic, paper, ink, scale_ui, layout)

    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = surface.makeImageSnapshot()
    Image.frombytes("RGBA", (width, height), snapshot.tobytes()).save(output, format="PNG", compress_level=1)
    if os.environ.get("SWISS_QUIET") != "1":
        print(f"  wrote {output}")


def _legend(canvas, layout, small, sans, low, mid, high, muted, ink, scale):
    width = layout["width"]
    margin = layout["margin"]
    bar_w = 168 * scale
    bar_h = 9 * scale
    x = width - margin - bar_w
    y = margin + 8 * scale
    paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *muted))
    font = small
    label = "FLOW VS. THE RIVER'S NORMAL"
    canvas.drawTextBlob(skia.TextBlob.MakeFromString(label, font), x, y, paint)
    steps = 48
    for step in range(steps):
        t = step / (steps - 1)
        value = t * 2 - 1
        colour = anomaly_rgb(value, low, mid, high)
        rect = skia.Rect.MakeXYWH(x + bar_w * step / steps, y + 8 * scale, bar_w / steps + 0.6, bar_h)
        canvas.drawRect(rect, skia.Paint(Color=skia.ColorSetARGB(255, *colour)))
    tick = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *muted))
    tick_font = small
    canvas.drawTextBlob(skia.TextBlob.MakeFromString("÷10", tick_font), x, y + 28 * scale, tick)
    usual = "usual"
    canvas.drawTextBlob(
        skia.TextBlob.MakeFromString(usual, tick_font),
        x + bar_w / 2 - tick_font.measureText(usual) / 2,
        y + 28 * scale,
        tick,
    )
    ten = "×10"
    canvas.drawTextBlob(
        skia.TextBlob.MakeFromString(ten, tick_font),
        x + bar_w - tick_font.measureText(ten),
        y + 28 * scale,
        tick,
    )
    # Source Sans has no proportional-to glyph, so the old "∝" drew as a box.
    note = "Ribbon width follows discharge, same scale for every river"
    note_font = small
    # The note is long for the bar. Draw it under the ticks, right aligned if it fits,
    # otherwise let it run left from the right margin.
    note_w = note_font.measureText(note)
    canvas.drawTextBlob(
        skia.TextBlob.MakeFromString(note, note_font),
        width - margin - note_w,
        y + 46 * scale,
        tick,
    )


def _date_block(canvas, layout, day, scene, date_font, kicker_font, number_font, ink, muted, colors, scale, preview_note, fraction=0.0):
    margin = layout["margin"]
    height = layout["height"]
    stamp = f"{int(day.day)} {MONTHS[int(day.month) - 1]} {int(day.year)}"
    date_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *ink))
    base = height - margin - 78 * scale
    canvas.drawTextBlob(skia.TextBlob.MakeFromString(stamp, date_font), margin, base, date_paint)
    kicker = scene["cfg"]["outlet_headline"].upper()
    kicker_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *muted))
    canvas.drawTextBlob(skia.TextBlob.MakeFromString(kicker, kicker_font), margin, base + 22 * scale, kicker_paint)
    dates = pd.to_datetime(scene["outlets"]["time"]).dt.normalize()
    match = np.where(dates == pd.Timestamp(day).normalize())[0]
    total = float(scene["outlets"]["total_m3s"].iloc[int(match[0])]) if len(match) else float("nan")
    row = int(match[0]) if len(match) else -1
    if fraction > 0 and 0 <= row < len(scene["outlets"]) - 1:
        nxt = float(scene["outlets"]["total_m3s"].iloc[row + 1])
        if np.isfinite(total) and np.isfinite(nxt):
            total = (1.0 - fraction) * total + fraction * nxt
    number = "— " if not np.isfinite(total) else f"{total:,.0f} m³/s"
    canvas.drawTextBlob(skia.TextBlob.MakeFromString(number, number_font), margin, base + 58 * scale, date_paint)
    _sparkline(canvas, layout, scene, day, muted, ink, colors, scale, preview_note is not None, fraction)


def _sparkline(canvas, layout, scene, day, muted, ink, colors, scale, preview: bool, fraction: float = 0.0):
    width = layout["width"]
    margin = layout["margin"]
    plot_w = 280 * scale
    plot_h = 64 * scale
    x0 = width - margin - plot_w
    y0 = layout["height"] - margin - 86 * scale
    axis = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1, Color=skia.ColorSetARGB(180, *muted))
    if preview or scene["source"] == "foen_mean":
        canvas.drawLine(x0, y0 + plot_h, x0 + plot_w, y0 + plot_h, axis)
        note = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *muted))
        font = skia.Font(skia.Typeface.MakeFromFile(scene["fonts"]["sans"]), 12 * scale)
        canvas.drawTextBlob(
            skia.TextBlob.MakeFromString("Daily trace loads with GloFAS", font),
            x0,
            y0 + plot_h - 8 * scale,
            note,
        )
        return
    values = scene["outlets"]["total_m3s"].to_numpy(dtype=float)
    values = np.where(np.isfinite(values) & (values > 0), values, np.nan)
    if not np.isfinite(values).any():
        return
    logged = np.log10(values)
    lo, hi = np.nanmin(logged), np.nanmax(logged)
    if hi - lo < 0.05:
        hi = lo + 0.05
    dates = pd.to_datetime(scene["outlets"]["time"])
    def xy(index):
        xx = x0 + plot_w * (index / max(1, len(values) - 1))
        yy = y0 + plot_h * (1 - (logged[index] - lo) / (hi - lo))
        return xx, yy
    faint = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.4, Color=skia.ColorSetARGB(80, *muted))
    solid = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.8, Color=skia.ColorSetARGB(230, *ink))
    current = int(np.where(dates.dt.normalize() == pd.Timestamp(day).normalize())[0][0])
    for index in range(len(values) - 1):
        if not np.isfinite(logged[index]) or not np.isfinite(logged[index + 1]):
            continue
        x1, y1 = xy(index)
        x2, y2 = xy(index + 1)
        if index < current:
            canvas.drawLine(x1, y1, x2, y2, solid)
        elif index > current:
            canvas.drawLine(x1, y1, x2, y2, faint)
        else:
            mx = x1 + fraction * (x2 - x1)
            my = y1 + fraction * (y2 - y1)
            canvas.drawLine(x1, y1, mx, my, solid)
            canvas.drawLine(mx, my, x2, y2, faint)
    cursor = current + (fraction if current < len(values) - 1 else 0.0)
    left_i = int(np.floor(cursor))
    right_i = min(left_i + 1, len(values) - 1)
    if np.isfinite(logged[left_i]) and np.isfinite(logged[right_i]):
        cx1, cy1 = xy(left_i)
        cx2, cy2 = xy(right_i)
        span = cursor - left_i
        canvas.drawCircle(
            cx1 + span * (cx2 - cx1),
            cy1 + span * (cy2 - cy1),
            3.2 * scale,
            skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *ink)),
        )
    tick_font = skia.Font(skia.Typeface.MakeFromFile(scene["fonts"]["sans"]), 11 * scale)
    tick_paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *muted))
    year = int(day.year)
    for label, month in (("Jan", 1), ("Jul", 7), ("Dec", 12)):
        when = pd.Timestamp(year=year, month=month, day=1)
        index = int(np.argmin(np.abs((dates - when).dt.total_seconds())))
        xx, _ = xy(index)
        canvas.drawTextBlob(skia.TextBlob.MakeFromString(label, tick_font), xx - 8, y0 + plot_h + 14 * scale, tick_paint)


def _callout(canvas, callout, scene, project, font, paper, ink, scale, layout):
    reaches = scene["reaches"]
    line = reaches.geometry.iloc[callout["reach"]]
    point = line.interpolate(0.5, normalized=True)
    x, y = project(point.x, point.y)
    text = callout["text"]
    text_width = font.measureText(text)
    pad = 8 * scale
    rect = skia.Rect.MakeXYWH(x + 8, y - 28 * scale, text_width + pad * 2, 22 * scale)
    canvas.drawRoundRect(rect, 3, 3, skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(235, *paper)))
    canvas.drawRoundRect(
        rect, 3, 3,
        skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1, Color=skia.ColorSetARGB(180, *ink)),
    )
    canvas.drawTextBlob(
        skia.TextBlob.MakeFromString(text, font),
        rect.left() + pad,
        rect.bottom() - 6 * scale,
        skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(255, *ink)),
    )


def render_still(cfg: dict, day, output: Path, layout_name: str = "portrait", preview_note: str | None = None) -> None:
    scene = load_scene(cfg, layout_name)
    paint(scene, _day_index(scene["times"], day), output, preview_note=preview_note)

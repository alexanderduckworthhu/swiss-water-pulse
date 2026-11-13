"""Build the reach table, lakes, glaciers, and the country mask in LV95."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import nearest_points, unary_union

from ..errors import DataSourceError
from ..settings import processed, raw
from .vectors import column, find_dataset, list_layers, materialise, pick_layer, read_layer

NAME_FIELDS = ("name", "gew_name", "gewaesser", "fluss", "river", "label", "bezeichnung")
AREA_FIELDS = ("upland_skm", "ezg_km2", "ezg", "area_km2", "flaeche", "einzugsgebiet", "a_km2")
FLOW_FIELDS = ("dis_av_cms", "mqn_jahr", "mq", "q_mean", "qmean", "mean_dis", "mq_m3")


def _to_lines(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    exploded = frame.explode(index_parts=False).reset_index(drop=True)
    exploded = exploded[exploded.geometry.geom_type == "LineString"].copy()
    return exploded


def load_country(cfg: dict) -> tuple[object, gpd.GeoDataFrame | None]:
    folder = Path(cfg["_root"]) / "data" / "raw" / "boundaries"
    archive = _only(folder, "boundaries", (".zip", ".gpkg"))
    path = find_dataset(archive)
    layers = list_layers(path)
    print("  boundary layers:", ", ".join(name for name, _ in layers))
    country_layer = pick_layer(layers, ("landes", "country", "staat"), "polygon")
    country = read_layer(path, country_layer).to_crs(cfg["crs"])
    icc = column(country, ("icc", "iso2", "country"))
    if icc:
        mask = country[icc].astype(str).str.upper().isin(["CH", "CHE"])
        if mask.any():
            country = country.loc[mask]
    if country.empty:
        raise DataSourceError(f"{country_layer} did not contain Switzerland (icc CH).")
    shape = unary_union(country.geometry)
    cantons = None
    try:
        canton_layer = pick_layer(layers, ("kanton", "canton"), "polygon")
    except DataSourceError:
        canton_layer = None
    if canton_layer and cfg.get("show_cantons", True):
        cantons = read_layer(path, canton_layer).to_crs(cfg["crs"])
        name = column(cantons, ("name", "kanton"))
        if name and name != "name":
            cantons = cantons.rename(columns={name: "name"})
    return shape, cantons


def _only(folder: Path, label: str, suffixes: tuple[str, ...]) -> Path:
    files = [path for path in folder.iterdir() if path.suffix.lower() in suffixes and path.is_file()]
    if not files:
        raise DataSourceError(
            f"No {label} file in {folder}. Run `uv run python -m src.cli download --only {label}`."
        )
    return max(files, key=lambda path: path.stat().st_size)


def load_hydrorivers(cfg: dict) -> gpd.GeoDataFrame:
    archive = raw(cfg, "rivers", "HydroRIVERS_v10_eu_shp.zip")
    if not archive.exists():
        raise DataSourceError("HydroRIVERS zip is missing. Download it before building reaches.")
    path = find_dataset(archive)
    bbox = cfg["bbox"]
    print(f"  reading HydroRIVERS {path.name} inside the Swiss bbox")
    frame = gpd.read_file(path, bbox=(bbox["west"], bbox["south"], bbox["east"], bbox["north"]))
    if frame.crs is None:
        frame = frame.set_crs(4326)
    frame = frame.to_crs(cfg["crs"])
    frame = _to_lines(frame)
    area = column(frame, ("upland_skm",))
    if area is None:
        raise DataSourceError(
            f"HydroRIVERS columns are {list(frame.columns)}. UPLAND_SKM is missing, "
            "so reaches cannot be snapped. The file is not the v1.0 attribute schema."
        )
    frame["area_km2"] = pd.to_numeric(frame[area], errors="coerce")
    discharge = column(frame, ("dis_av_cms",))
    frame["q_mean"] = pd.to_numeric(frame[discharge], errors="coerce") if discharge else np.nan
    hyriv = column(frame, ("hyriv_id",))
    nxt = column(frame, ("next_down",))
    if hyriv is None or nxt is None:
        raise DataSourceError("HydroRIVERS is missing HYRIV_ID or NEXT_DOWN.")
    frame["hyriv_id"] = frame[hyriv].astype("int64")
    frame["next_hyriv"] = frame[nxt].astype("int64")
    frame["name"] = ""
    frame["source"] = "hydrorivers"
    keep = frame["area_km2"] >= float(cfg["min_upstream_km2"])
    print(f"  HydroRIVERS reaches in the bbox: {len(frame):,}, kept {int(keep.sum()):,}")
    return frame.loc[keep].reset_index(drop=True)


def _link_by_nodes(geometries, areas: np.ndarray) -> np.ndarray:
    starts: dict[tuple[int, int], list[int]] = {}
    for index, line in enumerate(geometries):
        x, y = line.coords[0]
        starts.setdefault((round(x), round(y)), []).append(index)
    nxt = np.full(len(geometries), -1, dtype=np.int32)
    for index, line in enumerate(geometries):
        x, y = line.coords[-1]
        candidates = [item for item in starts.get((round(x), round(y)), []) if item != index]
        if not candidates:
            continue
        nxt[index] = max(candidates, key=lambda item: areas[item] if np.isfinite(areas[item]) else -1)
    return nxt


def load_foen_rivers(cfg: dict) -> gpd.GeoDataFrame | None:
    folder = Path(cfg["_root"]) / "data" / "raw" / "rivers"
    files = [path for path in folder.iterdir() if "mittlere" in path.name.lower() or "abfluss" in path.name.lower()]
    if not files:
        print("  FOEN mean-flow network is not on disk")
        return None
    path = find_dataset(files[0])
    layers = list_layers(path)
    print("  FOEN river layers:", ", ".join(f"{name} ({geom})" for name, geom in layers))
    layer = pick_layer(layers, ("abfluss", "fluss", "gew", "river", "line"), "line")
    if all(hint not in layer.lower() for hint in ("abfluss", "fluss", "gew", "river")):
        # pick_layer may have returned the only line layer. That is fine.
        pass
    frame = _to_lines(read_layer(path, layer).to_crs(cfg["crs"]))
    print(f"  FOEN columns: {list(frame.columns)}")
    name = column(frame, NAME_FIELDS)
    area = column(frame, AREA_FIELDS)
    flow = column(frame, FLOW_FIELDS)
    if name and not pd.api.types.is_numeric_dtype(frame[name]):
        frame["name"] = frame[name].astype(str)
    else:
        name = None
        frame["name"] = ""
    frame["area_km2"] = pd.to_numeric(frame[area], errors="coerce").astype("float64") if area else np.nan
    frame["q_mean"] = pd.to_numeric(frame[flow], errors="coerce").astype("float64") if flow else np.nan
    frame["source"] = "foen"
    frame["hyriv_id"] = -1
    frame["next_hyriv"] = -1
    print(
        f"  FOEN lines {len(frame):,}; "
        f"name={name or 'none'} area={area or 'none'} mean_flow={flow or 'none'}"
    )
    return frame.reset_index(drop=True)


def _attach_area_from_hydro(foen: gpd.GeoDataFrame, hydro: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    hydro = hydro.copy()
    hydro["mid"] = hydro.geometry.interpolate(0.5, normalized=True)
    tree_frame = gpd.GeoDataFrame(hydro[["area_km2", "q_mean", "hyriv_id"]], geometry=hydro["mid"], crs=hydro.crs)
    points = foen.geometry.interpolate(0.5, normalized=True)
    joined = gpd.sjoin_nearest(
        gpd.GeoDataFrame(geometry=points, crs=foen.crs),
        tree_frame,
        how="left",
        max_distance=2000,
        distance_col="snap_m",
    )
    joined = joined[~joined.index.duplicated(keep="first")]
    foen = foen.copy()
    foen["hydro_area"] = pd.to_numeric(joined["area_km2"], errors="coerce").to_numpy(dtype=float)
    foen["hydro_q"] = pd.to_numeric(joined["q_mean"], errors="coerce").to_numpy(dtype=float)
    foen["area_km2"] = pd.to_numeric(foen["area_km2"], errors="coerce").astype("float64")
    foen["q_mean"] = pd.to_numeric(foen["q_mean"], errors="coerce").astype("float64")
    missing = ~np.isfinite(foen["area_km2"].to_numpy(dtype=float))
    foen.loc[missing, "area_km2"] = foen.loc[missing, "hydro_area"]
    missing_q = ~np.isfinite(foen["q_mean"].to_numpy(dtype=float))
    foen.loc[missing_q, "q_mean"] = foen.loc[missing_q, "hydro_q"]
    return foen


def _topology(frame: gpd.GeoDataFrame) -> np.ndarray:
    if "next_hyriv" in frame.columns and (frame["hyriv_id"] > 0).any():
        lookup = {int(hyriv): index for index, hyriv in enumerate(frame["hyriv_id"].to_numpy())}
        nxt = np.full(len(frame), -1, dtype=np.int32)
        for index, downstream in enumerate(frame["next_hyriv"].to_numpy()):
            nxt[index] = lookup.get(int(downstream), -1)
        return nxt
    return _link_by_nodes(list(frame.geometry), frame["area_km2"].to_numpy(dtype=float))


def _foen_or_hydro(foen: gpd.GeoDataFrame, hydro: gpd.GeoDataFrame, cfg: dict) -> gpd.GeoDataFrame:
    if not np.isfinite(foen["area_km2"].to_numpy(dtype=float)).any():
        print("  FOEN file has no upstream area. Joining HydroRIVERS UPLAND_SKM within 2 km.")
        foen = _attach_area_from_hydro(foen, hydro)
    elif (~np.isfinite(foen["area_km2"].to_numpy(dtype=float))).mean() > 0.2:
        foen = _attach_area_from_hydro(foen, hydro)
    foen = foen[np.isfinite(foen["area_km2"]) & (foen["area_km2"] >= float(cfg["min_upstream_km2"]))]
    foen = foen.reset_index(drop=True)
    linked = _link_by_nodes(list(foen.geometry), foen["area_km2"].to_numpy(dtype=float))
    linked_fraction = float((linked >= 0).mean()) if len(linked) else 0
    print(f"  FOEN node links: {linked_fraction:.0%} of reaches have a downstream reach")
    if linked_fraction < 0.35:
        print(
            "  That is too sparse to interpolate unmatched headwaters. "
            "Drawing HydroRIVERS, which carries NEXT_DOWN. Names are copied across."
        )
        return _copy_names(hydro, foen)
    foen = foen.copy()
    foen["next_down"] = linked
    return foen


def build_reaches(cfg: dict) -> gpd.GeoDataFrame:
    hydro = load_hydrorivers(cfg)
    source = cfg["rivers"]["source"]
    frame = hydro
    if source == "foen":
        foen = load_foen_rivers(cfg)
        if foen is None or len(foen) < 400:
            print(
                "  FOEN network is missing or has fewer than 400 lines. "
                "Drawing HydroRIVERS instead, and recording that in the reach table."
            )
        else:
            peak = float(np.nanmax(foen["q_mean"].to_numpy(dtype=float)))
            if peak < 200:
                print(
                    f"  FOEN MQN_JAHR peaks at {peak:.0f} m³/s. "
                    "The Rhine at Basel is about 1,000 m³/s, so this column is not accumulated discharge. "
                    "Drawing HydroRIVERS, which has DIS_AV_CMS and NEXT_DOWN. "
                    "FOEN stays in the project for the gauge CSVs."
                )
            else:
                frame = _foen_or_hydro(foen, hydro, cfg)
    if "next_down" not in frame.columns:
        frame = frame.copy()
        frame["next_down"] = _topology(frame)
    frame = frame.reset_index(drop=True)
    lengths = frame.geometry.length
    frame = frame.loc[lengths >= 150].reset_index(drop=True)
    # Topology indices were positional. Rebuild after the length filter.
    if frame["source"].iloc[0] == "hydrorivers":
        frame["next_down"] = _topology(frame)
    else:
        frame["next_down"] = _link_by_nodes(list(frame.geometry), frame["area_km2"].to_numpy(dtype=float))
    if np.nanmax(frame["area_km2"].to_numpy(dtype=float)) > 200_000:
        print("  upstream-area values are above 200,000, so they are being treated as m² and converted to km²")
        frame["area_km2"] = frame["area_km2"] / 1e6
        frame = frame.loc[frame["area_km2"] >= float(cfg["min_upstream_km2"])].reset_index(drop=True)
        if str(frame["source"].iloc[0]) == "hydrorivers":
            frame["next_down"] = _topology(frame)
        else:
            frame["next_down"] = _link_by_nodes(list(frame.geometry), frame["area_km2"].to_numpy(dtype=float))
    print(
        f"  drawing {len(frame):,} reaches from {frame['source'].iloc[0]}; "
        f"largest basin {frame['area_km2'].max():.0f} km², "
        f"largest mean flow {np.nanmax(frame['q_mean'].to_numpy(dtype=float)):.0f} m³/s"
    )
    return frame


def _copy_names(hydro: gpd.GeoDataFrame, foen: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if not (foen["name"].astype(str).str.len() > 1).any():
        return hydro
    named = foen[foen["name"].astype(str).str.len() > 1]
    points = hydro.geometry.interpolate(0.5, normalized=True)
    joined = gpd.sjoin_nearest(
        gpd.GeoDataFrame(geometry=points, crs=hydro.crs),
        gpd.GeoDataFrame({"name": named["name"].values}, geometry=named.geometry, crs=named.crs),
        how="left",
        max_distance=800,
    )
    joined = joined[~joined.index.duplicated(keep="first")]
    hydro = hydro.copy()
    hydro["name"] = joined["name"].fillna("").astype(str).to_numpy()
    return hydro


def load_lakes(cfg: dict, country) -> gpd.GeoDataFrame:
    folder = Path(cfg["_root"]) / "data" / "raw" / "lakes"
    files = [path for path in folder.iterdir() if path.suffix.lower() in {".zip", ".gpkg", ".shp"} and path.is_file()]
    if not files:
        raise DataSourceError("No lake file in data/raw/lakes. Download swissTLMRegio or swissTLM3D first.")
    path = find_dataset(max(files, key=lambda item: item.stat().st_size))
    layers = list_layers(path)
    print("  lake file layers:", ", ".join(f"{name} ({geom})" for name, geom in layers))
    layer = pick_layer(layers, ("stehend", "see", "lake", "standing", "wasserfl"), "polygon")
    lakes = read_layer(path, layer).to_crs(cfg["crs"])
    lakes["area_km2"] = lakes.geometry.area / 1e6
    name = column(lakes, NAME_FIELDS)
    lakes["name"] = lakes[name].astype(str) if name else ""
    keep = lakes["area_km2"] >= float(cfg["lake_min_km2"])
    lakes = lakes.loc[keep].copy()
    country_shape = country.buffer(500)
    lakes = lakes[lakes.geometry.intersects(country_shape)].copy()
    lakes = lakes.reset_index(drop=True)
    print(f"  lakes kept (≥ {cfg['lake_min_km2']} km², touching Switzerland): {len(lakes)}")
    if len(lakes) < 8:
        raise DataSourceError(
            f"Only {len(lakes)} lake polygons passed the {cfg['lake_min_km2']} km² filter "
            f"from layer {layer}. The standing-water layer is probably the wrong one. "
            f"Layers were: {', '.join(name for name, _ in layers)}."
        )
    return lakes


def connect_rivers_to_lakes(reaches: gpd.GeoDataFrame, lakes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Stop lines at the shoreline, and extend a nearby endpoint onto it.

    Downstream ids are remapped so a reach removed because it sat inside a
    lake does not leave a dangling pointer.
    """
    lakes = lakes.copy()
    lakes["geometry"] = lakes.geometry.buffer(0)
    shore = unary_union(lakes.geometry.boundary)
    water = unary_union(lakes.geometry)
    geoms = []
    for line in reaches.geometry:
        if line.intersects(water):
            cut = line.difference(water)
            if cut.is_empty:
                geoms.append(None)
                continue
            if isinstance(cut, MultiLineString):
                cut = max(cut.geoms, key=lambda part: part.length)
            line = cut
        coords = list(line.coords)
        for end_index in (0, -1):
            point = Point(coords[end_index])
            if point.distance(water) < 600:
                snapped = nearest_points(point, shore)[1]
                coords[end_index] = (snapped.x, snapped.y)
        if len(coords) >= 2 and LineString(coords).length >= 80:
            geoms.append(LineString(coords))
        else:
            geoms.append(None)
    reaches = reaches.copy()
    reaches["old_id"] = np.arange(len(reaches))
    reaches["geometry"] = geoms
    kept = reaches[reaches.geometry.notna()].reset_index(drop=True)
    mapping = {int(old): new for new, old in enumerate(kept["old_id"].to_numpy())}
    remapped = []
    for downstream in kept["next_down"].to_numpy():
        remapped.append(mapping.get(int(downstream), -1) if int(downstream) >= 0 else -1)
    kept["next_down"] = remapped
    return kept.drop(columns=["old_id"])


def _join_lines(parts: list) -> LineString:
    coords = list(parts[0].coords)
    for line in parts[1:]:
        nxt = list(line.coords)
        if len(nxt) < 2:
            continue
        tail = Point(coords[-1])
        forward = tail.distance(Point(nxt[0]))
        backward = tail.distance(Point(nxt[-1]))
        if forward <= backward:
            coords.extend(nxt[1:] if forward < 80 else nxt)
        else:
            ordered = list(reversed(nxt))
            coords.extend(ordered[1:] if backward < 80 else ordered)
    if len(coords) < 2:
        return parts[0]
    return LineString(coords)


def dissolve_chains(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Join segments between confluences so a ribbon can be longer than it is wide.

    The FOEN mean-flow layer is cut into pieces a few hundred metres long.
    At national scale those pieces are shorter than the Rhine's ribbon, so the
    thickness never reads. Discharge and area come from the downstream end.
    """
    frame = frame.reset_index(drop=True)
    count = len(frame)
    downstream = frame["next_down"].to_numpy(dtype=int)
    incoming = np.zeros(count, dtype=np.int32)
    for target in downstream:
        if 0 <= target < count:
            incoming[target] += 1
    # Mean flow, not the joined upstream area. The area join often snaps a
    # small tributary to the neighbouring main stem, which would split the
    # main river at every creek.
    flows = frame["q_mean"].to_numpy(dtype=float)
    incoming_from: list[list[int]] = [[] for _ in range(count)]
    for index, target in enumerate(downstream):
        if 0 <= int(target) < count:
            incoming_from[int(target)].append(index)
    visited = np.zeros(count, dtype=bool)
    chains: list[list[int]] = []

    def continues(cursor: int, target: int) -> bool:
        """Stay on the main stem when the joining stream is small."""
        if incoming[target] <= 1:
            return True
        others = [flows[index] for index in incoming_from[target] if index != cursor]
        other = max(others) if others else 0.0
        mine = flows[cursor]
        return mine >= other and other <= 0.22 * max(flows[target], mine, 0.05)

    def walk(start: int) -> None:
        if visited[start]:
            return
        chain = [start]
        visited[start] = True
        cursor = start
        while True:
            target = int(downstream[cursor])
            if target < 0 or target >= count or visited[target] or not continues(cursor, target):
                break
            chain.append(target)
            visited[target] = True
            cursor = target
        chains.append(chain)

    # Headwaters first, so a main stem claims the segments below its minor tributaries.
    for start in np.where(incoming == 0)[0]:
        walk(int(start))
    for index in range(count):
        walk(index)

    old_to_new = np.full(count, -1, dtype=int)
    lines = []
    labels = []
    for new_index, chain in enumerate(chains):
        for old in chain:
            old_to_new[old] = new_index
        lines.append(_join_lines([frame.geometry.iloc[old] for old in chain]))
        label = ""
        if "label" in frame.columns:
            for old in chain:
                text = str(frame["label"].iloc[old] or "")
                if text and text.lower() != "nan":
                    label = text
                    break
        labels.append(label)
    ends = [chain[-1] for chain in chains]
    out = frame.iloc[ends].copy().reset_index(drop=True)
    out = out.set_geometry(lines)
    pointers = []
    for new_index, chain in enumerate(chains):
        target = int(downstream[chain[-1]])
        pointer = int(old_to_new[target]) if 0 <= target < count else -1
        pointers.append(-1 if pointer == new_index else pointer)
    out["next_down"] = pointers
    if "label" in out.columns:
        out["label"] = labels
    print(
        f"  dissolved {count:,} segments into {len(out):,} reaches "
        f"(longest {out.geometry.length.max() / 1000:.0f} km)"
    )
    return out


def _rebuild_next(frame: gpd.GeoDataFrame) -> np.ndarray:
    if (frame.get("hyriv_id", pd.Series(dtype=int)) > 0).any():
        return _topology(frame)
    return _link_by_nodes(list(frame.geometry), frame["area_km2"].to_numpy(dtype=float))


def load_glaciers(cfg: dict, country) -> gpd.GeoDataFrame:
    archive = raw(cfg, "glaciers", "inventory_sgi2016_r2020.zip")
    root = materialise(archive)
    shapefiles = [path for path in root.rglob("*.shp") if path.is_file()]
    outlines = [
        path for path in shapefiles
        if "debris" not in path.name.lower()
        and "divide" not in path.name.lower()
        and "line" not in path.name.lower()
        and "location" not in path.name.lower()
    ]
    named = [path for path in outlines if path.stem.lower().endswith("glaciers") or "glacier" in path.name.lower()]
    exact = [path for path in named if path.stem.lower().endswith("glaciers")]
    named = exact or named
    if not named and not outlines:
        raise DataSourceError(
            f"SGI2016 zip unpacked but no glacier polygon shapefile was found under {root}. "
            f"Shapefiles: {[path.name for path in shapefiles]}."
        )
    path = (named or outlines)[0]
    print(f"  glacier outlines: {path.name}")
    glaciers = gpd.read_file(path)
    if glaciers.crs is None:
        glaciers = glaciers.set_crs(cfg["crs"])
    glaciers = glaciers.to_crs(cfg["crs"])
    glaciers = glaciers.set_geometry(glaciers.geometry.force_2d())
    glaciers = glaciers[glaciers.geometry.intersects(country)].copy()
    glaciers["geometry"] = glaciers.geometry.simplify(60)
    print(f"  glaciers inside Switzerland: {len(glaciers):,}")
    return glaciers.reset_index(drop=True)


def save_static(cfg: dict, country, cantons, reaches, lakes, glaciers) -> None:
    country_path = processed(cfg, "country.geojson")
    gpd.GeoDataFrame(geometry=[country], crs=cfg["crs"]).to_file(country_path, driver="GeoJSON")
    if cantons is not None:
        keep = [column for column in ("name",) if column in cantons.columns]
        cantons[keep + ["geometry"]].to_file(processed(cfg, "cantons.geojson"), driver="GeoJSON")
    reaches_out = reaches.copy()
    reaches_out["geometry"] = reaches_out.geometry.simplify(90)
    drop = {"hydro_area", "hydro_q", "hyriv_id", "next_hyriv", "mid", "snap_m", "old_id"}
    keep = [name for name in reaches_out.columns if name != "geometry" and name not in drop]
    reaches_out[keep + ["geometry"]].to_parquet(processed(cfg, "reaches.parquet"))
    lake_cols = ["name", "area_km2", "geometry"]
    lakes[lake_cols].to_parquet(processed(cfg, "lakes.parquet"))
    glaciers[["geometry"]].to_parquet(processed(cfg, "glaciers.parquet"))
    stats = {
        "reaches": int(len(reaches)),
        "source": str(reaches["source"].iloc[0]) if len(reaches) else None,
        "lakes": int(len(lakes)),
        "glaciers": int(len(glaciers)),
        "linked_fraction": float((reaches["next_down"] >= 0).mean()) if len(reaches) else 0,
    }
    processed(cfg, "network_stats.json").write_text(json.dumps(stats, indent=2))
    print("  wrote reaches, lakes, glaciers, country")

"""Command line for Switzerland's Water Pulse."""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from .download.boundaries import download_boundaries
from .download.fonts import download_fonts
from .download.foen import download_stations
from .download.glaciers import download_glaciers
from .download.glofas import download_discharge, download_uparea, probe_catalogue
from .download.lakes import download_lakes
from .download.meteo import download_meteo
from .download.rivers import download_foen_rivers, download_hydrorivers
from .download.terrain import download_terrain
from .errors import DataSourceError
from .process.pipeline import process
from .render.encode import encode_fast, encode_gif, encode_sequence, require_ffmpeg
from .render.frame import _day_index, load_scene, paint
from .settings import load_config, root


def _days(text: str | None, year: int) -> list[date]:
    if not text:
        start, end = date(year, 1, 1), date(year, 12, 31)
    elif ":" in text:
        left, right = text.split(":", 1)
        start, end = date.fromisoformat(left), date.fromisoformat(right)
    else:
        start = end = date.fromisoformat(text)
    out = []
    day = start
    while day <= end:
        out.append(day)
        day += timedelta(days=1)
    return out


def _download(cfg: dict, only: set[str] | None, days: list[date], optional: bool) -> None:
    steps = {
        "boundaries": lambda: download_boundaries(cfg),
        "rivers": lambda: (download_foen_rivers(cfg), download_hydrorivers(cfg)),
        "lakes": lambda: download_lakes(cfg),
        "glaciers": lambda: download_glaciers(cfg),
        "terrain": lambda: download_terrain(cfg),
        "fonts": lambda: download_fonts(cfg),
        "foen": lambda: download_stations(cfg),
        "uparea": lambda: download_uparea(cfg),
        "glofas": lambda: (probe_catalogue(), download_discharge(cfg, days)),
    }
    if optional or cfg.get("show_precip"):
        steps["meteo"] = lambda: download_meteo(cfg)
    if cfg["baseline"] == "longterm" or cfg["camels"]["enabled"]:
        from .download.camels import download_camels
        steps["camels"] = lambda: download_camels(cfg)
    chosen = only or set(steps) - {"meteo", "camels", "glofas"}
    # A plain `download` fetches the open layers. GloFAS is included when a key
    # exists, and refused with instructions when it does not.
    if only is None:
        chosen = {"boundaries", "rivers", "lakes", "glaciers", "terrain", "fonts", "foen", "uparea"}
        if cfg.get("show_precip"):
            chosen.add("meteo")
        try:
            from .download.glofas import require_cdsapirc
            require_cdsapirc()
            chosen.add("glofas")
        except DataSourceError as exc:
            print(exc)
            print("\nOpen layers will still download. Daily discharge waits on the key above.\n")
    for name in ("boundaries", "rivers", "lakes", "glaciers", "terrain", "fonts", "foen", "uparea", "meteo", "camels", "glofas"):
        if name in chosen:
            steps[name]()


_WORKER_SCENE = None


def _init_worker(config_path: str, layout: str) -> None:
    global _WORKER_SCENE
    _WORKER_SCENE = load_scene(load_config(config_path), layout)


def _paint_job(job: tuple[int, float, str]) -> str:
    day_index, fraction, output = job
    paint(_WORKER_SCENE, day_index, Path(output), fraction=fraction)
    return output


def _series_times(cfg: dict):
    import pandas as pd
    import xarray as xr

    from .settings import processed

    dataset = xr.open_dataset(processed(cfg, "series.nc"))
    times = pd.to_datetime(dataset.time.values)
    dataset.close()
    return times


def _render(cfg: dict, days: list[date], draft: bool, layouts: list[str]) -> None:
    frames_per_day = 1 if draft else int(cfg["frames"]["frames_per_day"])
    times = _series_times(cfg)
    # Eight full scenes at once swapped the machine out and the workers stalled.
    workers = min(3, os.cpu_count() or 2)
    os.environ["SWISS_QUIET"] = "1"
    for layout in layouts:
        folder = root(cfg) / "out" / "frames" / layout
        folder.mkdir(parents=True, exist_ok=True)
        jobs = []
        skipped = 0
        for day in days:
            index = _day_index(times, datetime(day.year, day.month, day.day))
            for frame in range(frames_per_day):
                # Frame 0 is the day itself. Later frames ease toward the next day.
                number = index * frames_per_day + frame
                dest = folder / f"frame_{number:05d}.png"
                if dest.exists() and dest.stat().st_size > 10_000:
                    skipped += 1
                    continue
                jobs.append((index, frame / frames_per_day, str(dest)))
        total = skipped + len(jobs)
        if not jobs:
            print(f"render: {layout} already complete ({skipped} frames)", flush=True)
            continue
        print(f"render: {len(jobs)} {layout} frames on {workers} processes ({skipped} already done)", flush=True)
        if len(jobs) >= 6:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_worker,
                initargs=(cfg["_path"], layout),
            ) as pool:
                done = skipped
                futures = [pool.submit(_paint_job, job) for job in jobs]
                for future in as_completed(futures):
                    future.result()
                    done += 1
                    if done % 25 == 0 or done == total:
                        print(f"  {layout} {done}/{total}", flush=True)
        else:
            scene = load_scene(cfg, layout)
            for day_index, fraction, output in jobs:
                paint(scene, day_index, Path(output), fraction=fraction)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="swiss-water-pulse")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    download = sub.add_parser("download")
    download.add_argument("--only", default=None, help="Comma-separated: boundaries,rivers,lakes,glaciers,terrain,foen,uparea,glofas,meteo,fonts")
    download.add_argument("--days", default=None)
    download.add_argument("--with-optional", action="store_true")

    process_cmd = sub.add_parser("process")
    process_cmd.add_argument("--preview", action="store_true", help="Build a mean-flow series so the page can be drawn before GloFAS arrives.")

    sub.add_parser("validate")

    still = sub.add_parser("still")
    still.add_argument("--date", required=True)
    still.add_argument("--draft", action="store_true")

    preview = sub.add_parser("preview")
    preview.add_argument("--date", default="2025-06-15")

    render = sub.add_parser("render")
    render.add_argument("--days", default=None)
    render.add_argument("--draft", action="store_true")
    render.add_argument("--layout", choices=["portrait", "square"], default=None)

    sub.add_parser("encode")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    try:
        if args.cmd == "download":
            only = set(args.only.split(",")) if args.only else None
            _download(cfg, only, _days(args.days, int(cfg["year"])), args.with_optional)
        elif args.cmd == "process":
            process(cfg, preview=args.preview)
        elif args.cmd == "validate":
            _validate(cfg)
        elif args.cmd == "preview":
            process(cfg, preview=True)
            day = date.fromisoformat(args.date)
            output = root(cfg) / "out" / "key_frames" / f"{day.isoformat()}_preview.png"
            scene = load_scene(cfg, "portrait")
            note = "Preview — ribbon height is mean flow, colour waits on GloFAS"
            paint(scene, _day_index(scene["times"], datetime(day.year, day.month, day.day)), output, preview_note=note)
        elif args.cmd == "still":
            layout = "draft" if args.draft else "portrait"
            output = root(cfg) / "out" / "key_frames" / f"{args.date}.png"
            scene = load_scene(cfg, layout)
            paint(scene, _day_index(scene["times"], datetime.fromisoformat(args.date)), output)
        elif args.cmd == "render":
            days = _days(args.days, int(cfg["year"]))
            if args.draft:
                layouts = ["draft"]
            elif args.layout:
                layouts = [args.layout]
            else:
                layouts = ["portrait", "square"]
            _render(cfg, days, draft=args.draft or bool(args.days), layouts=layouts)
        elif args.cmd == "encode":
            _encode(cfg)
    except DataSourceError as exc:
        raise SystemExit(f"\n{exc}\n") from exc


def _validate(cfg: dict) -> None:
    import geopandas as gpd
    import pandas as pd

    from .download.foen import load_dropped_series
    from .process.vectors import find_dataset

    station_zip = next((root(cfg) / "data" / "raw" / "foen").glob("*.zip"), None)
    if station_zip is None:
        raise DataSourceError("Station shapefile is missing. Download with --only foen.")
    stations = gpd.read_file(find_dataset(station_zip))
    print("FOEN station columns:", list(stations.columns))
    id_col = next((column for column in stations.columns if str(column).lower() in {"edv", "station", "id", "nr", "messstelle"}), None)
    if id_col is None:
        for column in stations.columns:
            if "nr" in str(column).lower() or "id" in str(column).lower():
                id_col = column
                break
    name_col = next((column for column in stations.columns if "name" in str(column).lower()), None)
    table = pd.DataFrame(
        {
            "station_id": stations[id_col].astype(str) if id_col else "",
            "name": stations[name_col].astype(str) if name_col else "",
        }
    )
    out = root(cfg) / "data" / "processed" / "foen_stations_request.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out, index=False)
    print(f"  wrote {out} ({len(table)} stations)")
    print("  Order daily means from https://www.bafu.admin.ch/en/hydrological-data-service-for-watercourses-and-lakes")
    print("  and save them as data/raw/foen/{station_id}_q.csv — see data/raw/foen/README.md")
    series = load_dropped_series(cfg)
    if series.empty:
        print("  No gauge CSVs yet, so there is no bias table. That is not a failed download.")
        return
    series_path = root(cfg) / "data" / "processed" / "series.nc"
    if not series_path.exists():
        raise DataSourceError("Gauge CSVs are present, but series.nc is not. Process GloFAS before validating.")
    import xarray as xr

    reaches = gpd.read_parquet(root(cfg) / "data" / "processed" / "reaches.parquet")
    dataset = xr.open_dataset(series_path)
    if dataset.attrs.get("source") != "glofas":
        raise DataSourceError("series.nc is a mean-flow preview, not GloFAS. Validation needs the real discharge.")
    from .process.validate import validation_table

    q = dataset.Q.values
    times = pd.to_datetime(dataset.time.values)
    stations = stations.to_crs(cfg["crs"])
    stations["station_id"] = stations[id_col].astype(str)
    stations["name"] = stations[name_col].astype(str) if name_col else ""
    result = validation_table(cfg, reaches, q, pd.DatetimeIndex(times), stations)
    if not result.empty:
        path = root(cfg) / "data" / "processed" / "validation.csv"
        result.to_csv(path, index=False)
        print(result.to_string(index=False))
        print(f"  wrote {path}")


def _encode(cfg: dict) -> None:
    ffmpeg = require_ffmpeg()
    year = cfg["year"]
    fps = int(cfg["frames"]["fps"])
    fast_fps = fps
    portrait = root(cfg) / "out" / "frames" / "portrait"
    square = root(cfg) / "out" / "frames" / "square"
    if not any(portrait.glob("frame_*.png")):
        raise DataSourceError("No portrait frames in out/frames/portrait. Render before encoding.")
    portrait_mp4 = root(cfg) / "out" / f"swiss_water_pulse_{year}_4x5.mp4"
    encode_sequence(ffmpeg, str(portrait / "frame_%05d.png"), portrait_mp4, fps)
    step = int(cfg["frames"]["frames_per_day"])
    encode_fast(ffmpeg, str(portrait / "frame_%05d.png"), root(cfg) / "out" / f"swiss_water_pulse_{year}_4x5_fast.mp4", step, fps)
    if any(square.glob("frame_*.png")):
        encode_sequence(ffmpeg, str(square / "frame_%05d.png"), root(cfg) / "out" / f"swiss_water_pulse_{year}_1x1.mp4", fast_fps)
        encode_fast(ffmpeg, str(square / "frame_%05d.png"), root(cfg) / "out" / f"swiss_water_pulse_{year}_1x1_fast.mp4", step, fps)
    encode_gif(ffmpeg, portrait_mp4, root(cfg) / "out" / "preview.gif")
    print("Frames are in out/frames/ and can be deleted once you are happy with the mp4.")


if __name__ == "__main__":
    main()

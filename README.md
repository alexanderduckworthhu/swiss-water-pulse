# Switzerland's Water Pulse

Every river in Switzerland, drawn once a day as a ribbon. Thickness is the square root of discharge, on one scale for the whole country, so the Rhine is wide and an alpine stream is a thread. Colour is that reach against its own normal: brown when it is about a tenth of usual, cream when it is usual, deep blue when it is about ten times usual.

The brief's title says 2026. Every filename, sample day, and GloFAS request in the brief says 2025, and 2025 is a finished year, so `year` in `config.yaml` is 2025. Change that one field and the subtitle, the files, and the download follow.

The year is also a page: [alexanderduckworthhu.github.io/swiss-water-pulse](https://alexanderduckworthhu.github.io/swiss-water-pulse/). The slider and the calendar both move to a day. The chat answers from the 2025 series.

## Setup

Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and ffmpeg.

```bash
cd swiss-water-pulse
uv sync
```

The Copernicus key is not in the repo. Create an account at <https://ewds.climate.copernicus.eu/>, accept the dataset terms on the [GloFAS historical form](https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical?tab=download), and write `~/.cdsapirc`:

```text
url: https://ewds.climate.copernicus.eu/api
key: <PERSONAL-ACCESS-TOKEN>
```

`cdsapi` 0.7 or newer reads that file. Nothing in it should be committed.

## Make targets

| Target | What it does |
|---|---|
| `make download` | Open layers: borders, FOEN river network, HydroRIVERS, lakes, glaciers, terrain tiles, fonts, GloFAS upstream area. GloFAS discharge too, if the key is present. |
| `make process` | Reach table, snapping, daily series, anomalies. |
| `make validate` | Writes the FOEN station list to request, and a bias table once gauge CSVs are in place. |
| `make preview` | One still of the page, using FOEN mean discharge for ribbon height. Colour stays neutral. This is for judging the drawing before the key exists. |
| `make still` | One real day, `out/key_frames/2025-06-15.png`, after GloFAS has been processed. |
| `make draft` | 1–10 June 2025 at half resolution, one frame a day. |
| `make render` | Portrait and square frames for the whole year. |
| `make encode` | H.264 mp4s (`-preset veryfast -crf 20`, 30 fps) and `out/preview.gif`. |
| `make test` | Unit tests for snapping, the colour ramp, and the credit line. |

A narrower pull:

```bash
uv run python -m src.cli download --only glofas --days 2025-06-15
uv run python -m src.cli render --days 2025-06-01:2025-06-10 --draft
```

Frames land in `out/frames/` and can be deleted after `make encode`.

## FOEN discharge and lake levels

Station locations download with the rest. A full year of daily means does not: order it from the [Hydrological Data Service](https://www.bafu.admin.ch/en/hydrological-data-service-for-watercourses-and-lakes). `make validate` writes `data/processed/foen_stations_request.csv`. The file format is in `data/raw/foen/README.md`. Drop `{station_id}_q.csv` and `{station_id}_level.csv` in that folder. Lake polygons stay on the middle colour of their ramp until the level files arrive. The film does not invent a lake anomaly.

## Changing the year, or the country

`config.yaml` holds the year, the longitude/latitude box, the outlet anchors, the city and river labels, the ribbon scale, and the colours. Geometry is projected to EPSG:2056. Another country needs a different mask, a different CRS, and its own outlet list. The snapping rule (match upstream area inside a 3×3, then a 5×5, reject anything off by more than 2×) does not assume Switzerland.

`baseline` is `year_median` by default. `climatology` wants GloFAS for 1991–2020 as well, which is a large download. `longterm` turns on CAMELS-CH (Zenodo record 7957061) and is off until you set it.

`show_precip: true` fetches MeteoSwiss RhiresD and TabsD for the year. The precipitation day is 06:00 UTC to 05:50 UTC the next day. The wash is off until you opt in.

`lakes.source: tlm3d` uses the full swissTLM3D extract, about 3 GB. The default is swissTLMRegio, which is the same office's topographic model at a scale that holds up on a 1080-pixel frame.

## What a run records

`data/processed/snap_stats.json` says how many reaches matched a GloFAS cell, how many were scaled from the next matched reach downstream, and the area-ratio of the accepted matches. Reaches that match keep that cell's discharge. Reaches that do not are marked interpolated: their discharge is the downstream match times the ratio of upstream areas.

The number under the date is the sum of the outlets in `config.yaml` (Rhine at Basel, Rhône at Chancy, Ticino at Lago Maggiore, Inn at Martina, Doubs at Ocourt). It is not a closed water balance.

Captions and callouts are sentences in `captions.yaml` and rules on the series. A callout is only emitted once at least 300 days are in the series, so a ten-day draft does not invent a flood.

## Known limitations

GloFAS v4 is a 0.05° grid, about 5 km. In the Alps a headwater often fails the 2× area test and is scaled from the next river downstream that did match. The area ratio keeps the ribbon thin, and it also misses a local storm that never reaches that downstream cell.

Lakes are regulated. The Aare below Thun and Brienz, the Rhône below Geneva, and the Rhine below Constance will not match a natural gauge even when the model is having a good day. The validation table is there so that bias stays visible. It is empty until the FOEN CSVs are added.

swissALTI3D is the elevation model that belongs on this map, and it is published as about forty thousand 1 km tiles. DHM25 is not in the live STAC catalogue (`ch.swisstopo.digitales-hoehenmodell_25` returns 404). The hillshade is Mapzen terrain tiles on AWS at the zoom in `config.yaml`, resampled to about 160 m in LV95. `SOURCES.md` has the wording for each licence. The footer is built from that file, and only the layers a run actually used are printed.

## Layout of the code

`src/download/` is one module per source. Each one checks the live endpoint and stops with the page to open if the id or the file has moved. `src/process/` snaps reaches, builds the daily array, and writes the anomaly. `src/render/` draws with Skia and encodes with ffmpeg. Caches live in `data/raw/` and `data/processed/`.

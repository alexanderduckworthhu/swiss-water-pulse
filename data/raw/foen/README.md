# FOEN daily series

Full-year historical discharge and lake level are not a bulk open download. Order them from the FOEN Hydrological Data Service:

<https://www.bafu.admin.ch/en/hydrological-data-service-for-watercourses-and-lakes>

Ask for **daily mean discharge** (m³/s) for the gauges you care about, for the film year (default 2025) and, if you want the long climatology, for 1991–2020. Ask for **daily mean lake level** (m a.s.l.) for Geneva, Constance, Neuchâtel, Maggiore, Lucerne, Zurich, Lugano, Thun, Brienz, Biel, Zug, and Walen.

`make validate` writes `data/processed/foen_stations_request.csv` from the open station shapefile so you can paste ids into that request.

## Expected CSV

One station per file. Put the files in this folder.

- Name: `{station_id}_{parameter}.csv`
- `station_id` is the FOEN number, for example `2289`
- `parameter` is `q` for discharge or `level` for a lake
- Separator: `;` or `,` (the reader tries `;` first)
- Encoding: UTF-8 or Windows-1252
- Missing values: empty field, `NA`, or `NaN`

A header row is required. Column names are matched case-insensitively, in English or German:

| Role | Accepted names |
|---|---|
| Date | `date`, `datum`, `time`, `zeit` |
| Value | `value`, `wert`, `q`, `discharge`, `abfluss`, `level`, `wasserstand`, `pegel` |
| Status (optional) | `status`, `release`, `freigabe` |

Dates may be `YYYY-MM-DD` or `DD.MM.YYYY`, with or without a time.

Example:

```text
datum;wert;status
01.01.2025;612.4;validated
02.01.2025;598.1;validated
03.01.2025;;validated
```

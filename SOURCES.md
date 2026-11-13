---
credits:
  - id: glofas
    short: "Contains modified Copernicus Emergency Management Service information 2026"
  - id: glofas_uparea
    short: "GloFAS upstream area, Copernicus EMS"
  - id: foen
    short: "Hydrology: Federal Office for the Environment FOEN"
  - id: swisstopo
    short: "© swisstopo"
  - id: glamos
    short: "Glaciers: GLAMOS, CC BY 4.0"
  - id: hydrorivers
    short: "River areas: HydroRIVERS / HydroSHEDS"
  - id: meteoswiss
    short: "Source: MeteoSwiss"
  - id: camels
    short: "Baseline: CAMELS-CH, CC BY 4.0"
  - id: terrain_aws
    short: "Terrain: Mapzen tiles via AWS Open Data"
copyright: "© 2026 Alexander Duckworth"
---

# Sources, licences, and the credit line

The footer on the film is built from the `credits` list above. Only the sources that a given run actually used are printed. Change the wording here if a licence notice changes, then re-render.

Checked against the live endpoints on 26 October 2026.

## glofas — GloFAS v4 historical reanalysis

- **Dataset:** `cems-glofas-historical` on the Copernicus Early Warning Data Store (EWDS).
- **Page:** <https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical>
- **API:** <https://ewds.climate.copernicus.eu/api> via `cdsapi` (key in `~/.cdsapirc`).
- **What we use:** operational system version `version_4_0`, LISFLOOD, daily mean river discharge. The EWDS form was updated on 29 July 2026: the discharge variable is `average_river_discharge_in_the_last_24_hours`, dates are `year` / `month` / `day`, and `timespan` is required (`time_mean` for discharge). Version 4 remains the operational historical set. Version 5 is pre-operational and is not the default.
- **Licence:** CEMS-FLOODS datasets licence and the terms of use of the CEMS Early Warning Data Store. Free access for reproduction, distribution, communication to the public, and adaptation. No warranty.
- **Attribution (modified data, which this film is):** `Contains modified Copernicus Emergency Management Service information [Year]`
- **Attribution (unmodified redistribution):** `Generated using Copernicus Emergency Management Service information [Year]`
- **Terms:** <https://ewds.climate.copernicus.eu/licences/cems-floods> and <https://ewds.climate.copernicus.eu/licences/terms-of-use-cems>
- **Registration:** an EWDS account, a personal access token, and a one-time acceptance of the dataset terms on the download form. Nothing is committed.

## glofas_uparea — GloFAS v4 upstream area

- **File:** `uparea_glofas_v4_0.nc` (global, 0.05°, upstream area in m²).
- **Page:** <https://confluence.ecmwf.int/spaces/CEMS/pages/242067380/Auxiliary+Data>
- **Use:** static map for reach-to-cell snapping. It matches GloFAS v4.0 forecasts and reanalysis from 26 July 2023 onward.
- **Licence and attribution:** same Copernicus EMS notice as the discharge fields.

## foen — FOEN / BAFU hydrology and the Swiss river network

- **Mean discharge on the river network:** STAC `ch.bafu.mittlere-abfluesse`, GeoPackage/FileGDB in EPSG:2056. Used as the Swiss river geometry and the observed mean-flow scale.
- **Gauging-station locations:** STAC `ch.bafu.hydrologie-hydromessstationen`.
- **Daily series and lake levels:** not a bulk open download for a full historical year. Order them from the Hydrological Data Service and drop the CSVs in `data/raw/foen/`. Current raw values are published via hydrodaten.admin.ch and LINDAS; this project does not scrape the live feed.
- **Service:** <https://www.bafu.admin.ch/en/hydrological-data-service-for-watercourses-and-lakes>
- **Licence:** terms for the supply and use of FOEN hydrological data (23 October 2024, reference 23-1249). The data may be used for commercial or non-commercial purposes. Citation of the source is recommended. No warranty. Unusually costly extracts may be charged.
- **Terms:** <https://www.bafu.admin.ch/dam/en/sd-web/g7vjiKP5LJ11/liefer-nutzungsbedingungen-hydrologische-daten.pdf>
- **Geodata** on data.geo.admin.ch follow the federal open-data terms. Cite the Federal Office for the Environment FOEN.

## swisstopo — borders, lakes, and the national map

- **Country and cantons:** STAC `ch.swisstopo.swissboundaries3d`, release `swissboundaries3d_2026-01`, GeoPackage, EPSG:2056.
- **Lakes:** standing water in STAC `ch.swisstopo.swisstlmregio`, release `swisstlmregio_2026`. This is the swisstopo topographic model at Regio scale. The full `ch.swisstopo.swisstlm3d` extract (release `swisstlm3d_2026-02`) is the more precise water layer and is about 3.6 GB as a shapefile or 2.9 GB as a FileGDB; set `lakes.source: tlm3d` to use it.
- **DHM25:** there is no live STAC collection. `ch.swisstopo.digitales-hoehenmodell_25` returned HTTP 404 on 26 October 2026. `ch.swisstopo.swissalti3d` is published as 1 km tiles (2 m or 0.5 m), on the order of forty thousand files for the country. The film therefore uses the terrain fallback below and says so in the log.
- **Licence:** swisstopo OGD terms. Free to use, distribute, adapt, and use commercially. A source notice is mandatory: `Source: Federal Office of Topography swisstopo` or `© swisstopo`.
- **Terms:** <https://www.swisstopo.admin.ch/en/faq-free-geodata>

## glamos — Swiss Glacier Inventory 2016

- **Data:** glacier outlines, SGI2016, release 2020. Shapefile.
- **Download:** <https://doi.glamos.ch/data/inventory/inventory_sgi2016_r2020.zip>
- **DOI:** <https://doi.org/10.18750/inventory.sgi2016.r2020>
- **Licence:** Creative Commons Attribution 4.0 International (CC BY 4.0). GLAMOS products are open. Outlines derived from swisstopo imagery remain subject to swisstopo's terms as well; the `© swisstopo` credit covers that.
- **Citation:** Linsbauer, A., Huss, M., Hodel, E., Bauder, A., Fischer, M., Weidmann, Y., Bärtschi, H. and Schmassmann, E. (2021). The new Swiss Glacier Inventory SGI2016: from a topographical to a glaciological dataset. Frontiers in Earth Science. <https://doi.org/10.3389/feart.2021.704189>
- **Policy:** <https://doi.glamos.ch/GLAMOS_publication_and_data_policy.pdf>

## hydrorivers — HydroRIVERS v1.0

- **Data:** Europe and Middle East shapefile, 15 arc-second reaches, including `UPLAND_SKM` and `NEXT_DOWN`.
- **Download:** <https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_eu_shp.zip>
- **Page:** <https://www.hydrosheds.org/products/hydrorivers>
- **Licence:** the HydroSHEDS licence. Free for scientific, educational, and commercial use. By downloading, the user accepts that licence. Warranty is disclaimed.
- **Citation:** Lehner, B. and Grill, G. (2013). Global river hydrography and network routing: baseline data and new approaches to study the world’s large river systems. Hydrological Processes, 27(15): 2171–2186. Data available at <https://www.hydrosheds.org>.
- **Role here:** upstream area and downstream links for snapping. If the FOEN river network cannot be routed, these lines are also the drawn network, and the log says so.

## meteoswiss — gridded precipitation and temperature

- **Catalogue:** <https://data.geo.admin.ch/api/stac/v1/collections>
- **Collection:** `ch.meteoschweiz.ogd-surface-derived-grid`
- **What we use:** final daily precipitation `RhiresD` and daily mean temperature `TabsD` for the configured year, from the STAC item `archive-ch` when it is present (asset names contain `rhiresd` and `tabsd`). The downloader discovers this at runtime.
- **Station files, if you add them:** semicolon-separated, Windows-1252, empty fields mean missing. A precipitation day runs from 06:00 UTC to 05:50 UTC the next day.
- **Licence:** CC BY 4.0. Unrestricted reuse, including commercial use. Reproductions must not imply a MeteoSwiss endorsement.
- **Attribution:** `Source: MeteoSwiss` (also Quelle: MeteoSchweiz / Source: MétéoSuisse / Fonte: MeteoSvizzera).
- **Terms:** <https://opendatadocs.meteoswiss.ch/general/terms-of-use>

## camels — CAMELS-CH (optional long-term baseline)

- **Record requested in the brief:** Zenodo 7957061 (version 0.6). A later version, 0.9, is record 15025258. The downloader pins 7957061 unless `camels.record` is changed.
- **DOI:** <https://doi.org/10.5281/zenodo.7957061>
- **Concept DOI (always the latest):** <https://doi.org/10.5281/zenodo.7784632>
- **Licence:** Creative Commons Attribution 4.0 International.
- **Citation:** Höge, M., Kauzlaric, M., Siber, R., Schönenberger, U., Horton, P., Schwanbeck, J., Floriancic, M. G., Viviroli, D., Wilhelm, S., Sikorska-Senoner, A. E., and co-authors. CAMELS-CH: hydro-meteorological time series and landscape attributes for 331 catchments in hydrologic Switzerland. Earth System Science Data. Data: Zenodo 7957061.
- **Role:** per-gauge medians when `baseline: longterm`. It is not downloaded unless that mode is on.

## terrain_aws — Mapzen terrain tiles on AWS (fallback)

- **Tiles:** `s3://elevation-tiles-prod/geotiff/{z}/{x}/{y}.tif`, requested as `https://s3.amazonaws.com/elevation-tiles-prod/geotiff/{z}/{x}/{y}.tif`.
- **Why it is the fallback:** DHM25 is not in the live STAC API, and swissALTI3D at 2 m is tens of thousands of tiles. The zoom in `config.yaml` lands near 100–200 m, which is the working resolution of the hillshade.
- **Licence:** mixed. The AWS Open Data registry redistributes Mapzen / Tilezen terrain tiles. Underlying models include SRTM, GMTED, and national elevation datasets with their own notices (public domain and CC BY among them). See <https://registry.opendata.aws/terrain-tiles/> and <https://github.com/tilezen/joerd/blob/master/docs/data-sources.md>.
- **Attribution used on the film:** `Terrain: Mapzen tiles via AWS Open Data`.

## Fonts

Source Serif 4 and Source Sans 3, Adobe, SIL Open Font License 1.1. They are downloaded into `data/raw/fonts/` and are not data layers. If the font host is down, the renderer falls back to Georgia and Helvetica and writes that into the run log.

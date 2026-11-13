from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from ..errors import DataSourceError
from ..settings import raw
from .http import download, get_json, session

CDSAPI_HELP = """
GloFAS daily discharge is on the Copernicus Early Warning Data Store, and this
machine has no API key.

1. Create an account at https://ewds.climate.copernicus.eu/ (or the Copernicus
   login the EWDS page sends you to).
2. Open your profile and copy a personal access token.
3. Open the dataset form
   https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical?tab=download
   scroll to the terms (CEMS-FLOODS datasets licence) and accept them.
   The API refuses the download until you have done this once in the browser.
4. Write this file, with your token, and do not commit it:

   ~/.cdsapirc
   url: https://ewds.climate.copernicus.eu/api
   key: <PERSONAL-ACCESS-TOKEN>

5. Re-run:  uv run python -m src.cli download --only glofas --days 2025-06-15

Nothing else was put in its place. The ribbon film needs this field.
""".strip()


def cdsapirc_path() -> Path:
    return Path(os.environ.get("CDSAPI_RC", Path.home() / ".cdsapirc"))


def require_cdsapirc() -> Path:
    path = cdsapirc_path()
    if not path.exists():
        raise DataSourceError(CDSAPI_HELP)
    text = path.read_text()
    if "ewds.climate.copernicus.eu" not in text and "cds.climate.copernicus.eu" not in text:
        raise DataSourceError(
            f"{path} exists but its url is not the EWDS or CDS API.\n"
            "GloFAS historical lives on https://ewds.climate.copernicus.eu/api\n\n"
            + CDSAPI_HELP
        )
    if re.search(r"key:\s*$", text, re.M) or "PASTE" in text or "<" in text.split("key:", 1)[-1][:40]:
        raise DataSourceError(f"{path} has an empty or placeholder key.\n\n" + CDSAPI_HELP)
    return path


def _month_groups(days: list[date]) -> list[tuple[int, int, list[str]]]:
    groups: dict[tuple[int, int], list[str]] = {}
    for day in days:
        groups.setdefault((day.year, day.month), []).append(f"{day.day:02d}")
    return [(year, month, sorted(set(doms))) for (year, month), doms in sorted(groups.items())]


def _requests_for(cfg: dict, year: int, month: int, doms: list[str], product: str) -> list[dict]:
    """Current EWDS shape first, then the pre-July-2026 shape, so a rename fails in the open."""
    area = cfg["bbox"]
    area_list = [area["north"], area["west"], area["south"], area["east"]]
    current = {
        "system_version": [cfg["glofas"]["system_version"]],
        "hydrological_model": [cfg["glofas"]["hydrological_model"]],
        "product_type": [product],
        "variable": ["average_river_discharge_in_the_last_24_hours"],
        "timespan": ["time_mean"],
        "year": [str(year)],
        "month": [f"{month:02d}"],
        "day": doms,
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": area_list,
    }
    legacy = {
        "system_version": cfg["glofas"]["system_version"],
        "hydrological_model": cfg["glofas"]["hydrological_model"],
        "product_type": product,
        "variable": "river_discharge_in_the_last_24_hours",
        "hyear": str(year),
        "hmonth": f"{month:02d}",
        "hday": doms,
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": area_list,
    }
    return [current, legacy]


def download_discharge(cfg: dict, days: list[date]) -> list[Path]:
    print("glofas: daily discharge")
    require_cdsapirc()
    try:
        import cdsapi
    except ImportError as exc:
        raise DataSourceError("cdsapi is not installed. Run `uv sync` in the project.") from exc

    client = cdsapi.Client()
    saved: list[Path] = []
    errors: list[str] = []
    for year, month, doms in _month_groups(days):
        dest = raw(cfg, "glofas", f"discharge_{year}{month:02d}_{doms[0]}-{doms[-1]}.nc")
        if dest.exists() and dest.stat().st_size > 10_000:
            print(f"  have {dest.name}")
            saved.append(dest)
            continue
        got = False
        for product in cfg["glofas"]["product_types"]:
            for request in _requests_for(cfg, year, month, doms, product):
                print(f"  requesting {year}-{month:02d} product={product} format={request.get('data_format')}")
                try:
                    client.retrieve(cfg["glofas"]["dataset"], request, str(dest))
                except Exception as exc:  # cdsapi raises its own Exception subclasses
                    errors.append(f"{product} / {list(request)[:6]}: {exc}")
                    print(f"  rejected: {exc}")
                    continue
                if dest.exists() and dest.stat().st_size > 10_000:
                    note = dest.with_suffix(".product")
                    note.write_text(product + "\n")
                    print(f"  saved {dest.name} using product_type={product}")
                    saved.append(dest)
                    got = True
                    break
            if got:
                break
        if not got:
            raise DataSourceError(
                "GloFAS refused every request shape for "
                f"{year}-{month:02d}.\n"
                "The EWDS form changed, the year is not published yet, or the licence "
                "was not accepted in the browser.\n"
                "Dataset: https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical?tab=download\n"
                "What the API said:\n- " + "\n- ".join(errors[-6:])
            )
    return saved


def download_uparea(cfg: dict) -> Path:
    print("glofas: upstream-area static map")
    url = cfg["glofas"]["uparea_url"]
    dest = raw(cfg, "glofas", "uparea_glofas_v4_0.nc")
    response = session().head(url, timeout=40, allow_redirects=True)
    if response.status_code >= 400:
        page = cfg["glofas"]["uparea_page"]
        html = session().get(page, timeout=60).text
        found = re.findall(r'href="([^"]*uparea_glofas_v4_0\.nc[^"]*)"', html)
        if not found:
            raise DataSourceError(
                "The GloFAS v4 upstream-area file is not at the URL in config.yaml, "
                f"and {page} no longer links to uparea_glofas_v4_0.nc.\n"
                "Open that page, copy the current 'uparea_glofas_v4_0.nc' link into "
                "config.yaml glofas.uparea_url, and run the download again."
            )
        href = found[0].replace("&amp;", "&")
        if href.startswith("/"):
            href = "https://confluence.ecmwf.int" + href
        print(f"  configured URL failed; the wiki now points at {href}")
        url = href
    return download(url, dest, timeout=300)


def probe_catalogue() -> None:
    """Fail loudly if the dataset id itself has disappeared."""
    url = "https://ewds.climate.copernicus.eu/api/catalogue/v1/collections/cems-glofas-historical"
    try:
        payload = get_json(url)
    except DataSourceError as exc:
        raise DataSourceError(
            "Could not read the EWDS catalogue entry for cems-glofas-historical.\n"
            f"{exc}\n"
            "Check https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical"
        ) from exc
    if not payload:
        raise DataSourceError("EWDS returned an empty catalogue entry for cems-glofas-historical.")

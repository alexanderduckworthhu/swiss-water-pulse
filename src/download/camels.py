from __future__ import annotations

from pathlib import Path

from ..errors import DataSourceError
from ..settings import raw
from .http import download, head_ok


def download_camels(cfg: dict) -> Path:
    if not cfg["camels"]["enabled"] and cfg["baseline"] != "longterm":
        raise DataSourceError(
            "CAMELS-CH is only fetched when baseline is 'longterm' or camels.enabled is true. "
            "It is about 250 MB and is not required for the year-median film."
        )
    record = str(cfg["camels"]["record"])
    url = f"https://zenodo.org/records/{record}/files/camels_ch.zip?download=1"
    page = f"https://zenodo.org/records/{record}"
    print(f"camels: Zenodo {record}")
    if not head_ok(page):
        raise DataSourceError(
            f"Zenodo record {record} did not respond at {page}. "
            "CAMELS-CH may have moved. The concept DOI https://doi.org/10.5281/zenodo.7784632 "
            "always points at the latest version; put that record number in config.yaml camels.record."
        )
    dest = raw(cfg, "camels", f"camels_ch_{record}.zip")
    try:
        return download(url, dest, timeout=600)
    except DataSourceError as exc:
        raise DataSourceError(
            f"Could not download camels_ch.zip from record {record}.\n{exc}\n"
            f"Open {page} and confirm the file is still named camels_ch.zip."
        ) from exc

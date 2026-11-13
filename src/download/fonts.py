from __future__ import annotations

import zipfile
from pathlib import Path

from ..settings import raw
from .http import download

# Adobe's OFL release. If this tag moves, the renderer falls back to Georgia.
FONT_ZIP = "https://github.com/adobe-fonts/source-serif/releases/download/4.005R/source-serif-4.005_Desktop.zip"
SANS_ZIP = "https://github.com/adobe-fonts/source-sans/releases/download/3.052R/OTF-source-sans-3.052R.zip"

WANTED = {
    "SourceSerif4-Regular.otf": "serif",
    "SourceSerif4-Semibold.otf": "serif_semibold",
    "SourceSerif4-It.otf": "serif_italic",
    "SourceSans3-Regular.otf": "sans",
    "SourceSans3-Semibold.otf": "sans_semibold",
    "SourceSans3-It.otf": "sans_italic",
}


def download_fonts(cfg: dict) -> Path:
    folder = Path(cfg["_root"]) / "data" / "raw" / "fonts"
    folder.mkdir(parents=True, exist_ok=True)
    if all((folder / name).exists() for name in WANTED):
        print("fonts: already present")
        return folder
    print("fonts: Source Serif 4 and Source Sans 3 (SIL OFL)")
    for url in (FONT_ZIP, SANS_ZIP):
        dest = raw(cfg, "fonts", Path(url).name)
        try:
            download(url, dest, timeout=120)
        except Exception as exc:  # noqa: BLE001
            print(f"  font host failed ({exc}). Georgia and Helvetica will be used.")
            return folder
        with zipfile.ZipFile(dest) as archive:
            for info in archive.infolist():
                name = Path(info.filename).name
                if name in WANTED:
                    target = folder / name
                    with archive.open(info) as src, target.open("wb") as out:
                        out.write(src.read())
                    print(f"  {name}")
    return folder

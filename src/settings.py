from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config.yaml"
    with path.open() as handle:
        cfg = yaml.safe_load(handle)
    cfg["_path"] = str(path)
    cfg["_root"] = str(path.parent)
    return cfg


def root(cfg: dict) -> Path:
    return Path(cfg["_root"])


def raw(cfg: dict, *parts: str) -> Path:
    path = root(cfg) / "data" / "raw"
    for part in parts:
        path = path / part
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def processed(cfg: dict, name: str) -> Path:
    path = root(cfg) / "data" / "processed" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_captions(cfg: dict) -> dict:
    path = root(cfg) / "captions.yaml"
    with path.open() as handle:
        return yaml.safe_load(handle)

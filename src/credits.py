from __future__ import annotations

from pathlib import Path

import yaml

from .settings import ROOT


def load_credit_table(path: Path | None = None) -> dict:
    text = (path or ROOT / "SOURCES.md").read_text()
    if not text.startswith("---"):
        raise RuntimeError("SOURCES.md is missing the credits block at the top.")
    end = text.find("\n---", 3)
    if end < 0:
        raise RuntimeError("SOURCES.md credits block is not closed.")
    data = yaml.safe_load(text[3:end])
    if not data or "credits" not in data:
        raise RuntimeError("SOURCES.md credits block has no `credits` list.")
    return data


def footer_line(used_ids: list[str], path: Path | None = None, copyright_text: str | None = None) -> str:
    table = load_credit_table(path)
    by_id = {item["id"]: item["short"] for item in table["credits"]}
    missing = [item for item in used_ids if item not in by_id]
    if missing:
        raise RuntimeError(
            "These source ids are used in the film but have no credit in SOURCES.md: "
            + ", ".join(missing)
        )
    parts = [by_id[item] for item in used_ids]
    notice = copyright_text if copyright_text is not None else table.get("copyright", "")
    if notice:
        parts.append(notice)
    return "  ·  ".join(parts)

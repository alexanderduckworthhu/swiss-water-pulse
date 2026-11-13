"""Choose the season line and any callout from the data, using captions.yaml."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _season(rules: dict, day: pd.Timestamp, fields: dict) -> str:
    month = int(day.month)
    for rule in rules.get("seasons", []):
        if month not in rule.get("months", []):
            continue
        kind = rule.get("when", "always")
        threshold = float(rule.get("threshold", 0))
        if kind == "always":
            return rule["text"]
        if kind == "alpine_anomaly_above" and fields["alpine"] >= threshold:
            return rule["text"]
        if kind == "rhone_anomaly_above" and fields["rhone"] >= threshold:
            return rule["text"]
        if kind == "national_anomaly_below" and fields["national"] <= threshold:
            return rule["text"]
    return rules.get("default", "")


def assign_river_names(labels, next_down) -> np.ndarray:
    """Carry each labelled reach upstream and downstream along the network.

    Only eleven map labels exist. A flood on an unlabelled Rhine segment
    should still be called the Rhine, and a tributary that joins it takes
    that name unless it has a label of its own.
    """
    names = np.array(["" if not text or str(text).lower() == "nan" else str(text) for text in labels], dtype=object)
    nxt = np.asarray(next_down, dtype=int)
    count = len(names)
    for index, text in enumerate(list(names)):
        if not text:
            continue
        current = int(nxt[index])
        seen = {index}
        while 0 <= current < count and current not in seen and not names[current]:
            names[current] = text
            seen.add(current)
            current = int(nxt[current])
    incoming: list[list[int]] = [[] for _ in range(count)]
    for index, target in enumerate(nxt):
        target = int(target)
        if 0 <= target < count and target != index:
            incoming[target].append(index)
    queue = [index for index, text in enumerate(names) if text]
    head = 0
    while head < len(queue):
        current = queue[head]
        head += 1
        for child in incoming[current]:
            if not names[child]:
                names[child] = names[current]
                queue.append(child)
    return names


_THRESHOLDS: dict[tuple, np.ndarray] = {}


def _threshold(q_all: np.ndarray, percentile: float) -> np.ndarray:
    key = (id(q_all), q_all.shape, float(percentile))
    cached = _THRESHOLDS.get(key)
    if cached is None:
        cached = np.nanpercentile(q_all, percentile * 100, axis=1)
        _THRESHOLDS[key] = cached
    return cached


def _callout_text(template: str, river: str, place: str) -> str:
    if "{place}" in template and place and place.lower() != "nan":
        return template.format(river=river, place=place)
    if " near " in template:
        return template.split(" near ", 1)[0].format(river=river)
    return template.format(river=river)


def _callout(rules: dict, reaches, anomaly_today: np.ndarray, q_today: np.ndarray, q_all: np.ndarray) -> dict | None:
    if q_all.shape[1] < 300:
        return None
    if "river_name" in reaches.columns:
        names = reaches["river_name"].astype(str).to_numpy()
    elif "next_down" in reaches.columns and "label" in reaches.columns:
        names = assign_river_names(reaches["label"].tolist(), reaches["next_down"].to_numpy())
    else:
        names = reaches["label"].astype(str).to_numpy() if "label" in reaches.columns else np.array([""] * len(reaches))
    places = reaches["place"].astype(str).to_numpy() if "place" in reaches.columns else np.array([""] * len(reaches))
    areas_all = reaches["area_km2"].to_numpy(dtype=float)
    for rule in rules.get("callouts", []):
        minimum = float(rule.get("min_area_km2", 0))
        percentile = float(rule["percentile"])
        eligible = areas_all >= minimum
        limit = _threshold(q_all, percentile)
        if rule["when"] == "reach_above_percentile":
            hits = eligible & np.isfinite(q_today) & (q_today >= limit) & (limit > 0)
        elif rule["when"] == "reach_below_percentile":
            hits = eligible & np.isfinite(q_today) & (q_today <= limit)
        else:
            continue
        if not hits.any():
            continue
        # Prefer a named river. An unlabelled hit would otherwise read "River near the gauge".
        areas = np.where(hits, areas_all, -1.0)
        for index in np.argsort(areas)[::-1]:
            if areas[index] < 0:
                break
            name = str(names[index] or "")
            if not name or name.lower() == "nan":
                continue
            return {
                "text": _callout_text(rule["template"], name, str(places[index] or "")),
                "reach": int(index),
            }
    return None


def caption_for_day(rules, reaches, day, anomaly, q, q_all) -> dict:
    alpine_mask = reaches["alpine"].to_numpy(dtype=bool) if "alpine" in reaches.columns else np.zeros(len(reaches), dtype=bool)
    alpine = float(np.nanmedian(anomaly[alpine_mask])) if alpine_mask.any() else 0.0
    if "outlet_rhone" in reaches.columns and reaches["outlet_rhone"].any():
        rhone = float(anomaly[reaches["outlet_rhone"].to_numpy()][0])
    else:
        rhone = 0.0
    big = reaches["area_km2"].to_numpy(dtype=float) >= 200
    national = float(np.nanmedian(anomaly[big])) if big.any() else 0.0
    text = _season(rules, day, {"alpine": alpine, "rhone": rhone, "national": national})
    return {"season": text, "callout": _callout(rules, reaches, anomaly, q, q_all)}

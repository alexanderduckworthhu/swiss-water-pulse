from __future__ import annotations

def rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = min(1.0, max(0.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def anomaly_rgb(value: float, low: tuple[int, int, int], mid: tuple[int, int, int], high: tuple[int, int, int]):
    """Log-anomaly in [-1, 1]. The cream band is wide on purpose."""
    value = max(-1.0, min(1.0, float(value)))
    curved = abs(value) ** 0.75
    if value < 0:
        return mix(mid, low, curved)
    return mix(mid, high, curved)

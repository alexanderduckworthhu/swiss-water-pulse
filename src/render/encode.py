"""Encode PNG sequences with ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..errors import DataSourceError


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise DataSourceError(
            "ffmpeg is not on PATH. Install it with `brew install ffmpeg` and run the encode step again."
        )
    return path


def encode_sequence(ffmpeg: str, pattern: str, output: Path, fps: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", pattern,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise DataSourceError(f"ffmpeg failed for {output.name}:\n{result.stderr[-1500:]}")
    print(f"  wrote {output}")


def encode_fast(ffmpeg: str, pattern: str, output: Path, frames_per_day: int, fps: int) -> None:
    """One frame per day, so the year plays in about twelve seconds."""
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", pattern,
        "-vf", f"select='not(mod(n\\,{frames_per_day}))',setpts=N/{fps}/TB",
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise DataSourceError(f"ffmpeg failed for {output.name}:\n{result.stderr[-1500:]}")
    print(f"  wrote {output}")


def encode_gif(ffmpeg: str, source: Path, output: Path) -> None:
    palette = output.with_suffix(".palette.png")
    filters = "fps=8,scale=480:-1:flags=lanczos"
    gen = subprocess.run(
        [ffmpeg, "-y", "-i", str(source), "-vf", f"{filters},palettegen", str(palette)],
        capture_output=True, text=True,
    )
    if gen.returncode != 0:
        raise DataSourceError(f"ffmpeg palette failed:\n{gen.stderr[-800:]}")
    use = subprocess.run(
        [
            ffmpeg, "-y", "-i", str(source), "-i", str(palette),
            "-lavfi", f"{filters} [x]; [x][1:v] paletteuse",
            str(output),
        ],
        capture_output=True, text=True,
    )
    palette.unlink(missing_ok=True)
    if use.returncode != 0:
        raise DataSourceError(f"ffmpeg gif failed:\n{use.stderr[-800:]}")
    print(f"  wrote {output}")

from __future__ import annotations

import json
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..errors import DataSourceError

_SESSION: requests.Session | None = None


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        retry = Retry(
            total=5,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        sess = requests.Session()
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        sess.headers["User-Agent"] = "swiss-water-pulse/0.1 (research; contact via local project)"
        _SESSION = sess
    return _SESSION


def _etag_path(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".etag")


def download(url: str, dest: Path, timeout: int = 180) -> Path:
    """Download url to dest. Sends If-None-Match when we already have a body."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {}
    etag_file = _etag_path(dest)
    if dest.exists() and dest.stat().st_size > 1000 and etag_file.exists():
        headers["If-None-Match"] = etag_file.read_text().strip()
    elif dest.exists() and dest.stat().st_size > 1000:
        head = session().head(url, timeout=40, allow_redirects=True)
        length = head.headers.get("Content-Length")
        if length and int(length) == dest.stat().st_size:
            print(f"  have {dest.name}")
            return dest

    response = session().get(url, headers=headers, timeout=timeout, stream=True)
    if response.status_code == 304 and dest.exists():
        print(f"  unchanged {dest.name}")
        response.close()
        return dest
    if response.status_code >= 400:
        body = ""
        try:
            body = response.text[:500]
        except Exception:
            body = ""
        raise DataSourceError(
            f"Download failed ({response.status_code}) for {url}\n{body}\n"
            "The endpoint may have moved. The page cited in SOURCES.md is the place to look."
        )

    part = dest.with_suffix(dest.suffix + ".part")
    with part.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if chunk:
                handle.write(chunk)
    part.replace(dest)
    etag = response.headers.get("ETag")
    if etag:
        etag_file.write_text(etag)
    print(f"  saved {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def get_json(url: str, timeout: int = 60) -> dict:
    response = session().get(url, timeout=timeout)
    if response.status_code >= 400:
        raise DataSourceError(
            f"Request failed ({response.status_code}) for {url}\n{response.text[:400]}"
        )
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise DataSourceError(f"{url} did not return JSON. First bytes: {response.text[:200]}") from exc


def head_ok(url: str) -> bool:
    try:
        response = session().head(url, timeout=30, allow_redirects=True)
    except requests.RequestException:
        return False
    return response.status_code < 400


def polite_sleep(seconds: float) -> None:
    time.sleep(seconds)

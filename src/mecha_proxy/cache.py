"""Local download cache (portable: lives next to the tool, see paths.py).

Images are fetched once and reused. Re-checking with the server only happens on
an explicit refresh, and then uses conditional requests (ETag/Last-Modified) so
unchanged images are never downloaded again (server answers 304).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from . import config
from .paths import app_home


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Cache:
    """home/cards holds the downloaded images; home/.cache holds everything else."""

    def __init__(self, home: Path | None = None, session: requests.Session | None = None,
                 delay_s: float = config.REQUEST_DELAY_S, log=print):
        self.root = Path(home) if home else app_home()
        self.images_dir = self.root / "cards"
        self.meta_dir = self.root / ".cache"
        self.processed_dir = self.meta_dir / "processed"
        self.backs_dir = self.meta_dir / "backs"
        self.index_path = self.meta_dir / "index.json"
        self.manifest_path = self.meta_dir / "manifest.json"
        for d in (self.images_dir, self.processed_dir, self.backs_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        # assign, don't setdefault: requests.Session ships its own python-requests UA
        self.session.headers["User-Agent"] = config.USER_AGENT
        self.delay_s = delay_s
        self.log = log
        self._last_request = 0.0
        self.stats = {"downloaded": 0, "not_modified": 0, "cached": 0, "missing": 0}
        self.index: dict[str, dict] = self._load_index()

    # ---- index -------------------------------------------------------------
    def _load_index(self) -> dict:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.log(f"warning: cache index unreadable, rebuilding: {self.index_path}")
        return {}

    def save_index(self) -> None:
        atomic_write(self.index_path, json.dumps(self.index, indent=1, sort_keys=True).encode())

    # ---- network -----------------------------------------------------------
    def _throttle(self) -> None:
        wait = self.delay_s - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get_text(self, url: str) -> str:
        self._throttle()
        resp = self.session.get(url, timeout=config.TIMEOUT_S)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        return resp.text

    # ---- images ------------------------------------------------------------
    def local_path(self, url: str, rel: str | None = None) -> Path:
        """Where an image lives: images/<rel> (e.g. unit/EX02/EX02-U03 Name.webp)."""
        return self.images_dir / (rel or Path(urlparse(url).path).name)

    def _prune_empty_dirs(self, start: Path) -> None:
        d = start
        while d != self.images_dir and d.is_relative_to(self.images_dir):
            try:
                d.rmdir()  # only succeeds when empty
            except OSError:
                break
            d = d.parent

    def cached(self, url: str, rel: str | None = None) -> Path | None:
        """Return the cached file for url if present and intact, without network.

        If the file sits somewhere other than rel (older flat layout, or the card
        was renamed on the site), it is moved into place instead of re-downloaded.
        """
        entry = self.index.get(url)
        if not entry or entry.get("missing") or not entry.get("file"):
            return None
        path = self.images_dir / entry["file"]
        if not path.exists():
            return None
        if entry.get("sha256") != sha256_file(path):
            self.log(f"warning: cached file changed on disk, will re-download: {entry['file']}")
            return None
        if rel and Path(entry["file"]) != Path(rel):
            target = self.local_path(url, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
            self._prune_empty_dirs(path.parent)
            entry["file"] = Path(rel).as_posix()
            path = target
        return path

    def is_known_missing(self, url: str) -> bool:
        return bool(self.index.get(url, {}).get("missing"))

    def fetch_image(self, url: str, refresh: bool = False, rel: str | None = None) -> Path | None:
        """Get an image, downloading only if not cached (or changed, when refresh=True).

        rel is the path under images/ to store it at. Returns None if the server
        says the image does not exist (404).
        """
        path = self.cached(url, rel)
        if path and not refresh:
            self.stats["cached"] += 1
            return path
        if not refresh and self.is_known_missing(url):
            self.stats["missing"] += 1
            return None

        entry = self.index.get(url, {})
        headers = {}
        if path:  # only send validators if we actually hold the bytes
            if entry.get("etag"):
                headers["If-None-Match"] = entry["etag"]
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = entry["last_modified"]

        self._throttle()
        resp = self.session.get(url, headers=headers, timeout=config.TIMEOUT_S)

        if resp.status_code == 304 and path:
            entry["checked_at"] = _now()
            self.index[url] = entry
            self.stats["not_modified"] += 1
            return path
        if resp.status_code == 404:
            self.index[url] = {"missing": True, "checked_at": _now()}
            self.stats["missing"] += 1
            return None
        resp.raise_for_status()

        data = resp.content
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
        except Exception as exc:  # noqa: BLE001 - any decode failure means bad payload
            raise RuntimeError(f"server returned a non-image for {url}") from exc

        target = self.local_path(url, rel)
        atomic_write(target, data)
        old = entry.get("file")
        if old and Path(old) != Path(target.relative_to(self.images_dir)):
            stale = self.images_dir / old  # updated image under a new name: drop the old copy
            stale.unlink(missing_ok=True)
            self._prune_empty_dirs(stale.parent)
        self.index[url] = {
            "file": target.relative_to(self.images_dir).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
            "fetched_at": _now(),
            "checked_at": _now(),
        }
        self.stats["downloaded"] += 1
        return target

    # ---- manifest ----------------------------------------------------------
    def load_manifest(self) -> dict | None:
        if not self.manifest_path.exists():
            return None
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def save_manifest(self, manifest: dict) -> None:
        atomic_write(self.manifest_path, json.dumps(manifest, indent=1).encode())

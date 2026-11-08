"""Where the tool keeps its files.

Portable layout, next to the program:

  <home>/cards/<type>/<SET>/<CODE> <Name>.webp   downloaded images (browse these)
  <home>/.cache/                                 index, card list, resized copies, backs

<home> is, in order of preference:
  1. --home on the command line, or the MECHA_PROXY_HOME environment variable
  2. the folder containing the .exe (packaged Windows builds), if it's writable
  3. the project folder (running from a source checkout)
  4. Documents/Mecha-Assemble-Proxy-Helper (the macOS app – a .app bundle is no place for
     user files – an unwritable install folder, or an install into site-packages)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from platformdirs import user_cache_dir, user_documents_dir

LEGACY_APP_DIR = "MechaAssembleProxyHelper"  # v0.1.0/0.1.1 kept everything in the OS cache dir


def app_home(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("MECHA_PROXY_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):  # PyInstaller and friends
        if sys.platform == "darwin":  # inside Foo.app/Contents/MacOS: may be read-only or moved by macOS
            return Path(user_documents_dir()) / "Mecha-Assemble-Proxy-Helper"
        exe_dir = Path(sys.executable).resolve().parent
        if _writable(exe_dir):
            return exe_dir
        return Path(user_documents_dir()) / "Mecha-Assemble-Proxy-Helper"  # e.g. unzipped into Program Files
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "mecha_proxy").is_dir():
            return parent
    return Path(user_documents_dir()) / "Mecha-Assemble-Proxy-Helper"


def _writable(folder: Path) -> bool:
    """Really try to write (os.access is unreliable on Windows with UAC folder virtualization)."""
    probe = folder / ".write-test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def legacy_cache_dir() -> Path:
    return Path(user_cache_dir(LEGACY_APP_DIR, appauthor=False))


def migrate_legacy(home: Path, log=print) -> bool:
    """Move a v0.1.x cache from the hidden OS cache dir into the portable layout.

    Images, index and card list are moved (no re-download). Resized copies are
    left behind and deleted since they regenerate on demand.
    """
    old = legacy_cache_dir()
    new_index = home / ".cache" / "index.json"
    if not (old / "index.json").is_file() or new_index.exists() or old.resolve() == home.resolve():
        return False
    cards, meta = home / "cards", home / ".cache"
    cards.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True, exist_ok=True)
    moved = 0
    old_images = old / "images"
    if old_images.is_dir():
        for src in sorted(p for p in old_images.rglob("*") if p.is_file()):
            dst = cards / src.relative_to(old_images)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.move(str(src), str(dst))
                moved += 1
    for name in ("index.json", "manifest.json"):
        if (old / name).is_file():
            shutil.move(str(old / name), str(meta / name))
    if (old / "backs").is_dir() and not (meta / "backs").exists():
        shutil.move(str(old / "backs"), str(meta / "backs"))
    shutil.rmtree(old, ignore_errors=True)
    if old.parent.name == LEGACY_APP_DIR:  # Windows: …\MechaAssembleProxyHelper\Cache
        try:
            old.parent.rmdir()  # only if now empty
        except OSError:
            pass
    log(f"moved {moved} downloaded images from {old} to {cards}")
    return True

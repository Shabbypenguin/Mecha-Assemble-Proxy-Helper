"""Optional AI enhancement (Real-ESRGAN ncnn-vulkan) on Windows, macOS and Linux (x86_64).

- Tools are downloaded only when the user enables enhancement, from pinned sources, and
  verified by SHA-256; anything that doesn't match is refused and never run.
- Originals in cards/ are never touched. Enhanced masters go to cards-enhanced/<type>/<SET>/:
    normal:        the model's full output, lossless WebP
    space saving:  JPEG 95% (full colour) at the 400 DPI print size, embedded in PDFs as-is
- Each master records the original's SHA-256 and the preset, so it's redone only when the
  original changes (or the preset does).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests
from PIL import Image

from . import config
from .cache import atomic_write, sha256_file

Image.MAX_IMAGE_PIXELS = None  # 4x masters of our own cards are large but trusted

# ---- pinned downloads ---------------------------------------------------------------
@dataclass(frozen=True)
class Engine:
    """One platform's official Real-ESRGAN ncnn-vulkan release."""
    url: str
    sha256: str
    mb: int
    exe: str                      # program name inside the zip and on disk


_ESR = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-"
ENGINES: dict[str, Engine] = {
    "windows": Engine(_ESR + "windows.zip", "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d",
                      45, "realesrgan-ncnn-vulkan.exe"),
    # universal binary (Intel + Apple Silicon), MoltenVK built in
    "macos": Engine(_ESR + "macos.zip", "e0ad05580abfeb25f8d8fb55aaf7bedf552c375b5b4d9bd3c8d59764d2cc333a",
                    50, "realesrgan-ncnn-vulkan"),
    # x86_64, glibc 2.29+ (Ubuntu 20.04+); needs libvulkan.so.1 and libgomp.so.1 from the system
    "linux": Engine(_ESR + "ubuntu.zip", "e5aa6eb131234b87c0c51f82b89390f5e3e642b7b70f2b9bbe95b6a285a40c96",
                    45, "realesrgan-ncnn-vulkan"),
}


def platform_key() -> str | None:
    """Which engine build fits this computer, or None if there isn't one (e.g. Linux on ARM)."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64"):
        return "linux"
    return None


def current_engine() -> Engine | None:
    key = platform_key()
    return ENGINES[key] if key else None


# kept for callers/tests that refer to the Windows build directly
ESR_ZIP_URL, ESR_ZIP_SHA256, ESR_ZIP_MB = ENGINES["windows"].url, ENGINES["windows"].sha256, ENGINES["windows"].mb
UPSCAYL_MODELS = ("https://raw.githubusercontent.com/upscayl/upscayl/"
                  "a00d55fee90e0f9435d5eaa86e76700df8199af8/resources/models/")


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    model: str                 # ncnn model name passed with -n
    scale: int                 # -s
    files: tuple = ()          # extra model files: (filename, url, sha256)
    download_mb: int = 0
    credit: str = ""


PRESETS: dict[str, Preset] = {
    "ultramix": Preset(
        key="ultramix", label="Crisp graphics – UltraMix Balanced", model="ultramix-balanced-4x", scale=4,
        files=(("ultramix-balanced-4x.param", UPSCAYL_MODELS + "ultramix-balanced-4x.param",
                "859ecba5b3592ecf3e76c93bed65e9f627b5236dd696aae5a84ecf8c93ab65ce"),
               ("ultramix-balanced-4x.bin", UPSCAYL_MODELS + "ultramix-balanced-4x.bin",
                "171cae5968485d366b4fb575d232f98d117d94b766b15f22849cfccde40d2050")),
        download_mb=33,
        credit="UltraMix Balanced by Kim2091 – CC BY-NC-SA 4.0 (non-commercial, credit required); "
               "ncnn conversion distributed by Upscayl"),
    "official": Preset(
        key="official", label="Official – Real-ESRGAN animevideov3 x2", model="realesr-animevideov3", scale=2,
        credit="Real-ESRGAN animevideov3 model by Xintao Wang et al. – BSD-3-Clause"),
}
DEFAULT_PRESET = "ultramix"
DEFAULT_TYPES = ("unit", "other")   # tactics images are already ~300 DPI
SPACE_SAVING_DPI = 400
THUMB_W = 400  # enhanced grid previews, px wide
PRINT_DPI = 400  # what the app prints enhanced cards at
RUN_TIMEOUT_S = 600  # per card; a 4x UltraMix pass is seconds on a GPU
JPEG_SETTINGS = {"quality": 95, "subsampling": 0, "optimize": True}

CREDITS_TEXT = """\
AI enhancement uses these third-party components, downloaded from their own sources
when you enable it (they are not part of Mecha-Assemble-Proxy-Helper):

Real-ESRGAN ncnn-vulkan
  Copyright (c) 2021, Xintao Wang. BSD 3-Clause License.
  https://github.com/xinntao/Real-ESRGAN
  Redistribution and use in source and binary forms, with or without modification, are
  permitted provided that the conditions of the BSD 3-Clause License are met. THIS SOFTWARE
  IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND.

ncnn
  Copyright (C) Tencent. BSD 3-Clause License. https://github.com/Tencent/ncnn

UltraMix Balanced (upscaling model)
  By Kim2091. Creative Commons Attribution-NonCommercial-ShareAlike 4.0.
  https://openmodeldb.info/models/4x-UltraMix-Balanced  -  https://creativecommons.org/licenses/by-nc-sa/4.0/
  ncnn conversion distributed by Upscayl: https://github.com/upscayl/upscayl

Card images are processed locally on your computer; nothing is uploaded.
"""

CREDITS_HTML = (
    "• <a href='https://github.com/xinntao/Real-ESRGAN'>Real-ESRGAN ncnn-vulkan</a> – Xintao Wang, BSD-3-Clause<br>"
    "• <a href='https://github.com/Tencent/ncnn'>ncnn</a> – Tencent, BSD-3-Clause<br>"
    "• <a href='https://openmodeldb.info/models/4x-UltraMix-Balanced'>UltraMix Balanced</a> model – Kim2091, "
    "<a href='https://creativecommons.org/licenses/by-nc-sa/4.0/'>CC BY-NC-SA 4.0</a><br>"
    "• ncnn model conversion from <a href='https://github.com/upscayl/upscayl'>Upscayl</a> (AGPL-3.0)")


class EnhanceError(RuntimeError):
    pass


StepFn = Callable[[str, str, int, int], None]
LogFn = Callable[[str], None]


def supported() -> bool:
    return platform_key() is not None


UNSUPPORTED_MSG = ("AI enhancement isn't available on this system (it needs Windows, macOS, or Linux on "
                   "an x86_64 PC)")


# ---- tools -----------------------------------------------------------------------
class Tools:
    def __init__(self, meta_dir: Path, engine: Engine | None = None):
        self.engine = engine or current_engine() or ENGINES["windows"]
        self.dir = Path(meta_dir) / "ai"
        self.exe = self.dir / self.engine.exe
        self.models = self.dir / "models"
        self.marker = self.dir / "installed.json"

    def installed(self, preset: str | None = None) -> bool:
        if not self.exe.is_file() or not self.marker.is_file():
            return False
        if preset:
            p = PRESETS[preset]
            name = f"{p.model}-x{p.scale}" if p.model == "realesr-animevideov3" else p.model
            return (self.models / f"{name}.param").is_file() and (self.models / f"{name}.bin").is_file()
        return True

    def install(self, presets: list[str], session: requests.Session | None = None,
                on_progress: Callable[[str, int, int], None] | None = None,
                zip_url: str | None = None, zip_sha: str | None = None) -> None:
        """Download + verify the engine and the requested presets' model files."""
        zip_url, zip_sha = zip_url or self.engine.url, zip_sha or self.engine.sha256
        sess = session or requests.Session()
        sess.headers["User-Agent"] = config.USER_AGENT
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.exe.is_file():
            data = _download(sess, zip_url, "Real-ESRGAN engine", on_progress)
            if hashlib.sha256(data).hexdigest() != zip_sha:
                raise EnhanceError("The downloaded Real-ESRGAN package doesn't match its expected checksum, "
                                   "so it was not installed.")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    name = info.filename
                    if "/" in name.rstrip("/").replace("models/", "", 1) or ".." in name:
                        continue  # only top-level files and models/<file>; nothing that could escape
                    wanted = (name == self.engine.exe or name.endswith(".dll")
                              or (name.startswith("models/") and not name.endswith("/")))
                    if wanted:
                        target = self.dir / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(z.read(info))
            if not self.exe.is_file():
                raise EnhanceError("The Real-ESRGAN package didn't contain the expected program.")
            if os.name != "nt":  # the zips don't record Unix permissions
                self.exe.chmod(self.exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        for key in presets:
            for fname, url, sha in PRESETS[key].files:
                dst = self.models / fname
                if dst.is_file() and sha256_file(dst) == sha:
                    continue
                data = _download(sess, url, fname, on_progress)
                if hashlib.sha256(data).hexdigest() != sha:
                    raise EnhanceError(f"{fname} doesn't match its expected checksum, so it was not installed.")
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(dst, data)
        (self.dir / "THIRD-PARTY-LICENSES.txt").write_text(CREDITS_TEXT, encoding="utf-8")
        atomic_write(self.marker, json.dumps({"engine": zip_url, "sha256": zip_sha, "platform": platform_key(),
                                              "presets": presets, "installed_at": _now()}, indent=1).encode())

    def remove(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def run(self, src: Path, dst: Path, preset: str) -> None:
        p = PRESETS[preset]
        cmd = [str(self.exe), "-i", str(src), "-o", str(dst), "-n", p.model, "-s", str(p.scale), "-f", "png"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  stdin=subprocess.DEVNULL, cwd=self.dir, creationflags=flags,
                                  timeout=RUN_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise EnhanceError(f"The AI tool took longer than {RUN_TIMEOUT_S // 60} minutes on one card and "
                               "was stopped. Your graphics card may be too slow or busy.") from exc
        except OSError as exc:
            raise EnhanceError(f"Couldn't start the AI tool: {exc}") from exc
        err = (proc.stderr or "") + (proc.stdout or "")
        if proc.returncode != 0 or not dst.is_file():
            low = err.lower()
            if "error while loading shared libraries" in low:  # Linux: a system library is missing
                lib = err.split("libraries:", 1)[-1].split(":", 1)[0].strip() or "a system library"
                raise EnhanceError(f"The AI tool needs {lib}, which isn't installed. On Ubuntu/Debian: "
                                   "sudo apt install libvulkan1 libgomp1 mesa-vulkan-drivers "
                                   "(other distributions have similar packages).")
            if "vkcreateinstance" in low or "invalid gpu" in low or "vulkan" in low or "gpu" in low and "failed" in low:
                raise EnhanceError("AI enhancement needs a graphics card with Vulkan support, and none was "
                                   "found (or its driver is out of date). Updating your graphics driver may fix "
                                   "this; otherwise you can keep printing with the original images.")
            raise EnhanceError("The AI tool failed: " + (err.strip()[-400:] or f"exit code {proc.returncode}"))


def _download(sess: requests.Session, url: str, label: str,
              on_progress: Callable[[str, int, int], None] | None) -> bytes:
    with sess.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        for chunk in r.iter_content(1 << 16):
            buf.write(chunk)
            if on_progress:
                on_progress(label, buf.tell(), total)
        return buf.getvalue()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def print_width_px(card_type: str, dpi: int) -> int:
    return round(config.CARD_TYPES[card_type].width_mm / 25.4 * dpi)


# ---- enhanced image store ---------------------------------------------------------
class Store:
    """cards-enhanced/<type>/<SET>/<CODE Name>.webp|.jpg plus an index in .cache/enhanced.json."""

    def __init__(self, home: Path, meta_dir: Path):
        self.root = Path(home) / "cards-enhanced"
        self.index_path = Path(meta_dir) / "enhanced.json"
        self.thumbs = Path(meta_dir) / "enhanced-thumbs"   # small previews for the card grid
        self.index: dict[str, dict] = {}
        if self.index_path.is_file():
            try:
                self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.index = {}

    def save(self) -> None:
        atomic_write(self.index_path, json.dumps(self.index, indent=1, sort_keys=True).encode())

    @staticmethod
    def _rel(card, compressed: bool) -> str:
        base = card.image_relpath(card.print_image).rsplit(".", 1)[0]
        return base + (".jpg" if compressed else ".webp")

    def entry(self, card) -> dict | None:
        e = self.index.get(card.code)
        if e and (self.root / e["file"]).is_file():
            return e
        return None

    def path(self, card) -> Path | None:
        e = self.entry(card)
        return self.root / e["file"] if e else None

    def is_current(self, card, source: Path, preset: str, compressed: bool) -> bool:
        e = self.entry(card)
        return bool(e and e.get("source_sha256") == sha256_file(source) and e.get("preset") == preset
                    and bool(e.get("compressed")) == compressed)

    def _write(self, card, img: Image.Image, source_sha: str, preset: str, compressed: bool) -> Path:
        rel = self._rel(card, compressed)
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.stem + ".part" + target.suffix)
        if compressed:
            w = print_width_px(card.type, SPACE_SAVING_DPI)
            if img.width != w:
                img = img.resize((w, round(img.height * w / img.width)), Image.LANCZOS)
            img.convert("RGB").save(tmp, "JPEG", **JPEG_SETTINGS)
        else:
            img.convert("RGB").save(tmp, "WEBP", lossless=True, method=4)
        os.replace(tmp, target)
        self._write_thumb(card, img)
        old = self.index.get(card.code, {}).get("file")
        if old and old != rel:
            (self.root / old).unlink(missing_ok=True)  # replaced (e.g. compressed <-> full quality)
        self.index[card.code] = {"file": rel, "source_sha256": source_sha, "preset": preset,
                                 "compressed": compressed, "width": img.width, "height": img.height,
                                 "created_at": _now()}
        return target

    def _write_thumb(self, card, img: Image.Image) -> Path:
        self.thumbs.mkdir(parents=True, exist_ok=True)
        out = self.thumbs / f"{card.code}.jpg"
        t = img.convert("RGB")
        t = t.resize((THUMB_W, round(t.height * THUMB_W / t.width)), Image.LANCZOS)
        tmp = out.with_suffix(".part.jpg")
        t.save(tmp, "JPEG", quality=90)
        os.replace(tmp, out)
        return out

    def thumb(self, card) -> Path | None:
        """Grid-sized preview of the enhanced image (made on demand if missing or stale)."""
        src = self.path(card)
        if src is None:
            return None
        out = self.thumbs / f"{card.code}.jpg"
        if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
            return out
        try:
            with Image.open(src) as im:
                im.load()
                return self._write_thumb(card, im)
        except OSError:
            return None

    def enhance(self, tools: Tools, card, source: Path, preset: str, compressed: bool) -> Path:
        with tempfile.TemporaryDirectory(prefix="mapx-") as td:
            src_png, out_png = Path(td) / "in.png", Path(td) / "out.png"
            with Image.open(source) as im:
                im.convert("RGB").save(src_png)
            tools.run(src_png, out_png, preset)
            with Image.open(out_png) as out:
                out.load()
                return self._write(card, out, sha256_file(source), preset, compressed)

    def compress(self, card) -> bool:
        """Convert a full-quality master to the space-saving JPEG. Returns True if converted."""
        e = self.entry(card)
        if not e or e.get("compressed"):
            return False
        with Image.open(self.root / e["file"]) as im:
            im.load()
            self._write(card, im, e["source_sha256"], e["preset"], True)
        return True

    def remove_all(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.thumbs, ignore_errors=True)
        self.index = {}
        self.index_path.unlink(missing_ok=True)

    def count(self) -> int:
        return sum(1 for code in self.index if (self.root / self.index[code]["file"]).is_file())

    def compressed_codes(self) -> list[str]:
        return [c for c, e in self.index.items() if e.get("compressed")]

    def for_print(self, card, dpi: int | None, work_dir: Path, jpeg: bool = False) -> Path | None:
        """Enhanced image sized for printing (dpi None = full master), or None if there isn't one.

        jpeg=True ("Smaller PDF") gives a JPEG 95% 4:4:4 copy, which the PDF embeds as-is.
        Space-saving JPEGs are already at print size and are returned as-is, so the PDF embeds
        them without another round of compression.
        """
        e = self.entry(card)
        if not e:
            return None
        src = self.root / e["file"]
        if e.get("compressed"):
            return src
        w = min(print_width_px(card.type, dpi) if dpi else e.get("width", 0), e.get("width", 0))
        if w >= e.get("width", 0) and not jpeg:
            return src  # never enlarge past the master
        ext = ".jpg" if jpeg else ".png"
        key = hashlib.sha256(f"{e['file']}|{e['source_sha256']}|{e['preset']}|{w}|{ext}".encode()).hexdigest()[:16]
        out = Path(work_dir) / f"enh_{card.code}_{w}_{key}{ext}"
        if not out.exists():
            with Image.open(src) as im:
                im = im.convert("RGB")
                if w < im.width:
                    im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
                tmp = out.with_suffix(".part" + ext)
                if jpeg:
                    im.save(tmp, "JPEG", **JPEG_SETTINGS)
                else:
                    im.save(tmp)
                tmp.replace(out)
        return out


def enhance_many(store: Store, tools: Tools, items: list[tuple], preset: str, compressed: bool,
                 on_step: StepFn | None = None, should_stop: Callable[[], bool] | None = None,
                 force: bool = False) -> tuple[int, int, list[str]]:
    """items: [(card, source_path)]. Returns (enhanced, already_current, failed labels).

    Saves the index after every card, so a cancelled run keeps what it finished.
    """
    done = skipped = 0
    failed: list[str] = []
    total = len(items)
    for i, (card, source) in enumerate(items, 1):
        if should_stop and should_stop():
            break
        if not force and store.is_current(card, source, preset, compressed):
            skipped += 1
            status = "already enhanced"
        else:
            try:
                store.enhance(tools, card, source, preset, compressed)
                store.save()
                done += 1
                status = "enhanced"
            except EnhanceError:
                raise  # missing GPU etc.: stop and tell the user once
            except Exception as exc:  # noqa: BLE001 - one bad card shouldn't stop the batch
                failed.append(card.label)
                status = f"failed ({exc.__class__.__name__})"
        if on_step:
            on_step(card.label, status, i, total)
    store.save()
    return done, skipped, failed

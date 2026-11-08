"""Shared operations used by both the CLI and the GUI.

Nothing here prints or exits; progress and messages go through callbacks so each
front end can present them its own way.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from . import backs, config, pdf, scraper
from .cache import Cache, atomic_write
from .layout import fit_grid
from .paths import app_home, migrate_legacy

StepFn = Callable[[str, str, int, int], None]  # (label, status, done, total)
LogFn = Callable[[str], None]


def open_cache(home: str | Path | None = None, log: LogFn = print) -> Cache:
    root = app_home(str(home) if home else None)
    migrate_legacy(root, log=log)
    return Cache(root, log=log)


def load_or_build_manifest(cache: Cache, types: list[str], offline: bool, log: LogFn = print) -> dict:
    manifest = cache.load_manifest()
    missing = [t for t in types if not (manifest and manifest.get("cards", {}).get(t))]
    if missing:
        if offline:
            raise RuntimeError(f"no saved card list for {', '.join(missing)}; update first")
        manifest = scraper.build_manifest(cache, missing, log=log)
        cache.save_manifest(manifest)
    return manifest


def refresh_manifest(cache: Cache, types: list[str], log: LogFn = print,
                     on_step: StepFn | None = None, refresh: bool = False) -> dict:
    """Re-scrape card lists. Card details are fetched only for new cards (all if refresh)."""
    manifest = scraper.build_manifest(cache, types, log=log, refresh_details=refresh, on_step=on_step)
    cache.save_manifest(manifest)
    return manifest


def full_update(cache: Cache, log: LogFn = print, on_step: StepFn | None = None,
                should_stop: Callable[[], bool] | None = None) -> tuple[dict, list[str], list[str]]:
    """Menu "Update cards": re-read lists and every card's details, then download new images and
    re-check the images of cards whose details changed (errata). Returns (manifest, missing labels,
    changed codes)."""
    manifest = refresh_manifest(cache, list(config.CARD_TYPES), log=log, on_step=on_step, refresh=True)
    changed = set(manifest.get("last_changed", []))
    cards = [c for t in config.CARD_TYPES for c in scraper.cards_from_manifest(manifest, t)]
    _, missing = fetch_images(cache, [c for c in cards if c.code not in changed], offline=False,
                              on_step=on_step, should_stop=should_stop)
    if changed and not (should_stop and should_stop()):
        _, more = fetch_images(cache, [c for c in cards if c.code in changed], offline=False, refresh=True,
                               on_step=on_step, should_stop=should_stop)
        missing += more
    return manifest, missing, sorted(changed)


# ---- print history (for "cards you've printed were updated") -----------------------
def _printed_path(cache: Cache) -> Path:
    return cache.meta_dir / "printed.json"


def load_printed(cache: Cache) -> dict[str, str]:
    """{code: ISO time it was last put in a PDF}."""
    try:
        return json.loads(_printed_path(cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_printed(cache: Cache, codes) -> None:
    printed = load_printed(cache)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    printed.update({c: now for c in codes})
    atomic_write(_printed_path(cache), json.dumps(printed, indent=1, sort_keys=True).encode("utf-8"))


def changed_since_printed(manifest: dict | None, printed: dict[str, str]) -> list[str]:
    """Codes Bandai changed after they were last printed."""
    changes = (manifest or {}).get("changes", {})
    return sorted(code for code, ch in changes.items() if code in printed and ch.get("date", "") > printed[code])


def missing_images(cache: Cache, manifest: dict | None) -> list[scraper.Card]:
    """Cards in the saved list whose image isn't downloaded yet (no network)."""
    out = []
    for t in config.CARD_TYPES:
        for c in scraper.cards_from_manifest(manifest or {}, t):
            if local_image(cache, c) is None:
                out.append(c)
    return out


def new_on_site(cache: Cache, manifest: dict | None, log: LogFn = lambda m: None) -> list[str]:
    """Card codes listed on the site that the saved list doesn't have yet.

    Reads only the list pages (a handful of requests); nothing is saved or downloaded.
    """
    known = {c["code"] for cards in (manifest or {}).get("cards", {}).values() for c in cards}
    new = []
    for ctype in config.CARD_TYPES.values():
        try:
            listed = scraper.scrape_type(cache, ctype, log=log)
        except RuntimeError:  # an empty list page (e.g. no token cards yet) isn't an error here
            continue
        new += [c.label for c in listed if c.code not in known]
    return new


def card_urls(card: scraper.Card) -> list[str]:
    return [card.print_image] + ([card.list_image] if card.list_image != card.print_image else [])


def local_image(cache: Cache, card: scraper.Card) -> Path | None:
    """Already-downloaded image for a card, without touching the network."""
    for url in card_urls(card):
        path = cache.cached(url, card.image_relpath(url))
        if path:
            return path
    return None


def image_for(cache: Cache, card: scraper.Card, offline: bool, refresh: bool = False) -> Path | None:
    if offline:
        return local_image(cache, card)
    for url in card_urls(card):
        path = cache.fetch_image(url, refresh=refresh, rel=card.image_relpath(url))
        if path:
            return path
    return None


def fetch_images(cache: Cache, cards: list[scraper.Card], offline: bool, refresh: bool = False,
                 on_step: StepFn | None = None,
                 should_stop: Callable[[], bool] | None = None) -> tuple[dict[str, Path], list[str]]:
    """Get images for cards. Returns ({code: path}, [labels with no image])."""
    found, missing = {}, []
    total = len(cards)
    try:
        for i, card in enumerate(cards, 1):
            if should_stop and should_stop():
                break
            before = dict(cache.stats)
            path = image_for(cache, card, offline=offline, refresh=refresh)
            if path is None:
                status = "unavailable"
                missing.append(card.label)
            elif cache.stats["downloaded"] > before["downloaded"]:
                status = "downloaded"
            elif cache.stats["not_modified"] > before["not_modified"]:
                status = "unchanged"
            else:
                status = "cached"
            if path:
                found[card.code] = path
            if on_step:
                on_step(card.label, status, i, total)
    finally:
        cache.save_index()
    return found, missing


@dataclass
class PrintSettings:
    card_type: str = "unit"
    size: tuple[float, float] | None = None       # None -> config default for card_type
    mode: str = "duplex"                          # duplex | split | single
    flip: str = "long"
    bleed: float = 0.0
    gap: float = 0.0                              # card spacing between neighbouring cards, mm
    margin: float = 5.0
    back: Path | None = None                      # None -> built-in back
    back_offset: tuple[float, float] = (0.0, 0.0)
    crop_marks: bool = True
    reverse_backs: bool = False
    enhanced: bool = False                        # use AI-enhanced images where they exist
    sharpness: str = "400"                        # enhanced print size: "300" | "400" | "max"
    small_pdf: bool = False                       # enhanced images as JPEG 95% instead of lossless
    extra: dict = field(default_factory=dict)

    @property
    def enhanced_dpi(self) -> int | None:
        return None if self.sharpness == "max" else int(self.sharpness)

    @property
    def card_size(self) -> tuple[float, float]:
        return self.size or config.CARD_TYPES[self.card_type].size_mm


def per_sheet(settings: PrintSettings) -> int:
    w, h = settings.card_size
    return fit_grid(*config.LETTER_MM, w, h, bleed=settings.bleed, margin=settings.margin,
                    gap=settings.gap).per_page


def sheets_needed(settings: PrintSettings, n_cards: int) -> int:
    return math.ceil(n_cards / per_sheet(settings)) if n_cards else 0


def resolve_back(cache: Cache, s: PrintSettings) -> Path | None:
    if s.mode == "single":
        return None
    w, h = s.card_size
    if s.back:
        back = Path(s.back)
        if not back.exists():
            raise FileNotFoundError(f"back image not found: {back}")
        if s.bleed > 0:  # custom art: extend its edges for bleed like a front
            return pdf.prepare_image(back, w, h, s.bleed, 0, cache.processed_dir)
        return back
    return backs.back_path(cache.backs_dir, w, h, s.bleed)


def open_store(cache: Cache):
    from .enhance import Store
    return Store(cache.root, cache.meta_dir)


def make_pdf(cache: Cache, selection: list[tuple[scraper.Card, int]], found: dict[str, Path],
             s: PrintSettings, out: Path, log: LogFn = print, store=None) -> list[Path]:
    if s.enhanced and store is None:
        store = open_store(cache)
    fronts, labels, used_enhanced = [], [], 0
    for card, qty in selection:
        if card.code not in found:
            continue
        path = found[card.code]
        if s.enhanced:
            enh = store.for_print(card, s.enhanced_dpi, cache.processed_dir, jpeg=s.small_pdf)
            if enh is not None:
                path, used_enhanced = enh, used_enhanced + 1
        fronts += [path] * qty
        labels += [card.label] * qty
    if s.enhanced:
        log(f"AI-enhanced images used for {used_enhanced} of {len(selection)} card(s); "
            "the rest use the originals")
    if not fronts:
        raise RuntimeError("none of the selected cards have an image")
    w, h = s.card_size
    job = pdf.PrintJob(fronts=fronts, labels=labels, card_w=w, card_h=h, back=resolve_back(cache, s),
                       bleed=s.bleed, gap=s.gap, margin=s.margin, flip=s.flip, back_offset=s.back_offset,
                       crop_marks=s.crop_marks)
    written = pdf.build(job, s.mode, Path(out), cache.processed_dir, reverse_backs=s.reverse_backs, log=log)
    record_printed(cache, [card.code for card, _ in selection if card.code in found])
    return written


PROJECT_NAME = "Mecha-Assemble-Proxy-Helper"
TYPE_LABEL = {"unit": "units", "tactics": "tactics", "other": "other"}


def _taken(path: Path) -> bool:
    """True if path, or a split-mode sibling (_fronts/_backs), already exists."""
    return any(p.exists() for p in (path, path.with_name(path.stem + "_fronts.pdf"),
                                    path.with_name(path.stem + "_backs.pdf")))


def default_output(out_dir: Path, types: list[str], suffix: str = "", today: date | None = None) -> Path:
    """Default PDF path: <project>_<YYYY-MM-DD>[_suffix].pdf, numbered (_2, _3…) if taken.

    The number is chosen so that none of the files that would be written for
    `types` (see output_paths) already exist.
    """
    stem = f"{PROJECT_NAME}_{(today or date.today()).isoformat()}" + (f"_{suffix}" if suffix else "")
    n = 1
    while True:
        base = Path(out_dir) / (f"{stem}.pdf" if n == 1 else f"{stem}_{n}.pdf")
        if not any(_taken(p) for p in output_paths(base, types).values()):
            return base
        n += 1


def output_paths(base: Path, types: list[str]) -> dict[str, Path]:
    """One PDF per card type. A single type keeps the name as-is; several get _units / _tactics."""
    base = Path(base)
    if len(types) <= 1:
        return {t: base for t in types}
    return {t: base.with_name(f"{base.stem}_{TYPE_LABEL[t]}{base.suffix or '.pdf'}") for t in types}


def make_calibration(s: PrintSettings, out: Path) -> Path:
    w, h = s.card_size
    return pdf.build_calibration(w, h, Path(out), bleed=s.bleed, gap=s.gap, margin=s.margin, flip=s.flip,
                                 back_offset=s.back_offset)

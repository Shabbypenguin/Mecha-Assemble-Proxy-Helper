"""Command line interface.

  mecha-proxy update                       refresh card list, download new images
  mecha-proxy list --type unit             show available cards
  mecha-proxy build --type unit --all -o units.pdf
  mecha-proxy calibrate --type unit -o test.pdf
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, backs, config, scraper, service
from .cache import Cache
from .paths import app_home


def _types(arg: str) -> list[str]:
    return list(config.CARD_TYPES) if arg == "all" else [arg]


def _size(args) -> tuple[float, float]:
    ct = config.CARD_TYPES[args.type]
    return config.parse_size(args.size) if args.size else ct.size_mm


def _offset(text: str) -> tuple[float, float]:
    try:
        x, y = (float(v) for v in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("offset must look like 0.5,-1 (mm)") from exc
    return x, y


def _open_cache(args) -> Cache:
    return service.open_cache(args.home)


def _manifest(cache: Cache, types: list[str], offline: bool) -> dict:
    try:
        return service.load_or_build_manifest(cache, types, offline)
    except RuntimeError as exc:
        sys.exit(f"{exc} (run 'mecha-proxy update')")


class Progress:
    """One status line per card: overwritten in place on a terminal, plain lines otherwise."""

    def __init__(self, total: int, stream=None):
        self.total, self.done = total, 0
        self.stream = stream or sys.stdout
        self.tty = self.stream.isatty()
        self._width = 0

    def step(self, label: str, status: str) -> None:
        self.done += 1
        line = f"[{self.done}/{self.total}] {label:<9} {status}"
        if self.tty:
            self.stream.write("\r" + line.ljust(self._width))
            self._width = max(self._width, len(line))
            if self.done == self.total:
                self.stream.write("\n")
        else:
            self.stream.write(line + "\n")
        self.stream.flush()

    def close(self) -> None:
        if self.tty and 0 < self.done < self.total:
            self.stream.write("\n")  # interrupted mid-run: don't leave the cursor on the bar
            self.stream.flush()


def _fetch_with_progress(cache: Cache, cards: list[scraper.Card], offline: bool,
                         refresh: bool = False) -> tuple[dict[str, Path], list[str]]:
    bar = Progress(len(cards))
    try:
        return service.fetch_images(cache, cards, offline, refresh,
                                    on_step=lambda label, status, *_: bar.step(label, status))
    finally:
        bar.close()


# ---- commands -----------------------------------------------------------------
def cmd_update(args) -> int:
    cache = _open_cache(args)
    types = _types(args.type)
    details_bar: list[Progress] = []

    def detail_step(label, status, done, total):
        if not details_bar:
            print("card details (first time only):")
            details_bar.append(Progress(total))
        details_bar[0].step(label, status)

    try:
        manifest = service.refresh_manifest(cache, types, on_step=detail_step, refresh=args.refresh)
    finally:
        if details_bar:
            details_bar[0].close()
    cards = [c for t in types for c in scraper.cards_from_manifest(manifest, t)]
    _, failed = _fetch_with_progress(cache, cards, offline=False, refresh=args.refresh)
    s = cache.stats
    print(f"images: {s['downloaded']} downloaded, {s['not_modified']} unchanged (checked), "
          f"{s['cached']} already cached, {len(failed)} unavailable")
    if failed:
        print("unavailable: " + ", ".join(failed))
    return 0


def cmd_list(args) -> int:
    cache = _open_cache(args)
    manifest = _manifest(cache, _types(args.type), offline=args.offline)
    for t in _types(args.type):
        cards = scraper.cards_from_manifest(manifest, t)
        if args.set:
            cards = [c for c in cards if c.set in args.set]
        print(f"\n{t} ({len(cards)})")
        for c in cards:
            have = "*" if service.local_image(cache, c) else " "
            print(f"  {have} {c.label:<9} {c.name}")
    cache.save_index()  # cached() may have moved files into the sorted layout
    print("\n* = image already downloaded")
    return 0


def _parse_selection(args, cards: list[scraper.Card]) -> list[tuple[scraper.Card, int]]:
    by_code = {c.code: c for c in cards}
    chosen: dict[str, int] = {}
    if args.all:
        for c in cards:
            chosen[c.code] = args.qty
    for s in args.sets or []:
        hits = [c for c in cards if c.set == s]
        if not hits:
            sys.exit(f"no {args.type} cards in set {s.upper()}")
        for c in hits:
            chosen[c.code] = args.qty
    for item in args.cards or []:
        code, _, q = item.partition(":")
        code = code.strip().lower()
        if code not in by_code:
            sys.exit(f"unknown {args.type} card {code.upper()} (see 'mecha-proxy list')")
        try:
            qty = int(q) if q else args.qty
        except ValueError:
            sys.exit(f"bad quantity in {item!r}")
        if qty < 1:
            sys.exit(f"quantity must be at least 1 in {item!r}")
        chosen[code] = qty
    if not chosen:
        sys.exit("select cards with --all, --sets or --cards")
    return [(by_code[code], qty) for code, qty in sorted(chosen.items())]


def cmd_build(args) -> int:
    cache = _open_cache(args)
    manifest = _manifest(cache, [args.type], offline=args.offline)
    cards = scraper.cards_from_manifest(manifest, args.type)
    selection = _parse_selection(args, cards)
    card_w, card_h = _size(args)

    found, missing = _fetch_with_progress(cache, [c for c, _ in selection], offline=args.offline)
    if missing:
        print("skipped (no image" + (" in cache" if args.offline else "") + "): " + ", ".join(missing))
    if not found:
        return 1
    settings = _settings(args)
    out = Path(args.output) if args.output else service.default_output(cache.root / "output", [args.type])
    written = service.make_pdf(cache, selection, found, settings, out)
    if not args.offline:
        s = cache.stats
        print(f"images: {s['downloaded']} downloaded, {s['cached']} from cache")
    for p in written:
        print(f"wrote {p}")
    return 0


def _settings(args) -> service.PrintSettings:
    back = getattr(args, "back", None)
    return service.PrintSettings(
        card_type=args.type, size=config.parse_size(args.size) if args.size else None,
        mode=getattr(args, "mode", "duplex"), flip=args.flip, bleed=args.bleed, margin=args.margin,
        gap=getattr(args, "spacing", 0.0),
        back=Path(back) if back and back.lower() != "default" else None,
        back_offset=args.back_offset, crop_marks=not getattr(args, "no_crop_marks", False),
        reverse_backs=getattr(args, "reverse_backs", False),
        enhanced=getattr(args, "enhanced", False), sharpness=getattr(args, "sharpness", "400"),
        small_pdf=getattr(args, "small_pdf", False))


def cmd_calibrate(args) -> int:
    if args.output:
        target = Path(args.output)
    else:
        target = service.default_output(service.app_home(args.home) / "output", [args.type],
                                        suffix=f"calibration_{service.TYPE_LABEL[args.type]}")
    out = service.make_calibration(_settings(args), target)
    print(f"wrote {out} - print it like your cards, then hold it up to a light")
    return 0


def cmd_back(args) -> int:
    card_w, card_h = _size(args)
    img = backs.render_back(card_w, card_h, dpi=args.dpi, bleed_mm=args.bleed)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    img.save(args.output, dpi=(args.dpi, args.dpi))
    print(f"wrote {args.output} ({img.width}x{img.height}px)")
    return 0


def _size_mb(root: Path) -> float:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) / 1e6 if root.exists() else 0.0


def cmd_cache(args) -> int:
    cache = _open_cache(args)
    if args.clear_processed:
        shutil.rmtree(cache.processed_dir, ignore_errors=True)
        cache.processed_dir.mkdir(parents=True, exist_ok=True)
        print("cleared resized copies (downloads kept)")
    img_root = cache.images_dir
    images = [p for p in img_root.rglob("*") if p.is_file() and not p.name.endswith(".part")]
    print(f"cards: {img_root}\n  {len(images)} images, {_size_mb(img_root):.1f} MB")
    groups: dict[str, int] = {}
    for p in images:
        parts = p.relative_to(img_root).parts
        key = "/".join(parts[:2]) if len(parts) >= 3 else "(unsorted - run 'update')"
        groups[key] = groups.get(key, 0) + 1
    for key in sorted(groups):
        print(f"  {key}: {groups[key]}")
    print(f"tool data: {cache.meta_dir} ({_size_mb(cache.meta_dir):.1f} MB, safe to delete)")
    return 0


def cmd_ai(args) -> int:
    from . import enhance
    cache = _open_cache(args)
    tools = enhance.Tools(cache.meta_dir)
    if args.action == "status":
        store = service.open_store(cache)
        print(f"AI tools: {'installed' if tools.installed() else 'not installed'} ({tools.dir})")
        for key, p in enhance.PRESETS.items():
            print(f"  preset {key:9} {'ready' if tools.installed(key) else 'not downloaded':15} {p.label}")
        print(f"enhanced images: {store.count()} in {store.root} "
              f"({len(store.compressed_codes())} space-saving)")
        return 0
    if args.action == "remove":
        tools.remove()
        print("AI tools removed (enhanced images kept)")
        return 0
    if not enhance.supported():
        sys.exit(enhance.UNSUPPORTED_MSG)
    print(enhance.CREDITS_TEXT)
    last = [""]

    def progress(label, done, total):
        msg = f"\r  {label}: {done / 1e6:.0f}/{total / 1e6:.0f} MB" if total else f"\r  {label}: {done / 1e6:.0f} MB"
        if msg != last[0]:
            print(msg, end="", flush=True)
            last[0] = msg

    tools.install([args.preset], on_progress=progress)
    print("\nAI tools installed and verified.")
    return 0


def cmd_enhance(args) -> int:
    from . import enhance
    cache = _open_cache(args)
    tools = enhance.Tools(cache.meta_dir)
    if not tools.installed(args.preset):
        sys.exit(f"AI tools for preset '{args.preset}' aren't installed: run 'mecha-proxy ai install "
                 f"--preset {args.preset}' first")
    types = list(config.CARD_TYPES) if args.type == "all" else args.type.split(",")
    manifest = _manifest(cache, types, offline=True)
    items = []
    for t in types:
        for card in scraper.cards_from_manifest(manifest, t):
            if args.cards and card.code not in args.cards:
                continue
            src = service.local_image(cache, card)
            if src:
                items.append((card, src))
    cache.save_index()
    if not items:
        sys.exit("no downloaded cards to enhance (run 'mecha-proxy update' first)")
    store = service.open_store(cache)
    bar = Progress(len(items))
    try:
        done, skipped, failed = enhance.enhance_many(
            store, tools, items, args.preset, args.space_saving, force=args.force,
            on_step=lambda label, status, *_: bar.step(label, status))
    except enhance.EnhanceError as exc:
        bar.close()
        sys.exit(str(exc))
    bar.close()
    print(f"{done} enhanced, {skipped} already up to date, {len(failed)} failed -> {store.root}")
    return 0 if not failed else 1


def cmd_gui(args) -> int:
    try:
        from . import gui
    except ImportError:
        sys.exit('the desktop app needs PySide6: python -m pip install -e ".[gui]"')
    return gui.main(["mecha-proxy-gui"] + (["--home", args.home] if args.home else []))


def cmd_open(args) -> int:
    cache = _open_cache(args)
    target = cache.images_dir
    if sys.platform.startswith("win"):
        os.startfile(target)  # noqa: S606 - opens Explorer on our own folder
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(target)], check=False)
    print(f"opened {target}")
    return 0


# ---- parser -------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mecha-proxy", description="Print replacement cards at home.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--home", help=f"folder for cards/ and .cache/ (default: {app_home()})")
    sub = p.add_subparsers(dest="cmd", required=True)

    ai = sub.add_parser("ai", help="optional AI enhancement tools: status / install / remove")
    ai.add_argument("action", choices=["status", "install", "remove"])
    ai.add_argument("--preset", choices=["ultramix", "official"], default="ultramix")
    ai.set_defaults(func=cmd_ai)

    en = sub.add_parser("enhance", help="AI-enhance downloaded cards into cards-enhanced/ (originals untouched)")
    en.add_argument("--type", default="unit,other", help="card types, comma separated, or 'all' (default unit,other)")
    en.add_argument("--cards", type=lambda s: [x.strip().lower() for x in s.split(",") if x.strip()],
                    help="only these card codes")
    en.add_argument("--preset", choices=["ultramix", "official"], default="ultramix")
    en.add_argument("--space-saving", action="store_true",
                    help="store as JPEG 95%% at 400 DPI print size instead of the full lossless output")
    en.add_argument("--force", action="store_true", help="redo cards that are already enhanced")
    en.set_defaults(func=cmd_enhance)

    g = sub.add_parser("gui", help="open the desktop app")
    g.set_defaults(func=cmd_gui)

    op = sub.add_parser("open", help="open the downloaded cards folder")
    op.set_defaults(func=cmd_open)

    u = sub.add_parser("update", help="refresh the card list and download any new images")
    u.add_argument("--type", choices=[*config.CARD_TYPES, "all"], default="all")
    u.add_argument("--refresh", action="store_true",
                   help="re-check cached images and re-read card details "
                        "(unchanged images are not re-downloaded)")
    u.set_defaults(func=cmd_update)

    ls = sub.add_parser("list", help="show available cards")
    ls.add_argument("--type", choices=[*config.CARD_TYPES, "all"], default="all")
    ls.add_argument("--set", type=lambda s: [x.strip().lower() for x in s.split(",")],
                    help="filter by set codes, e.g. st01,ex02")
    ls.add_argument("--offline", action="store_true")
    ls.set_defaults(func=cmd_list)

    def sizing(sp):
        sp.add_argument("--type", choices=list(config.CARD_TYPES), required=True)
        sp.add_argument("--size", help="override card size in mm, e.g. 148x105")
        sp.add_argument("--bleed", type=float, default=0.0, help="bleed in mm (0 = none, try 2-3)")
        sp.add_argument("--spacing", type=float, default=0.0,
                        help="white space between cards in mm (0 = cards touch, shared cut lines)")
        sp.add_argument("--margin", type=float, default=5.0, help="unprintable page margin in mm")

    b = sub.add_parser("build", help="make printable PDF sheets")
    sizing(b)
    sel = b.add_argument_group("selection (combine freely)")
    sel.add_argument("--all", action="store_true", help="every card of this type")
    sel.add_argument("--sets", type=lambda s: [x.strip().lower() for x in s.split(",")],
                     help="whole sets, e.g. st01,ex02")
    sel.add_argument("--cards", type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
                     help="specific cards with optional qty, e.g. ex01-u02:3,st01-u01")
    sel.add_argument("--qty", type=int, default=1, help="copies of each card from --all/--sets (default 1)")
    b.add_argument("--mode", choices=["duplex", "split", "single"], default="duplex",
                   help="duplex: auto-duplex printer; split: fronts+backs PDFs for manual duplex "
                        "or gluing; single: fronts only")
    b.add_argument("--flip", choices=["long", "short"], default="long",
                   help="which edge the sheet turns over (match your printer's duplex setting)")
    b.add_argument("--back", help="custom back image path (default: built-in red hex back)")
    b.add_argument("--back-offset", type=_offset, default=(0.0, 0.0),
                   help="shift backs by X,Y mm to correct printer misalignment")
    b.add_argument("--reverse-backs", action="store_true",
                   help="split mode: reverse back page order (some printers need it for manual duplex)")
    b.add_argument("--no-crop-marks", action="store_true")
    b.add_argument("--offline", action="store_true", help="use only the local cache, no network")
    b.add_argument("--enhanced", action="store_true", help="use AI-enhanced images where they exist")
    b.add_argument("--sharpness", choices=["300", "400", "max"], default="400",
                   help="print size for enhanced images in DPI (default 400; max = full master)")
    b.add_argument("--small-pdf", action="store_true",
                   help="store enhanced images as JPEG 95%% (about 4x smaller PDF, same look on paper)")
    b.add_argument("-o", "--output",
                   help="output PDF path (default: output/Mecha-Assemble-Proxy-Helper_<date>.pdf)")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("calibrate", help="print a front/back alignment test sheet")
    sizing(c)
    c.add_argument("--flip", choices=["long", "short"], default="long")
    c.add_argument("--back-offset", type=_offset, default=(0.0, 0.0))
    c.add_argument("-o", "--output", help="output PDF path (default: dated name in output/)")
    c.set_defaults(func=cmd_calibrate)

    bk = sub.add_parser("back", help="export the built-in card back as a PNG")
    sizing(bk)
    bk.add_argument("--dpi", type=int, default=300)
    bk.add_argument("-o", "--output", required=True)
    bk.set_defaults(func=cmd_back)

    ca = sub.add_parser("cache", help="show cache location and size")
    ca.add_argument("--clear-processed", action="store_true",
                    help="delete resized/bleed copies (downloads are kept)")
    ca.set_defaults(func=cmd_cache)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "bleed", 0) < 0 or getattr(args, "margin", 0) < 0 or getattr(args, "spacing", 0) < 0:
        sys.exit("bleed, spacing and margin must be >= 0")
    if getattr(args, "qty", 1) < 1:
        sys.exit("--qty must be at least 1")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level: show a clean message
        if os.environ.get("MECHA_PROXY_DEBUG"):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Scenarios: Bandai's scenario PDFs, kept offline and reflowed for US Letter printing.

Bandai publishes each scenario as one wide page: a two-page rulebook spread (A3 landscape,
two A4 pages side by side). For printing, each spread is split down the middle into two
Letter pages, scaled to fit inside a 5 mm margin (about 91%; Bandai's pages are printed edge to
edge, which home printers can't do), keeping the original text and artwork as they are.

The scenario goal card is printed inside the spread, at a different scale in each PDF and
sometimes with no text layer at all (text converted to outlines). Optionally it is cut out
and placed twice on a third page (one per player) at 88 x 63 mm with crop marks. It is found
on a low-resolution render of the page by its red "SCENARIO GOAL CARD" ribbon (pdfium), then
measured precisely on a high-resolution render of just that area. If exactly one such
ribbon isn't found, or the area doesn't look like a card, it is skipped rather than
printing a wrong crop.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse

from . import config
from .cache import atomic_write

LIST_PATH = "rules-qa/scenario/"
MM = 72 / 25.4
LETTER = (215.9 * MM, 279.4 * MM)

# The goal card's red ribbon starts at the card's top-left corner and covers 55.9% of the card
# height; the card has 88:63 proportions (measured on Sleeping Leviathan at 97% scale and
# Operation Lockdown at 61%). Printed at 88 x 63 mm: the tactics card size, which those exact
# proportions point to (no physical card to measure yet).
GOAL_RIBBON_SHARE = 0.5587
GOAL_CARD_RATIO = 88 / 63
GOAL_CARD_PRINT_MM = (88.0, 63.0)
SPREAD_RATIO = 1.2  # pages wider than tall by more than this are two-page spreads
PRINT_MARGIN = 5 * MM  # unprintable edge on most home printers

StepFn = Callable[[str, str, int, int], None]


@dataclass
class Scenario:
    title: str
    set_name: str
    url: str | None          # None = "coming soon"
    updated: str | None      # as shown on the site, e.g. "2026.11.13"
    size: str | None = None  # e.g. "6.35MB"

    @property
    def available(self) -> bool:
        return bool(self.url)

    @property
    def key(self) -> str:
        return f"{self.set_name} | {self.title}"

    @property
    def filename(self) -> str | None:
        if not self.url:
            return None
        name = Path(unquote(urlparse(self.url).path)).name
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name) or None


# ---- reading the list page ----------------------------------------------------------------
class _ListParser(HTMLParser):
    """Each scenario: <a class="pg-rules__btLink" href=pdf> … subTtl (set) <br> title … Last Updated."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._cur: dict | None = None
        self._depth = 0
        self._field: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "a" and "pg-rules__btLink" in cls:
            self._cur = {"href": None if "disabled" in cls else a.get("href"), "set": [], "title": [],
                         "updated": [], "size": []}
            self._depth = 1
            return
        if self._cur is None:
            return
        if tag == "a":
            self._depth += 1
        if "pg-rules__subTtl" in cls:
            self._field = "set"
        elif "pg-rules__mainTtl" in cls:
            self._field = "title"
        elif "pg-rules__supplementLastupTxt" in cls:
            self._field = "updated"
        elif "pg-rules__supplementDownloadTxt" in cls:
            self._field = "size"

    def handle_endtag(self, tag):
        if self._cur is None:
            return
        if tag == "span" and self._field == "set":
            self._field = "title"
        elif tag == "p" and self._field in ("updated", "size"):
            self._field = None
        elif tag == "a":
            self._depth -= 1
            if self._depth == 0:
                self.items.append(self._cur)
                self._cur, self._field = None, None

    def handle_data(self, data):
        if self._cur is not None and self._field:
            text = " ".join(data.split())
            if text:
                self._cur[self._field].append(text)


def parse_list(html: str, base: str = config.SITE_BASE) -> list[Scenario]:
    p = _ListParser()
    p.feed(html)
    out = []
    for it in p.items:
        title = " ".join(it["title"]).strip()
        if not title:
            continue
        updated_txt = " ".join(it["updated"])
        m = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", updated_txt)
        updated = f"{m[1]}.{int(m[2]):02d}.{int(m[3]):02d}" if m else None
        href = it["href"]
        url = urljoin(base, href) if href and href.lower().split("?")[0].endswith(".pdf") else None
        out.append(Scenario(title=title, set_name=" ".join(it["set"]).strip(), url=url,
                            updated=updated if url else None, size=" ".join(it["size"]) or None))
    return out


def fetch_list(cache) -> list[Scenario]:
    return parse_list(cache.get_text(urljoin(config.SITE_BASE, LIST_PATH)))


# ---- local copies -----------------------------------------------------------------------------
def folder(cache) -> Path:
    return Path(cache.root) / "scenarios"


def _state_path(cache) -> Path:
    return Path(cache.meta_dir) / "scenarios.json"


def load_state(cache) -> dict:
    """{"list": [Scenario...], "files": {url: {"file", "updated", "sha256"}}, "checked_at"}."""
    try:
        return json.loads(_state_path(cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"list": [], "files": {}}


def _save_state(cache, state: dict) -> None:
    atomic_write(_state_path(cache), json.dumps(state, indent=1).encode("utf-8"))


def saved_list(cache) -> list[Scenario]:
    return [Scenario(**s) for s in load_state(cache).get("list", [])]


def local_pdf(cache, sc: Scenario) -> Path | None:
    info = load_state(cache).get("files", {}).get(sc.url or "")
    if not info:
        return None
    path = folder(cache) / info["file"]
    return path if path.is_file() else None


def is_pdf(data: bytes) -> bool:
    if not data.startswith(b"%PDF"):
        return False
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages) > 0
    except Exception:  # noqa: BLE001 - unreadable means not usable
        return False


def update(cache, log=print, on_step: StepFn | None = None,
           should_stop: Callable[[], bool] | None = None) -> tuple[list[Scenario], list[str]]:
    """Refresh the list and download new or updated PDFs. Returns (scenarios, changed titles)."""
    scenarios = fetch_list(cache)
    state = load_state(cache)
    files = state.setdefault("files", {})
    todo = [s for s in scenarios if s.available]
    changed = []
    folder(cache).mkdir(parents=True, exist_ok=True)
    for i, sc in enumerate(todo, 1):
        if should_stop and should_stop():
            break
        info = files.get(sc.url)
        have = info and (folder(cache) / info["file"]).is_file()
        if have and info.get("updated") == sc.updated:
            status = "up to date"
        else:
            cache._throttle()
            resp = cache.session.get(sc.url, timeout=max(config.TIMEOUT_S, 120))
            resp.raise_for_status()
            data = resp.content
            if not is_pdf(data):
                raise RuntimeError(f"the site returned something that isn't a PDF for {sc.title}")
            target = folder(cache) / sc.filename
            atomic_write(target, data)
            if info and info.get("file") != sc.filename:
                (folder(cache) / info["file"]).unlink(missing_ok=True)
            files[sc.url] = {"file": sc.filename, "updated": sc.updated,
                             "sha256": hashlib.sha256(data).hexdigest()}
            status = "updated" if have else "downloaded"
            changed.append(sc.title)
            log(f"scenario {sc.title}: {status}")
        if on_step:
            on_step(sc.title, status, i, len(todo))
    state["list"] = [asdict(s) for s in scenarios]
    state["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_state(cache, state)
    return scenarios, changed


def new_on_site(cache) -> list[str]:
    """Titles that are downloadable on the site but not yet saved here (light startup check)."""
    have = {s.url for s in saved_list(cache) if s.available} | set(load_state(cache).get("files", {}))
    return [s.title for s in fetch_list(cache) if s.available and s.url not in have]


# ---- Letter PDF ---------------------------------------------------------------------------------
def _clip(page, rect: tuple[float, float, float, float]) -> None:
    """Restrict everything already drawn on page to rect (x0, y0, x1, y1), in points."""
    from pypdf.generic import ContentStream, FloatObject
    x0, y0, x1, y1 = rect
    cs = page.get_contents()
    ops = cs.operations if cs is not None else []
    head = [([], b"q"), ([FloatObject(x0), FloatObject(y0), FloatObject(x1 - x0), FloatObject(y1 - y0)], b"re"),
            ([], b"W"), ([], b"n")]
    new = ContentStream(None, None)
    new.operations = head + ops + [([], b"Q")]
    page.replace_contents(new)


def _placed(src, src_rect, dst_xy, scale):
    """A Letter page showing src_rect of src page at dst_xy (lower-left), scaled, clipped."""
    from pypdf import PageObject, Transformation
    x0, y0, x1, y1 = src_rect
    dx, dy = dst_xy
    tmp = PageObject.create_blank_page(width=LETTER[0], height=LETTER[1])
    tmp.merge_transformed_page(src, Transformation().translate(-x0, -y0).scale(scale, scale).translate(dx, dy))
    _clip(tmp, (dx, dy, dx + (x1 - x0) * scale, dy + (y1 - y0) * scale))
    return tmp


def page_halves(page) -> list[tuple[float, float, float, float]]:
    """Content rects for one source page: two halves for a spread, else the whole page."""
    b = page.mediabox
    x0, y0, x1, y1 = float(b.left), float(b.bottom), float(b.right), float(b.top)
    if (x1 - x0) > SPREAD_RATIO * (y1 - y0):
        mid = (x0 + x1) / 2
        return [(x0, y0, mid, y1), (mid, y0, x1, y1)]
    return [(x0, y0, x1, y1)]


def _red_strips(img, pxmm: float) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (px) of tall, narrow, strongly red areas in a PIL RGB image."""
    from PIL import ImageChops
    r, g, b = img.split()
    mask = ImageChops.multiply(ImageChops.multiply(r.point(lambda v: 255 if v > 150 else 0),
                                                   g.point(lambda v: 255 if v < 70 else 0)),
                               ImageChops.multiply(b.point(lambda v: 255 if v < 70 else 0),
                                                   ImageChops.subtract(r, g).point(lambda v: 255 if v > 100 else 0)))
    w = img.width
    red = {(i % w, i // w) for i, v in enumerate(mask.tobytes()) if v}  # mode "L": one byte per pixel
    out, seen = [], set()
    for start in red:
        if start in seen:
            continue
        seen.add(start)
        stack, xs, ys = [start], [], []
        while stack:
            x, y = stack.pop()
            xs.append(x)
            ys.append(y)
            for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if q in red and q not in seen:
                    seen.add(q)
                    stack.append(q)
        x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
        if (y1 - y0) > 4 * (x1 - x0) and (y1 - y0) > 10 * pxmm and (x1 - x0) > pxmm:
            out.append((x0, y0, x1, y1))
    return out


def find_goal_card(pdf_path: Path, page_index: int = 0) -> tuple[float, float, float, float] | None:
    """Rect (x0, y0, x1, y1 in PDF points, origin bottom-left) of the goal card on a page, or None."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        page = doc[page_index]
        pw, ph = page.get_size()                     # points
        lo = 50 / 72                                  # 50 dpi overview
        img = page.render(scale=lo).to_pil().convert("RGB")
        pxmm = lo * MM
        strips = _red_strips(img, pxmm)
        if len(strips) != 1:
            return None
        x0, y0, x1, y1 = strips[0]
        # precise measurement: 400 dpi render of just the ribbon area (crop = margins to cut, points)
        pad = 2 * MM
        l, t = x0 / lo - pad, y0 / lo - pad
        r, b = x1 / lo + pad, y1 / lo + pad
        hi = 400 / 72
        crop = (max(l, 0), max(ph - b, 0), max(pw - r, 0), max(t, 0))
        img2 = page.render(scale=hi, crop=crop).to_pil().convert("RGB")
        fine = _red_strips(img2, hi * MM)
        if not fine:
            return None
        fx0, fy0, fx1, fy1 = max(fine, key=lambda s: s[3] - s[1])
        left = crop[0] + fx0 / hi                     # points from the page's left edge
        top = crop[3] + fy0 / hi                      # points from the page's top edge
        card_h = (fy1 - fy0) / hi / GOAL_RIBBON_SHARE
        card_w = card_h * GOAL_CARD_RATIO
        if left + card_w > pw or top + card_h > ph:
            return None
        # sanity check: the area right of the ribbon is a light card face
        box = img.crop((int((left + card_w * 0.15) * lo), int(top * lo), int((left + card_w) * lo),
                        int((top + card_h) * lo))).convert("L")
        data = box.tobytes()
        if not data or sum(data) / len(data) < 170:
            return None
        return (left, ph - top - card_h, left + card_w, ph - top)
    except Exception:  # noqa: BLE001 - any rendering trouble: no goal card rather than a wrong one
        return None
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass


def thumbnail(pdf_path: Path, out: Path, width: int = 360) -> Path | None:
    """Small PNG preview of the scenario's first page (left half of a spread: title + board)."""
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf_path))
        page = doc[0]
        pw, ph = page.get_size()
        half = pw / 2 if pw > SPREAD_RATIO * ph else 0
        scale = width / (pw - half)
        img = page.render(scale=scale, crop=(0, 0, half, 0)).to_pil().convert("RGB")
        doc.close()
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return out
    except Exception:  # noqa: BLE001 - previews are optional
        return None


def _crop_marks_page(cards: list[tuple[float, float, float, float]]):
    """Transparent overlay page with crop marks around each card rect (points)."""
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    c.setLineWidth(0.3)
    gap, ln = 1.0 * MM, 5.0 * MM
    for x0, y0, x1, y1 in cards:
        for x in (x0, x1):
            c.line(x, y1 + gap, x, y1 + gap + ln)
            c.line(x, y0 - gap, x, y0 - gap - ln)
        for y in (y0, y1):
            c.line(x0 - gap, y, x0 - gap - ln, y)
            c.line(x1 + gap, y, x1 + gap + ln, y)
    c.setFont("Helvetica", 7)
    c.drawCentredString(LETTER[0] / 2, 12 * MM, "Scenario goal cards - one per player. Print at 100% / Actual size.")
    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def build_letter_pdf(sources: list[Path], out: Path, goal_cards: bool = False,
                     log=print) -> tuple[Path, list[str]]:
    """Letter PDF of the given scenario PDFs. Returns (path, notes for the user)."""
    from pypdf import PageObject, PdfReader, PdfWriter
    writer = PdfWriter()
    notes = []
    for src in sources:
        reader = PdfReader(str(src))
        card = None
        for index, page in enumerate(reader.pages):
            for rect in page_halves(page):
                w, h = rect[2] - rect[0], rect[3] - rect[1]
                s = min((LETTER[0] - 2 * PRINT_MARGIN) / w, (LETTER[1] - 2 * PRINT_MARGIN) / h)
                dst = ((LETTER[0] - w * s) / 2, (LETTER[1] - h * s) / 2)
                out_page = PageObject.create_blank_page(width=LETTER[0], height=LETTER[1])
                out_page.merge_page(_placed(page, rect, dst, s))
                writer.add_page(out_page)
            if goal_cards and card is None:
                found = find_goal_card(Path(src), index)
                if found:
                    card = (page, found)
        if goal_cards:
            if card is None:
                notes.append(f"{src.stem}: goal card not found, so it was left out")
                continue
            page, rect = card
            s = GOAL_CARD_PRINT_MM[0] * MM / (rect[2] - rect[0])
            cw, ch = (rect[2] - rect[0]) * s, (rect[3] - rect[1]) * s
            x = (LETTER[0] - cw) / 2
            gap = 25 * MM
            y_top = (LETTER[1] + gap) / 2
            y_bot = (LETTER[1] - gap) / 2 - ch
            sheet = PageObject.create_blank_page(width=LETTER[0], height=LETTER[1])
            for y in (y_top, y_bot):
                sheet.merge_page(_placed(page, rect, (x, y), s))
            sheet.merge_page(_crop_marks_page([(x, y_top, x + cw, y_top + ch), (x, y_bot, x + cw, y_bot + ch)]))
            writer.add_page(sheet)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    writer.compress_identical_objects()  # defaults: merge duplicates, drop unused objects
    tmp = out.with_suffix(".part.pdf")
    with open(tmp, "wb") as f:
        writer.write(f)
    tmp.replace(out)
    log(f"scenario PDF: {len(writer.pages)} Letter page(s) -> {out}")
    return out, notes

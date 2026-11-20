"""Rules menu: Bandai's rule documents (core rulebook, organized play guide), always the latest.

The Rules/Q&A -> Rules page uses the same download blocks as the scenario page, so the list is
read with the scenario parser. Bandai puts the date in each PDF's file name
(…Rulebook_en_1106.pdf), so a document is tracked by its title, never by its link: picking it
from the menu re-reads the page, and if the link or the "Last Updated" date changed, the new PDF
replaces the old one. If the site can't be reached, the last downloaded copy is used.

Printing ("full pages" or a fold-in-half booklet) keeps the original pages as vectors. The
documents are Word files with wide margins, so the area that actually has content is measured
(pdfium render) and that is what gets fitted to the paper: full pages then print at 100% where
they fit, and booklet text comes out noticeably larger than shrinking whole pages would give.
Pages with artwork to the edges (the cover) are fitted whole.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from . import config, scenarios
from .cache import atomic_write
from .scenarios import MM, PRINT_MARGIN, Scenario, is_pdf, page_points

LIST_PATH = "rules-qa/rules/"
LAYOUTS = {"pages": "Full pages", "booklet": "Booklet (fold in half)"}
CONTENT_PAD = 2 * MM          # breathing room kept around the measured content
FULL_BLEED = 0.97             # content covering this share of both sides = artwork to the edges
INK_LEVEL = 230               # grey level below which a pixel counts as printed
LABEL_Y = 6 * MM               # sheet label height: inside what home printers can print
THICK_BOOKLET = 20            # sheets; more than this is hard to fold and staple
EDGE_BAND = 6 * MM            # edge decoration (bars running off the page) is ignored when measuring


# ---- the list and local copies ------------------------------------------------------------
def parse_list(html: str) -> list[Scenario]:
    return [d for d in scenarios.parse_list(html) if d.title]


def fetch_list(cache) -> list[Scenario]:
    return parse_list(cache.get_text(urljoin(config.SITE_BASE, LIST_PATH)))


def folder(cache) -> Path:
    return Path(cache.root) / "rules"


def _state_path(cache) -> Path:
    return Path(cache.meta_dir) / "rules.json"


def load_state(cache) -> dict:
    """{"list": [doc...], "files": {title: {"file", "url", "updated", "sha256"}}, "checked_at"}."""
    try:
        return json.loads(_state_path(cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"list": [], "files": {}}


def _save_state(cache, state: dict) -> None:
    atomic_write(_state_path(cache), json.dumps(state, indent=1).encode("utf-8"))


def saved_list(cache) -> list[Scenario]:
    return [Scenario(**d) for d in load_state(cache).get("list", [])]


def refresh_list(cache) -> list[Scenario]:
    """Re-read the page (one request, nothing downloaded) so the menu knows what exists."""
    docs = fetch_list(cache)
    state = load_state(cache)
    state["list"] = [asdict(d) for d in docs]
    state["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_state(cache, state)
    return docs


def local_copy(cache, title: str) -> tuple[Path, dict] | None:
    info = load_state(cache).get("files", {}).get(title)
    if not info:
        return None
    path = folder(cache) / info["file"]
    return (path, info) if path.is_file() else None


def get_latest(cache, title: str, log=print) -> tuple[Path, str]:
    """The newest copy of a document: (local PDF, note for the user, "" if nothing to say)."""
    try:
        docs = refresh_list(cache)
    except Exception as exc:  # noqa: BLE001 - offline: fall back to what we have
        have = local_copy(cache, title)
        if have:
            return have[0], (f"Couldn't reach Bandai's site, so this uses the copy downloaded earlier "
                             f"(last updated {have[1].get('updated') or 'unknown'}).")
        raise RuntimeError(f"couldn't reach Bandai's site to download {title.title()} ({exc})") from exc
    doc = next((d for d in docs if d.title == title), None)
    if doc is None or not doc.available:
        have = local_copy(cache, title)
        if have:
            return have[0], ("Bandai's page no longer offers this document, so this uses the copy "
                             "downloaded earlier.")
        raise RuntimeError(f"{title.title()} isn't available on Bandai's site right now")
    state = load_state(cache)
    files = state.setdefault("files", {})
    info = files.get(title)
    have = local_copy(cache, title)
    if have and info.get("url") == doc.url and info.get("updated") == doc.updated:
        return have[0], ""
    log(f"Downloading {title.title()} ({doc.size or 'PDF'})…")
    cache._throttle()
    resp = cache.session.get(doc.url, timeout=max(config.TIMEOUT_S, 120))
    resp.raise_for_status()
    data = resp.content
    if not is_pdf(data):
        raise RuntimeError(f"the site returned something that isn't a PDF for {title.title()}")
    folder(cache).mkdir(parents=True, exist_ok=True)
    target = folder(cache) / doc.filename
    atomic_write(target, data)
    if info and info.get("file") != doc.filename:
        (folder(cache) / info["file"]).unlink(missing_ok=True)  # the previous version
    files[title] = {"file": doc.filename, "url": doc.url, "updated": doc.updated,
                    "sha256": hashlib.sha256(data).hexdigest()}
    _save_state(cache, state)
    return target, (f"Downloaded the latest version (updated {doc.updated})." if have is None
                    else f"Bandai posted a new version (updated {doc.updated}); downloaded it.")


# ---- measuring the content ------------------------------------------------------------------
def content_boxes(pdf_path: Path) -> list[tuple[float, float, float, float] | None]:
    """Per page: rect (points, origin bottom-left) to print, or None for a blank page.

    Text pages share one rect (the union of their content plus a little padding) so every page
    gets the same scale and position; full-bleed pages (artwork to the edges) keep the whole page.
    Anything within EDGE_BAND of the page edge (decorative bars running off the page, which home
    printers can't print anyway) is left out of the measurement and clipped.
    """
    from pypdf import PdfReader
    sizes = [(float(p.mediabox.width), float(p.mediabox.height)) for p in PdfReader(str(pdf_path)).pages]
    whole = [(0.0, 0.0, w, h) for w, h in sizes]
    if len(set((round(w), round(h)) for w, h in sizes)) != 1:
        return whole  # mixed page sizes: keep it simple
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf_path))
    except Exception:  # noqa: BLE001 - no measuring: print whole pages
        return whole
    pw, ph = sizes[0]
    scale = 36 / 72
    kinds, union = [], None
    try:
        for i in range(len(doc)):
            img = doc[i].render(scale=scale).to_pil().convert("L")
            e = round(EDGE_BAND * scale)
            inner = img.crop((e, e, img.width - e, img.height - e))
            bb = inner.point(lambda v: 255 if v < INK_LEVEL else 0).getbbox()
            if bb is None:
                kinds.append("blank")
                continue
            if bb[2] - bb[0] >= FULL_BLEED * inner.width and bb[3] - bb[1] >= FULL_BLEED * inner.height:
                kinds.append("full")
                continue
            l, t, r, b = ((v + e) / scale for v in bb)  # points from the top-left
            kinds.append("text")
            union = (l, t, r, b) if union is None else (min(union[0], l), min(union[1], t),
                                                        max(union[2], r), max(union[3], b))
    except Exception:  # noqa: BLE001
        return whole
    finally:
        doc.close()
    if union is None:
        return [None if k == "blank" else (0.0, 0.0, pw, ph) for k in kinds]
    l, t, r, b = union
    text_rect = (max(l - CONTENT_PAD, 0), max(ph - b - CONTENT_PAD, 0),
                 min(r + CONTENT_PAD, pw), min(ph - t + CONTENT_PAD, ph))
    return [None if k == "blank" else (0.0, 0.0, pw, ph) if k == "full" else text_rect for k in kinds]


def _fit(rect, area_w: float, area_h: float, cap: float | None = None) -> float:
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    s = min(area_w / w, area_h / h)
    return min(s, cap) if cap else s


# ---- building the printable PDF -------------------------------------------------------------
def _form(writer, page, rect, cache: dict):
    """The page as a Form XObject clipped to rect (its /BBox). No content parsing: fast."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject
    key = (id(page), rect)
    if key in cache:
        return cache[key]
    contents = page.get_contents()
    form = DecodedStreamObject()
    form.set_data(contents.get_data() if contents is not None else b"")
    res = page.get("/Resources")
    form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                 NameObject("/BBox"): ArrayObject(FloatObject(v) for v in rect),
                 NameObject("/Resources"): res.get_object().clone(writer) if res is not None
                 else DictionaryObject()})
    ref = writer._add_object(form.flate_encode())
    cache[key] = ref
    return ref


MODES = {"duplex": "Double-sided printer", "manual": "Manual (print fronts, then backs)"}


def _guide_steps(mode: str, reverse: bool, sheets: int | None,
                 files: tuple[str, str] | None) -> list[tuple[str, str]]:
    """(heading, text) for each step of the booklet guide, worded for the chosen print settings."""
    edge = "short edge (holding it the way it came out of the printer: top to bottom, like a notepad)"
    if mode == "duplex":
        printing = [("Print", f"Print double-sided at <b>100% / Actual size</b> (turn off \"Fit to page\"), "
                              "<b>flip on short edge</b>. To save a sheet, skip "
                              "this guide: print from page 3 of the PDF.")]
    else:
        fronts, backs = files or ("…_fronts.pdf", "…_backs.pdf")
        printing = [
            ("Print the fronts", f"Print <b>{fronts}</b> at <b>100% / Actual size</b> (turn off \"Fit to page\")."),
            ("Put the stack back", f"Without changing the order, put the printed stack back in the paper tray, "
                                   f"turned over along its {edge}. Not sure which way your printer feeds? Pencil an "
                                   "arrow on a blank sheet, print one page on it and see where the arrow ends up."),
            ("Print the backs", f"Print <b>{backs}</b>. If the backs come out in reverse order, "
                                f"{'untick' if reverse else 'tick'} \"Reverse back page order\" and save again; if "
                                "they are upside down, the stack went back in turned the wrong way."),
        ]
    steps = printing + [
        ("Check", "Sheet 1 has the cover on the right half of its front and page 2 on the left half of its "
                  "back. Every sheet is labelled in its bottom margin (\"Sheet 3 of "
                  f"{sheets or 'N'} - front\")."),
        ("Stack", "Put the sheets in order: sheet 1 on top, cover side up, edges lined up."),
        ("Fold", "Fold the whole stack in half at once, folding the left half <b>underneath</b> so the cover "
                 "stays on the outside. Press the fold flat with a ruler or the back of a spoon."),
        ("Bind", "Staple along the fold from the outside, two staples about a quarter of the way in from the top "
                 "and bottom. A long-reach stapler is easiest. With a normal stapler: open it flat, lay the "
                 "opened booklet cover side up on a piece of cardboard, staple through the fold, then bend the "
                 "legs closed by hand. No stapler: binder clips along the spine, or a few stitches of needle "
                 "and thread through the fold."),
    ]
    if not sheets or sheets > 6:
        steps.append(("Trim (optional)", "With many sheets the inner pages stick out a little at the open edge. "
                                         "Trim them even with a paper cutter, staying inside the white margin."))
    return steps


def guide_pdf(paper: str, mode: str = "duplex", reverse: bool = False, sheets: int | None = None,
              files: tuple[str, str] | None = None) -> bytes:
    """One landscape page: how to print, fold and bind the booklet, with simple diagrams."""
    import io

    from reportlab.lib.colors import Color, black, white
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Frame, Paragraph
    pw, ph = page_points(paper)
    W, H = ph, pw                                   # landscape
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    m = 14 * MM
    red, grey = Color(0.72, 0.1, 0.12), Color(0.55, 0.55, 0.55)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(m, H - m - 6, "How to make this booklet")
    c.setFont("Helvetica", 9.5)
    c.setFillColor(grey)
    count = f"{sheets} sheet{'s' * (sheets != 1)} · " if sheets else ""
    c.drawString(m, H - m - 22, f"{count}{config.paper_label(paper)} · "
                                f"{MODES[mode].split(' (')[0].lower()} · flip on short edge")
    c.setFillColor(black)
    head = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=11, leading=13, spaceBefore=6)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=12)
    story = []
    for i, (title, text) in enumerate(_guide_steps(mode, reverse, sheets, files), 1):
        story += [Paragraph(f"<font color='#b81a1f'>{i}.</font> {title}", head), Paragraph(text, body)]
    col_w = (W - 2 * m) * 0.58
    Frame(m, m, col_w, H - 2 * m - 34, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0).addFromList(
        story, c)

    # diagrams, right column
    x0 = m + col_w + 12 * MM
    dw = W - m - x0
    label = lambda x, y, t: (c.setFont("Helvetica", 8), c.setFillColor(grey), c.drawCentredString(x, y, t),  # noqa: E731
                             c.setFillColor(black))
    # 1: sheet 1, front: cover on the right
    bh = dw / 1.414 * 0.68
    y = H - m - 34 - bh
    bw = bh * 1.414
    bx = x0 + (dw - bw) / 2
    c.setLineWidth(0.8)
    c.rect(bx, y, bw, bh)
    c.setDash(3, 2)
    c.line(bx + bw / 2, y, bx + bw / 2, y + bh)
    c.setDash()
    c.setFillColor(red)
    c.rect(bx + bw / 2 + 6, y + 6, bw / 2 - 12, bh - 12, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(bx + bw * 0.75, y + bh / 2 - 3, "COVER")
    c.setFillColor(black)
    c.setFont("Helvetica", 8)
    c.drawCentredString(bx + bw / 4, y + bh / 2 - 3, "back cover")
    label(bx + bw / 2, y - 11, "Sheet 1, front: cover on the right")
    # 2: stack and fold the left half underneath
    y2 = y - 22 - bh
    for k in range(3, 0, -1):
        c.setFillColor(white)
        c.rect(bx + k * 3, y2 - k * 3, bw, bh, fill=1)
    c.setFillColor(white)
    c.rect(bx, y2, bw, bh, fill=1)
    c.setFillColor(black)
    c.setDash(3, 2)
    c.line(bx + bw / 2, y2, bx + bw / 2, y2 + bh)
    c.setDash()
    p = c.beginPath()
    p.moveTo(bx + bw * 0.2, y2 + bh * 0.55)
    p.curveTo(bx + bw * 0.25, y2 - bh * 0.25, bx + bw * 0.45, y2 - bh * 0.25, bx + bw * 0.62, y2 + bh * 0.2)
    c.setStrokeColor(red)
    c.setLineWidth(1.4)
    c.drawPath(p)
    c.line(bx + bw * 0.62, y2 + bh * 0.2, bx + bw * 0.58, y2 + bh * 0.12)
    c.line(bx + bw * 0.62, y2 + bh * 0.2, bx + bw * 0.64, y2 + bh * 0.1)
    c.setStrokeColor(black)
    c.setLineWidth(0.8)
    label(bx + bw / 2, y2 - 20, "Stack in order, fold the left half underneath")
    # 3: folded booklet with two staples on the spine
    y3 = y2 - 34 - bh
    fx = bx + bw / 4
    c.setFillColor(red)
    c.rect(fx, y3, bw / 2, bh, fill=1, stroke=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(fx + bw / 4, y3 + bh / 2 - 3, "COVER")
    c.setFillColor(black)
    c.setLineWidth(2)
    for t in (0.25, 0.75):
        c.line(fx + 2, y3 + bh * t - 5, fx + 2, y3 + bh * t + 5)
    c.setLineWidth(0.8)
    label(fx + bw / 4, y3 - 11, "Two staples along the fold")
    c.setFont("Helvetica", 7)
    c.setFillColor(grey)
    c.drawRightString(W - m, m / 2, "Made with Mecha-Assemble-Proxy-Helper · the rule pages themselves are Bandai's, unchanged")
    c.showPage()
    c.save()
    return buf.getvalue()


def guide_image(paper: str, mode: str, reverse: bool, sheets: int | None = None, width: int = 1100,
                files: tuple[str, str] | None = None):
    """The guide page as a PIL image, for showing in the app."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(guide_pdf(paper, mode, reverse, sheets, files))
    try:
        page = doc[0]
        return page.render(scale=width / page.get_size()[0]).to_pil().convert("RGB")
    finally:
        doc.close()


def sheets_for(pdf_path: Path | None) -> int | None:
    """Booklet sheets a document needs (4 pages per sheet), if it has been downloaded."""
    if not pdf_path:
        return None
    try:
        from pypdf import PdfReader
        return -(-len(PdfReader(str(pdf_path)).pages) // 4)
    except Exception:  # noqa: BLE001
        return None


def output_files(out: Path, layout: str, mode: str) -> list[Path]:
    out = Path(out)
    if layout == "booklet" and mode == "manual":
        return [out.with_name(out.stem + "_fronts.pdf"), out.with_name(out.stem + "_backs.pdf")]
    return [out]


def build_pdf(src: Path, out: Path, paper: str = config.DEFAULT_PAPER, layout: str = "pages",
              log=print, mode: str = "duplex", reverse: bool = False,
              guide: bool = True) -> tuple[Path, dict]:
    """Printable PDF of a rule document. Returns (first file, info: files, sheets, pages, scale %, …).

    Booklet options: mode "duplex" (one file) or "manual" (_fronts / _backs files to print one after
    the other; a booklet always flips on the short edge), reverse (manual: backs file in reverse order),
    guide (an assembly guide sheet first, with a blank back so front/back pairing is kept).
    """
    import io

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    if layout not in LAYOUTS or mode not in MODES:
        raise ValueError("unknown layout or mode")
    reader = PdfReader(str(src))
    pages = list(reader.pages)
    boxes = content_boxes(Path(src))
    pw, ph = page_points(paper)                 # portrait
    files = output_files(out, layout, mode)
    writers = [PdfWriter() for _ in files]
    forms: dict = {}
    text_scales, all_scales = [], []
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})

    def place(writer, page, rect, cell_x, cell_w, cell_h, name, record=True):
        page_box = (0.0, 0.0, float(page.mediabox.width), float(page.mediabox.height))
        s = _fit(rect, cell_w - 2 * PRINT_MARGIN, cell_h - 2 * PRINT_MARGIN, cap=1.0)  # never enlarge
        if record:
            all_scales.append(s)
            if rect != page_box:
                text_scales.append(s)
        w, h = (rect[2] - rect[0]) * s, (rect[3] - rect[1]) * s
        tx = cell_x + (cell_w - w) / 2 - rect[0] * s
        ty = (cell_h - h) / 2 - rect[1] * s
        ref = _form(writer, page, rect, forms.setdefault(id(writer), {}))
        return f"q {s:.6f} 0 0 {s:.6f} {tx:.3f} {ty:.3f} cm {name} Do Q\n", name, ref

    def source(writer, index, cell_x, cell_w, cell_h):
        if index >= len(pages) or boxes[index] is None:
            return None  # padding page or blank page
        return place(writer, pages[index], boxes[index], cell_x, cell_w, cell_h, f"/P{index}")

    def sheet(writer, width, height, placements, label=""):
        page = writer.add_blank_page(width=width, height=height)
        xobjects = DictionaryObject()
        ops = ""
        for item in placements:
            if item:
                op, name, ref = item
                ops += op
                xobjects[NameObject(name)] = ref
        if label:  # tiny grey sheet label in the bottom margin, left of the fold
            text = label.replace("\\", "").replace("(", "").replace(")", "")
            ops += f"BT /F1 5 Tf 0.55 g {width / 2 - 60:.2f} {LABEL_Y:.2f} Td ({text}) Tj ET\n"
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects,
                                                           NameObject("/Font"): DictionaryObject(
                                                               {NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(ops.encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    if layout == "pages":
        for i in range(len(pages)):
            sheet(writers[0], pw, ph, [source(writers[0], i, 0.0, pw, ph)])
        sheets = len(pages)
    else:
        n = -(-len(pages) // 4) * 4             # pad to a multiple of 4 with blank pages
        sheets = n // 4
        fronts_w, backs_w = (writers[0], writers[0]) if mode == "duplex" else writers
        backs: list = []                        # (writer, placements builder) for the backs file

        def emit_back(build):
            if mode == "duplex":
                build()
            else:
                backs.append(build)

        if guide:
            gpage = PdfReader(io.BytesIO(guide_pdf(paper, mode, reverse, sheets,
                                                   tuple(f.name for f in files) if mode == "manual" else None))).pages[0]
            grect = (0.0, 0.0, float(gpage.mediabox.width), float(gpage.mediabox.height))
            sheet(fronts_w, ph, pw, [place(fronts_w, gpage, grect, 0.0, ph, pw, "/G", record=False)])
            emit_back(lambda: sheet(backs_w, ph, pw, []))
        for k in range(sheets):                 # landscape sheets, two pages side by side
            (fl, fr), (bl, br) = (n - 1 - 2 * k, 2 * k), (2 * k + 1, n - 2 - 2 * k)
            tag = f"Sheet {k + 1} of {sheets} - "
            sheet(fronts_w, ph, pw, [source(fronts_w, fl, 0.0, ph / 2, pw), source(fronts_w, fr, ph / 2, ph / 2, pw)],
                  tag + "front")
            emit_back(lambda bl=bl, br=br, tag=tag: sheet(
                backs_w, ph, pw, [source(backs_w, bl, 0.0, ph / 2, pw), source(backs_w, br, ph / 2, ph / 2, pw)],
                tag + "back"))
        for build in (reversed(backs) if reverse else backs):
            build()
    for path, writer in zip(files, writers):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part.pdf")
        with open(tmp, "wb") as f:
            writer.write(f)
        tmp.replace(path)
    scale = round(100 * min(text_scales or all_scales or [1.0]))
    info = {"layout": layout, "pages": len(pages), "sheets": sheets, "scale": scale, "paper": paper,
            "mode": mode, "reverse": reverse, "guide": guide and layout == "booklet",
            "files": files}
    log(f"rules PDF: {len(pages)} page(s) on {sheets} {config.paper_label(paper)} sheet(s), "
        f"{LAYOUTS[layout].lower()}, text at {scale}% -> {', '.join(str(f) for f in files)}")
    return files[0], info


def how_to_print(info: dict) -> str:
    paper = config.paper_label(info["paper"])
    if info["layout"] == "booklet":
        n = info["sheets"]
        text = f"Booklet: {n} {paper} sheet{'s' * (n != 1)}, text at {info['scale']}% of the original size. "
        if info.get("mode") == "manual":
            fronts, backs = (f.name for f in info["files"])
            text += (f"Print {fronts}, put the stack back in the tray turned over along its short edge, "
                     f"then print {backs}. ")
        else:
            text += "Print double-sided with \"flip on short edge\" at 100% / Actual size. "
        text += "Then fold the stack in half and staple along the fold."
        if info.get("guide"):
            text += " The first sheet of the PDF is a step-by-step assembly guide."
        if n > THICK_BOOKLET:
            text += (f" {n} sheets is a thick stack to fold – you could print it in full pages and staple or "
                     "bind it instead.")
        return text
    return (f"{info['pages']} {paper} page{'s' * (info['pages'] != 1)}, text at {info['scale']}% of the "
            "original size. Print at 100% / Actual size, single- or double-sided.")

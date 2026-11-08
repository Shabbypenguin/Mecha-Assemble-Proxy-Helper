"""PDF output: card sheets with crop marks, optional bleed, and three print modes.

Modes
  duplex  one PDF, front/back pages interleaved, for printers with auto duplex
  split   fronts PDF + backs PDF: manual duplex (re-feed the stack) or print two
          sheets and glue them back to back
  single  fronts only
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from . import config
from .layout import Grid, Slot, fit_grid

MARK_LEN = 5.0   # crop mark length, mm
MARK_GAP = 1.0   # gap between card block and crop mark, mm
LOW_DPI_WARN = 200


@dataclass
class PrintJob:
    fronts: list[Path]          # one entry per physical card (quantities already expanded)
    labels: list[str]           # matching card codes, for logging
    card_w: float
    card_h: float
    back: Path | None
    bleed: float = 0.0
    margin: float = 5.0
    gap: float = 0.0            # card spacing: white space between neighbouring cards, mm
    flip: str = "long"
    back_offset: tuple[float, float] = (0.0, 0.0)  # mm, shifts every back page
    crop_marks: bool = True


# ---- image prep ---------------------------------------------------------------
def prepare_image(src: Path, card_w: float, card_h: float, bleed: float, rot: int,
                  work_dir: Path) -> Path:
    """Return src scaled to the card's aspect, edge-extended for bleed, rotated.

    Results are cached by source content + parameters, so rebuilding is fast.
    The artwork itself is never cropped. JPEG sources (space-saving enhanced images) stay JPEG:
    used as-is when nothing needs changing, so the PDF embeds them without recompressing.
    """
    is_jpeg = Path(src).suffix.lower() in (".jpg", ".jpeg")
    if is_jpeg and bleed <= 0 and not rot:
        return Path(src)
    raw = Path(src).read_bytes()
    key = hashlib.sha256(raw + f"|{card_w}|{card_h}|{bleed}|{rot}".encode()).hexdigest()[:20]
    ext = ".jpg" if is_jpeg else ".png"
    out = Path(work_dir) / f"{Path(src).stem}_{key}{ext}"
    if out.exists():
        return out
    with Image.open(src) as im:
        im = im.convert("RGB")
        if bleed > 0:
            im = edge_extend(im, round(bleed * im.width / card_w), round(bleed * im.height / card_h))
        if rot:
            im = im.rotate(rot, expand=True)
        tmp = out.with_suffix(".part" + ext)
        if is_jpeg:
            im.save(tmp, "JPEG", quality=95, subsampling=0)
        else:
            im.save(tmp)
        tmp.replace(out)
    return out


def edge_extend(im: Image.Image, pad_x: int, pad_y: int) -> Image.Image:
    """Grow an image by repeating its outermost pixels outward (print bleed)."""
    if pad_x <= 0 and pad_y <= 0:
        return im
    w, h = im.size
    out = Image.new(im.mode, (w + 2 * pad_x, h + 2 * pad_y))
    out.paste(im, (pad_x, pad_y))
    if pad_x:
        out.paste(im.crop((0, 0, 1, h)).resize((pad_x, h)), (0, pad_y))
        out.paste(im.crop((w - 1, 0, w, h)).resize((pad_x, h)), (pad_x + w, pad_y))
    if pad_y:
        out.paste(im.crop((0, 0, w, 1)).resize((w, pad_y)), (pad_x, 0))
        out.paste(im.crop((0, h - 1, w, h)).resize((w, pad_y)), (pad_x, pad_y + h))
    if pad_x and pad_y:
        for (sx, sy), (dx, dy) in (((0, 0), (0, 0)), ((w - 1, 0), (pad_x + w, 0)),
                                   ((0, h - 1), (0, pad_y + h)), ((w - 1, h - 1), (pad_x + w, pad_y + h))):
            out.paste(im.getpixel((sx, sy)), (dx, dy, dx + pad_x, dy + pad_y))
    return out


def effective_dpi(src: Path, card_w: float) -> float:
    with Image.open(src) as im:
        return im.width / (card_w / 25.4)


# ---- drawing ------------------------------------------------------------------
def _draw_card(c: rl_canvas.Canvas, img: Path, slot: Slot, bleed: float) -> None:
    c.drawImage(str(img), (slot.x - bleed) * mm, (slot.y - bleed) * mm,
                (slot.w + 2 * bleed) * mm, (slot.h + 2 * bleed) * mm)


def _draw_crop_marks(c: rl_canvas.Canvas, g: Grid) -> None:
    xs, ys = g.cut_lines()
    left, bottom = g.x0, g.y0
    right, top = g.x0 + g.block_w, g.y0 + g.block_h
    c.saveState()
    c.setLineWidth(0.3)
    c.setStrokeColorRGB(0, 0, 0)
    for x in xs:
        c.line(x * mm, max(bottom - MARK_GAP - MARK_LEN, 0) * mm, x * mm, (bottom - MARK_GAP) * mm)
        c.line(x * mm, (top + MARK_GAP) * mm, x * mm, min(top + MARK_GAP + MARK_LEN, g.page_h) * mm)
    for y in ys:
        c.line(max(left - MARK_GAP - MARK_LEN, 0) * mm, y * mm, (left - MARK_GAP) * mm, y * mm)
        c.line((right + MARK_GAP) * mm, y * mm, min(right + MARK_GAP + MARK_LEN, g.page_w) * mm, y * mm)
    c.restoreState()


def _draw_footer(c: rl_canvas.Canvas, g: Grid, text: str) -> None:
    """Scale check bar + note, only where the bottom margin has room."""
    if g.y0 < 14:
        return
    c.saveState()
    c.setLineWidth(0.5)
    x0, y = 15, 8
    c.line(x0 * mm, y * mm, (x0 + 50) * mm, y * mm)
    for t in (0, 50):
        c.line((x0 + t) * mm, (y - 1.5) * mm, (x0 + t) * mm, (y + 1.5) * mm)
    c.setFont("Helvetica", 6.5)
    c.drawString((x0 + 53) * mm, (y - 0.8) * mm,
                 f"This bar must measure 50 mm - print at 100% / Actual size.  {text}")
    c.restoreState()


def _new_canvas(path: Path) -> rl_canvas.Canvas:
    pw, ph = config.LETTER_MM
    c = rl_canvas.Canvas(str(path), pagesize=(pw * mm, ph * mm))
    c.setTitle(path.stem)
    c.setCreator("Mecha-Assemble-Proxy-Helper")
    return c


# ---- public API ---------------------------------------------------------------
def build(job: PrintJob, mode: str, out: Path, work_dir: Path,
          reverse_backs: bool = False, log=print) -> list[Path]:
    if mode not in ("duplex", "split", "single"):
        raise ValueError("mode must be duplex, split or single")
    if mode != "single" and job.back is None:
        raise ValueError(f"mode {mode!r} needs a back image")
    if not job.fronts:
        raise ValueError("no cards selected")

    pw, ph = config.LETTER_MM
    g = fit_grid(pw, ph, job.card_w, job.card_h, bleed=job.bleed, margin=job.margin, gap=job.gap)
    fronts_slots, backs_slots = g.front_slots(), g.back_slots(job.flip)
    log(f"layout: {g.cols}x{g.rows} = {g.per_page} per sheet"
        f"{' (cards rotated)' if g.rotated else ''}, {job.card_w:g}x{job.card_h:g} mm, bleed {job.bleed:g} mm"
        f"{f', spacing {job.gap:g} mm' if job.gap else ''}")

    for path, label in zip(job.fronts, job.labels):
        dpi = effective_dpi(path, job.card_w)
        if dpi < LOW_DPI_WARN:
            log(f"note: {label} is ~{dpi:.0f} DPI at print size (soft but usable; upscaling helps)")
            break  # same source resolution for the whole set; say it once

    pages = [job.fronts[i:i + g.per_page] for i in range(0, len(job.fronts), g.per_page)]
    front_rot = fronts_slots[0].rot
    footer = f"{job.card_w:g}x{job.card_h:g} mm cards"

    def draw_front_page(c, imgs):
        for img, slot in zip(imgs, fronts_slots):
            _draw_card(c, prepare_image(img, job.card_w, job.card_h, job.bleed, front_rot, work_dir),
                       slot, job.bleed)
        if job.crop_marks:
            _draw_crop_marks(c, g)
        _draw_footer(c, g, footer)
        c.showPage()

    def draw_back_page(c, count):
        dx, dy = job.back_offset
        for slot in backs_slots[:count]:
            # back art is already rendered with bleed, so treat it as the full cell
            img = prepare_image(job.back, job.card_w, job.card_h, 0.0, slot.rot, work_dir)
            moved = Slot(slot.x + dx, slot.y + dy, slot.w, slot.h, slot.rot)
            _draw_card(c, img, moved, job.bleed)
        if job.crop_marks:
            _draw_crop_marks(c, g)
        c.showPage()

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if mode == "duplex":
        c = _new_canvas(out)
        for imgs in pages:
            draw_front_page(c, imgs)
            draw_back_page(c, len(imgs))
        c.save()
        written.append(out)
    elif mode == "single":
        c = _new_canvas(out)
        for imgs in pages:
            draw_front_page(c, imgs)
        c.save()
        written.append(out)
    else:  # split
        f_path = out.with_name(out.stem + "_fronts.pdf")
        b_path = out.with_name(out.stem + "_backs.pdf")
        c = _new_canvas(f_path)
        for imgs in pages:
            draw_front_page(c, imgs)
        c.save()
        c = _new_canvas(b_path)
        order = list(reversed(pages)) if reverse_backs else pages
        for imgs in order:
            draw_back_page(c, len(imgs))
        c.save()
        written += [f_path, b_path]
    log(f"{len(job.fronts)} cards on {len(pages)} sheet(s)")
    return written


def build_calibration(card_w: float, card_h: float, out: Path, bleed: float = 0.0, gap: float = 0.0,
                      margin: float = 5.0, flip: str = "long",
                      back_offset: tuple[float, float] = (0.0, 0.0)) -> Path:
    """Two-page test sheet: numbered outlines + crosshairs on both sides.

    Print it the way you'll print cards, hold it up to a light: outlines and
    crosshairs should line up, and each number should be behind itself.
    """
    pw, ph = config.LETTER_MM
    g = fit_grid(pw, ph, card_w, card_h, bleed=bleed, margin=margin, gap=gap)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(Path(out))

    def page(slots, title, offset=(0.0, 0.0)):
        dx, dy = offset
        c.setLineWidth(0.6)
        for i, s in enumerate(slots, 1):
            x, y = s.x + dx, s.y + dy
            c.rect(x * mm, y * mm, s.w * mm, s.h * mm)
            cx, cy = x + s.w / 2, y + s.h / 2
            c.line((cx - 6) * mm, cy * mm, (cx + 6) * mm, cy * mm)
            c.line(cx * mm, (cy - 6) * mm, cx * mm, (cy + 6) * mm)
            c.setFont("Helvetica-Bold", 22)
            c.drawCentredString(cx * mm, (cy + 9) * mm, str(i))
        c.setFont("Helvetica", 9)
        c.drawCentredString(pw / 2 * mm, (ph - margin + 1) * mm if g.y0 + g.block_h < ph - margin - 2
                            else 3 * mm, title)
        _draw_crop_marks(c, g)
        c.showPage()

    page(g.front_slots(), "CALIBRATION - FRONT (page 1)")
    page(g.back_slots(flip), f"CALIBRATION - BACK (page 2, flip on {flip} edge)", back_offset)
    c.save()
    return Path(out)

"""'Start printing' dialog: every print setting, a diagram of what it does, and a live sheet preview."""
from __future__ import annotations

import io
import time as _time
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF, QTransform)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from . import backs, config, enhance, service
from .layout import fit_grid

RED, DARK, PAPER, INK, MUTED = "#c62832", "#7a1219", "#f4f4f2", "#202124", "#9aa0a6"

MODES = [("duplex", "Double-sided – printer does it (duplex)"),
         ("split", "Double-sided – manual (flip the stack or glue)"),
         ("single", "Single-sided – fronts only")]
# measured on UltraMix-enhanced unit cards at 400 DPI: ~4.5 MB lossless, ~1.1 MB JPEG 95% each
LOSSLESS_BYTES_PER_PX = 1.17
JPEG_BYTES_PER_PX = 0.29


def _mb(v: float) -> str:
    return f"{v / 1000:.1f} GB" if v >= 1000 else f"{v:.0f} MB" if v >= 10 else f"{v:.1f} MB"


FLIPS = [("long", "Long edge (turn side to side)"), ("short", "Short edge (turn end over end)")]

CAPTIONS = {
    "mode": {
        "duplex": "Your printer prints the front, flips the sheet itself and prints the backs. One PDF. "
                  "Leave the printer's duplex setting on “flip on long edge” (the usual default).",
        "split": "Two PDFs. Print the fronts, put the printed stack back in the tray flipped over, "
                 "then print the backs. Or print both on separate sheets and glue them back to back.",
        "single": "Just the card fronts. Use this for sleeves or when you don't want a back.",
    },
    "flip": {
        "long": "The sheet turns over like a book page (side to side). Most printers' default "
                "duplex setting. The backs are mirrored left↔right so they land behind their fronts.",
        "short": "The sheet turns over like a notepad (top to bottom). Pick this if your printer is set "
                 "to flip on the short edge, or that's how you re-feed the stack.",
    },
    "back": "The image printed on the back of every card. The built-in back is original art; "
            "you can also pick your own image (e.g. a scan of your card back).",
    "bleed": "Extends each card's edge colour past the cut line, so a slightly off cut leaves no white "
             "sliver. Cards get their own gap, so fewer may fit per sheet. 0 = cards touch, one cut per line.",
    "gap": "Plain white space between neighbouring cards, so each card is cut on its own (handy with a "
           "paper trimmer or rounded-corner punch). Every card gets its own cut lines; fewer may fit per sheet. "
           "0 = cards touch and share cut lines.",
    "crop": "Short lines in the page margin that show exactly where to cut. They sit outside the cards.",
    "offset": "If the backs print slightly off, shift them here. Print the calibration sheet, hold it up "
              "to a light, measure the gap. +X moves backs right, +Y moves them up (as seen on the back).",
    "small": "AI-enhanced cards go into the PDF at full quality (400 DPI, no compression). Tick this to store "
             "them as high-quality JPEG instead: about a quarter of the file size and it looks the same on paper. "
             "Useful for emailing the PDF or a print shop's upload limit. Original images aren't affected.",
    "reverse": "Manual mode only. If, after re-feeding, the backs come out in the wrong order "
               "(sheet 1's backs on the last sheet), tick this.",
}


# ---- drawing helpers --------------------------------------------------------------
def _page(p: QPainter, r: QRectF, label: str = ""):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawRoundedRect(r.translated(3, 3), 3, 3)
    p.setBrush(QColor(PAPER))
    p.setPen(QPen(QColor("#bbbbbb"), 1))
    p.drawRoundedRect(r, 3, 3)
    if label:
        p.save()
        _small(p)
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(r.left() - 20, r.bottom() + 4, r.width() + 40, 16), Qt.AlignHCenter, label)
        p.restore()


def _front(p: QPainter, r: QRectF, n: str = ""):
    p.setPen(QPen(QColor("#888"), 1))
    p.setBrush(QColor("#ffffff"))
    p.drawRect(r)
    p.fillRect(QRectF(r.left(), r.top(), r.width(), r.height() * 0.22), QColor(RED))
    p.setPen(QColor("#cfcfcf"))
    for i in range(3):
        y = r.top() + r.height() * (0.38 + i * 0.18)
        p.drawLine(QPointF(r.left() + r.width() * 0.12, y), QPointF(r.right() - r.width() * 0.2, y))
    if n:
        _num(p, r, n, INK)


def _back(p: QPainter, r: QRectF, n: str = ""):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(DARK))
    p.drawRect(r)
    p.setBrush(QColor(RED))
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.25, r.top())
    path.lineTo(r.left() + r.width() * 0.6, r.top())
    path.lineTo(r.left() + r.width() * 0.2, r.bottom())
    path.lineTo(r.left(), r.bottom())
    path.closeSubpath()
    p.drawPath(path)
    if n:
        _num(p, r, n, "#ffffff")


def _num(p: QPainter, r: QRectF, n: str, color: str):
    p.save()
    f = QFont(p.font())
    f.setBold(True)
    f.setPointSizeF(max(7.0, min(r.height() * 0.32, 16.0)))
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(r, Qt.AlignCenter, n)
    p.restore()


def _small(p: QPainter, size: float = 8.5, bold: bool = False):
    f = QFont(p.font())
    f.setPointSizeF(size)
    f.setBold(bold)
    p.setFont(f)


def _caption_top(p: QPainter, r: QRectF, text: str, color: str = "#e8eaed"):
    p.save()
    _small(p, 9, True)
    p.setPen(QColor(color))
    p.drawText(QRectF(r.left(), r.top() - 4, r.width(), 18), Qt.AlignHCenter, text)
    p.restore()


def _arrow(p: QPainter, a: QPointF, b: QPointF, bend: float = 0.0, color: str = "#e8eaed"):
    p.save()
    path = QPainterPath(a)
    mid = (a + b) / 2
    ctrl = QPointF(mid.x(), mid.y() - bend)
    path.quadTo(ctrl, b)
    p.setPen(QPen(QColor(color), 2))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    direction = b - ctrl
    length = max((direction.x() ** 2 + direction.y() ** 2) ** 0.5, 1e-6)
    ux, uy = direction.x() / length, direction.y() / length
    head = QPolygonF([b, QPointF(b.x() - 9 * ux + 5 * uy, b.y() - 9 * uy - 5 * ux),
                      QPointF(b.x() - 9 * ux - 5 * uy, b.y() - 9 * uy + 5 * ux)])
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawPolygon(head)
    p.restore()


def _sheet(p: QPainter, r: QRectF, cols: int, rows: int, back: bool, numbers: list[str], mirror: str = ""):
    _page(p, r)
    m = r.width() * 0.1
    cw, ch = (r.width() - 2 * m) / cols, (r.height() - 2 * m) / rows
    i = 0
    for row in range(rows):
        for col in range(cols):
            c, rr = col, row
            if mirror == "long":
                c = cols - 1 - col
            elif mirror == "short":
                rr = rows - 1 - row
            cell = QRectF(r.left() + m + c * cw + 2, r.top() + m + rr * ch + 2, cw - 4, ch - 4)
            (_back if back else _front)(p, cell, numbers[i] if i < len(numbers) else "")
            i += 1


class DiagramWidget(QWidget):
    """Draws the explanation for one setting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(440, 230)
        self.topic = "mode"
        self.state: dict = {}
        self.back_pixmap: QPixmap | None = None
        self.pdf_mb: tuple[float, float] = (0.0, 0.0)  # enhanced cards: full quality, smaller PDF

    def show_topic(self, topic: str, state: dict):
        self.topic, self.state = topic, state
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#26272b"))
        r = QRectF(self.rect()).adjusted(16, 14, -16, -26)
        getattr(self, f"_draw_{self.topic}", self._draw_mode)(p, r)
        p.end()

    def _two_pages(self, r: QRectF):
        w = min(r.height() * 0.85 * 0.77, r.width() * 0.32)
        h = w / 0.77
        gap = r.width() - 2 * w
        top = r.top() + (r.height() - h) / 2 - 6
        left = QRectF(r.left() + gap * 0.15, top, w, h)
        right = QRectF(r.right() - gap * 0.15 - w, top, w, h)
        return left, right

    def _draw_mode(self, p, r):
        mode = self.state.get("mode", "duplex")
        nums = ["1", "2", "3", "4"]
        if mode == "single":
            w = min(r.height() * 0.85 * 0.77, r.width() * 0.32)
            page = QRectF(r.center().x() - w / 2, r.top() + (r.height() - w / 0.77) / 2 - 6, w, w / 0.77)
            _sheet(p, page, 2, 2, False, nums)
            self._labels(p, page, page, "fronts only", "")
            return
        left, right = self._two_pages(r)
        _sheet(p, left, 2, 2, False, nums)
        _sheet(p, right, 2, 2, True, nums, mirror=self.state.get("flip", "long"))
        a = QPointF(left.right() + 6, left.center().y())
        b = QPointF(right.left() - 6, right.center().y())
        _arrow(p, a, b, bend=30)
        if mode == "duplex":
            _caption_top(p, r, "the printer flips the sheet for you")
            self._labels(p, left, right, "front", "back (same sheet)")
        else:
            _caption_top(p, r, "re-feed the printed stack flipped over – or glue two sheets")
            self._labels(p, left, right, "…_fronts.pdf", "…_backs.pdf")

    def _labels(self, p, left, right, a, b):
        p.save()
        _small(p)
        p.setPen(QColor(MUTED))
        for rect, text in ((left, a), (right, b)):
            if text:
                p.drawText(QRectF(rect.left() - 40, rect.bottom() + 4, rect.width() + 80, 16),
                           Qt.AlignHCenter, text)
        p.restore()

    def _draw_flip(self, p, r):
        flip = self.state.get("flip", "long")
        left, right = self._two_pages(r)
        nums = ["1", "2", "3", "4"]
        _sheet(p, left, 2, 2, False, nums)
        _sheet(p, right, 2, 2, True, nums, mirror=flip)
        blue = "#57c8ff"
        p.save()
        p.setPen(QPen(QColor(blue), 2, Qt.DashLine))
        if flip == "long":
            p.drawLine(QPointF(left.center().x(), left.top() - 6), QPointF(left.center().x(), left.bottom() + 6))
            p.restore()
            _arrow(p, QPointF(left.left() + 4, left.top() - 4), QPointF(left.right() - 4, left.top() - 4),
                   bend=16, color=blue)
            _caption_top(p, r, "turned over side to side (like a book page)", blue)
        else:
            p.drawLine(QPointF(left.left() - 6, left.center().y()), QPointF(left.right() + 6, left.center().y()))
            p.restore()
            x = left.left() - 10
            _arrow(p, QPointF(x, left.top() + 4), QPointF(x, left.bottom() - 4), bend=0, color=blue)
            _caption_top(p, r, "turned over end over end (like a notepad)", blue)
        _arrow(p, QPointF(left.right() + 6, left.center().y()), QPointF(right.left() - 6, right.center().y()),
               bend=30)
        self._labels(p, left, right, "front", "what you see on the back")

    def _draw_back(self, p, r):
        h = r.height() * 0.9
        ratio = self.state.get("ratio", 148 / 105)
        w = min(h * ratio, r.width() * 0.6)
        h = w / ratio
        card = QRectF(r.center().x() - w / 2, r.top() + (r.height() - h) / 2, w, h)
        if self.back_pixmap and not self.back_pixmap.isNull():
            p.drawPixmap(card.toRect(), self.back_pixmap)
        else:
            _back(p, card)
        p.setPen(QPen(QColor("#888"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(card)

    def _draw_small(self, p, r):
        full, small = self.pdf_mb
        on = self.state.get("small_pdf", False)
        _small(p)
        top = r.top() + 10
        bar_max = r.width() - 190
        for i, (label, mb, chosen) in enumerate((("Full quality", full, not on), ("Smaller PDF", small, on))):
            y = top + i * 46
            p.setPen(QColor("#e8eaed" if chosen else MUTED))
            p.drawText(QRectF(r.left(), y, 110, 24), Qt.AlignVCenter, label)
            w = max(bar_max * (mb / full if full else 0), 4)
            p.fillRect(QRectF(r.left() + 115, y + 4, w, 16), QColor(RED if chosen else "#555"))
            p.drawText(QRectF(r.left() + 122 + w, y, 90, 24), Qt.AlignVCenter, f"≈ {_mb(mb)}")
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(r.left(), top + 100, r.width(), 40), Qt.TextWordWrap,
                   "Estimated size of the AI-enhanced cards in this PDF (each card is stored once, "
                   "however many copies you print).")

    def _draw_bleed(self, p, r):
        bleed = float(self.state.get("bleed", 0))
        w = r.width() * 0.42
        h = w / 1.41
        cut = QRectF(r.center().x() - w / 2, r.top() + (r.height() - h) / 2, w, h)
        ext = min(bleed, 5) * 6  # exaggerated so it's visible
        art = cut.adjusted(-ext, -ext, ext, ext)
        p.fillRect(art.adjusted(-14, -14, 14, 14), QColor(PAPER))
        p.fillRect(art, QColor(RED))
        p.fillRect(cut.adjusted(w * 0.08, h * 0.25, -w * 0.08, -h * 0.12), QColor("#ffffff"))
        p.setPen(QPen(QColor(INK), 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(cut)
        p.save()
        _small(p, 8)
        p.setPen(QColor(INK))
        p.drawText(QRectF(cut.left(), cut.top() - 16 - ext, cut.width(), 14), Qt.AlignHCenter, "cut line")
        p.restore()
        _small(p)
        p.setPen(QColor("#e8eaed"))
        txt = (f"{bleed:g} mm of edge colour past the cut line" if bleed
               else "No bleed: an off cut shows a white sliver")
        p.drawText(QRectF(r.left(), r.bottom() - 2, r.width(), 18), Qt.AlignHCenter, txt)

    def _draw_gap(self, p, r):
        gap = float(self.state.get("gap", 0))
        w = r.width() * 0.3
        h = w / 1.41
        px = min(gap, 10) * 5  # exaggerated so small gaps are visible
        total = 2 * w + px
        left = QRectF(r.center().x() - total / 2, r.top() + (r.height() - h) / 2 - 8, w, h)
        right = left.translated(w + px, 0)
        p.fillRect(left.adjusted(-14, -14, w + px + 14, 14), QColor(PAPER))
        _front(p, left, "1")
        _front(p, right, "2")
        if gap:
            y = left.bottom() + 10
            _arrow(p, QPointF(left.right() + px / 2, y), QPointF(left.right(), y), color="#57c8ff")
            _arrow(p, QPointF(left.right() + px / 2, y), QPointF(right.left(), y), color="#57c8ff")
        p.save()
        _small(p)
        p.setPen(QColor("#e8eaed"))
        p.drawText(QRectF(r.left(), r.bottom() - 2, r.width(), 18), Qt.AlignHCenter,
                   f"{gap:g} mm of white space between cards" if gap else "No spacing: neighbours share one cut")
        p.restore()

    def _draw_crop(self, p, r):
        on = self.state.get("crop", True)
        page = QRectF(r.left() + r.width() * 0.2, r.top(), r.width() * 0.6, r.height() * 0.95)
        _page(p, page)
        m = 18
        cards = QRectF(page.left() + m + 10, page.top() + m + 10, page.width() - 2 * m - 20, page.height() - 2 * m - 20)
        half = cards.width() / 2
        for i in range(2):
            _front(p, QRectF(cards.left() + i * half, cards.top(), half, cards.height() / 2))
            _front(p, QRectF(cards.left() + i * half, cards.top() + cards.height() / 2, half, cards.height() / 2))
        if on:
            p.setPen(QPen(QColor(INK), 1.4))
            for x in (cards.left(), cards.left() + half, cards.right()):
                p.drawLine(QPointF(x, page.top() + 3), QPointF(x, cards.top() - 4))
                p.drawLine(QPointF(x, cards.bottom() + 4), QPointF(x, page.bottom() - 3))
            for y in (cards.top(), cards.center().y(), cards.bottom()):
                p.drawLine(QPointF(page.left() + 3, y), QPointF(cards.left() - 4, y))
                p.drawLine(QPointF(cards.right() + 4, y), QPointF(page.right() - 3, y))

    def _draw_offset(self, p, r):
        dx, dy = self.state.get("offset", (0.0, 0.0))
        w = r.width() * 0.34
        h = w / 1.41
        base = QRectF(r.center().x() - w / 2, r.top() + (r.height() - h) / 2, w, h)
        scale = 8  # exaggerate mm so small shifts are visible
        moved = base.translated(dx * scale, -dy * scale)
        _back(p, moved)
        p.setPen(QPen(QColor("#57c8ff"), 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(base)
        if dx or dy:
            _arrow(p, base.center(), moved.center(), color="#57c8ff")
        p.save()
        _small(p)
        p.setPen(QColor("#e8eaed"))
        p.drawText(QRectF(r.left(), r.bottom() - 2, r.width(), 18), Qt.AlignHCenter,
                   f"dashed = normal position · red = shifted X {dx:+g} mm, Y {dy:+g} mm")
        p.restore()

    def _draw_reverse(self, p, r):
        on = self.state.get("reverse", False)
        for k, (title, order) in enumerate((("fronts printed", ["1", "2", "3"]),
                                            ("backs printed", ["3", "2", "1"] if on else ["1", "2", "3"]))):
            x = r.left() + r.width() * (0.18 + k * 0.42)
            for i, n in enumerate(order):
                rect = QRectF(x + i * 12, r.top() + 6 + i * 12, r.width() * 0.2, r.height() * 0.6)
                (_back if k else _front)(p, rect, n)
            p.save()
            _small(p)
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(x - 20, r.bottom() - 4, r.width() * 0.3, 16), Qt.AlignHCenter, title)
            p.restore()


# ---- live preview -----------------------------------------------------------------
class SheetPreview(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.setMinimumSize(200, 260)
        self.grid = None
        self.images: list[QPixmap | None] = []
        self.back = False
        self.offset = (0.0, 0.0)
        self.flip = "long"
        self.crop = True
        self.empty_text = ""
        self.images_include_bleed = False

    def set_data(self, grid, images, back=False, offset=(0.0, 0.0), flip="long", crop=True, empty_text="",
                 images_include_bleed=False):
        self.grid, self.images, self.back = grid, images, back
        self.images_include_bleed = images_include_bleed
        self.offset, self.flip, self.crop, self.empty_text = offset, flip, crop, empty_text
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#26272b"))
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(0, 4, self.width(), 18), Qt.AlignHCenter, self.title)
        g = self.grid
        area = QRectF(self.rect()).adjusted(10, 26, -10, -10)
        if g is None:
            p.drawText(area, Qt.AlignCenter | Qt.TextWordWrap, self.empty_text)
            p.end()
            return
        s = min(area.width() / g.page_w, area.height() / g.page_h)
        page = QRectF(area.center().x() - g.page_w * s / 2, area.top(), g.page_w * s, g.page_h * s)
        p.fillRect(page, QColor("#ffffff"))

        def to_px(x_mm, y_mm, w_mm, h_mm):  # PDF origin is bottom-left
            return QRectF(page.left() + x_mm * s, page.bottom() - (y_mm + h_mm) * s, w_mm * s, h_mm * s)

        slots = g.back_slots(self.flip) if self.back else g.front_slots()
        dx, dy = self.offset if self.back else (0.0, 0.0)
        for slot, pix in zip(slots, self.images):
            cell = to_px(slot.x - g.bleed + dx, slot.y - g.bleed + dy, slot.w + 2 * g.bleed, slot.h + 2 * g.bleed)
            if pix is None or pix.isNull():
                _back(p, cell) if self.back else _front(p, cell)
                continue
            if slot.rot:
                pix = pix.transformed(QTransform().rotate(-slot.rot), Qt.SmoothTransformation)
            if self.images_include_bleed or g.bleed <= 0:
                p.drawPixmap(cell, pix, QRectF(pix.rect()))
            else:
                trim = to_px(slot.x + dx, slot.y + dy, slot.w, slot.h)
                _draw_with_edge_bleed(p, pix, trim, cell)
        if self.crop:
            p.setPen(QPen(QColor("#000000"), 1))
            xs, ys = g.cut_lines()
            top, bottom = g.y0 + g.block_h, g.y0
            left, right = g.x0, g.x0 + g.block_w
            for x in xs:
                for y0, y1 in ((bottom - 6, bottom - 1), (top + 1, top + 6)):
                    a, b = to_px(x, y0, 0, 0).topLeft(), to_px(x, y1, 0, 0).topLeft()
                    p.drawLine(a, b)
            for y in ys:
                for x0, x1 in ((left - 6, left - 1), (right + 1, right + 6)):
                    p.drawLine(to_px(x0, y, 0, 0).topLeft(), to_px(x1, y, 0, 0).topLeft())
        p.end()


def _draw_with_edge_bleed(p: QPainter, pix: QPixmap, trim: QRectF, cell: QRectF):
    """Draw the card at exactly its trim size and fill the bleed band by stretching its
    outermost pixels outward, the same idea as the PDF's edge-extended bleed."""
    w, h = pix.width(), pix.height()
    L, T = trim.left() - cell.left(), trim.top() - cell.top()
    R, B = cell.right() - trim.right(), cell.bottom() - trim.bottom()
    src = lambda x, y, sw, sh: QRectF(x, y, sw, sh)
    # edges
    p.drawPixmap(QRectF(cell.left(), trim.top(), L, trim.height()), pix, src(0, 0, 1, h))
    p.drawPixmap(QRectF(trim.right(), trim.top(), R, trim.height()), pix, src(w - 1, 0, 1, h))
    p.drawPixmap(QRectF(trim.left(), cell.top(), trim.width(), T), pix, src(0, 0, w, 1))
    p.drawPixmap(QRectF(trim.left(), trim.bottom(), trim.width(), B), pix, src(0, h - 1, w, 1))
    # corners
    for x, y, cw, ch, sx, sy in ((cell.left(), cell.top(), L, T, 0, 0),
                                 (trim.right(), cell.top(), R, T, w - 1, 0),
                                 (cell.left(), trim.bottom(), L, B, 0, h - 1),
                                 (trim.right(), trim.bottom(), R, B, w - 1, h - 1)):
        p.drawPixmap(QRectF(x, y, cw, ch), pix, src(sx, sy, 1, 1))
    p.drawPixmap(trim, pix, QRectF(pix.rect()))


def _pil_to_pixmap(img) -> QPixmap:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    qimg = QImage.fromData(buf.getvalue(), "PNG")
    return QPixmap.fromImage(qimg)


# ---- dialog -----------------------------------------------------------------------
class PrintDialog(QDialog):
    calibration_requested = Signal()

    def __init__(self, parent, selections: dict[str, list], images: dict[str, Path | None], prefs: dict,
                 enhanced: dict[str, Path] | None = None):
        super().__init__(parent)
        self.enhanced = enhanced or {}
        self.setWindowTitle("Start printing")
        self.resize(1100, 720)
        self.selections, self.images, self.prefs = selections, images, dict(prefs)
        self._thumbs: dict[str, QPixmap] = {}
        self._backs: dict[tuple, QPixmap] = {}

        outer = QHBoxLayout(self)
        left = QVBoxLayout()
        outer.addLayout(left, 0)
        right = QVBoxLayout()
        outer.addLayout(right, 1)

        box = QGroupBox("Print settings")
        form = QFormLayout(box)
        self.mode = QComboBox()
        for k, label in MODES:
            self.mode.addItem(label, k)
        self.flip = QComboBox()
        for k, label in FLIPS:
            self.flip.addItem(label, k)
        self.custom_back = Path(prefs["custom_back"]) if prefs.get("custom_back") else None
        self.use_custom = False
        backw = QWidget()
        bl = QHBoxLayout(backw)
        bl.setContentsMargins(0, 0, 0, 0)
        self.back_label = QLineEdit()
        self.back_label.setReadOnly(True)
        self.back_browse = QPushButton("Browse…")
        self.back_browse.setToolTip("Use your own image for the card backs (e.g. a scan of your card back)")
        self.back_default = QPushButton("Default")
        self.back_default.setToolTip("Go back to the default red hex placeholder")
        bl.addWidget(self.back_label, 1)
        bl.addWidget(self.back_browse)
        bl.addWidget(self.back_default)
        self.back = backw
        self.bleed = QDoubleSpinBox()
        self.bleed.setRange(0, 5)
        self.bleed.setSingleStep(0.5)
        self.bleed.setSuffix(" mm")
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0, 10)
        self.gap.setSingleStep(0.5)
        self.gap.setSuffix(" mm")
        offw = QWidget()
        off = QHBoxLayout(offw)
        off.setContentsMargins(0, 0, 0, 0)
        off.setSpacing(4)
        self.off_x, self.off_y = QDoubleSpinBox(), QDoubleSpinBox()
        for i, (sp, lab) in enumerate(((self.off_x, "X"), (self.off_y, "Y"))):
            sp.setRange(-10, 10)
            sp.setSingleStep(0.25)
            sp.setDecimals(2)
            sp.setSuffix(" mm")
            sp.setFixedWidth(100)
            if i:
                off.addSpacing(14)
            off.addWidget(QLabel(lab))
            off.addWidget(sp)
        off.addStretch()
        self.crop = QCheckBox("Crop marks")
        self.reverse = QCheckBox("Reverse back page order")
        checks = QWidget()
        cl = QHBoxLayout(checks)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.crop)
        cl.addSpacing(16)
        cl.addWidget(self.reverse)
        # Smaller PDF: only offered when the PDF would contain full-quality AI-enhanced images
        # (space-saving copies are already JPEG; originals are small anyway)
        self.small_pdf = QCheckBox("Smaller PDF")
        sel_codes = {c.code for sel in selections.values() for c, _ in sel}
        self.ai_count = (len(sel_codes & set(self.enhanced)), len(sel_codes))
        self.small_applies = any(self.enhanced[c].suffix.lower() not in (".jpg", ".jpeg")
                                 for c in sel_codes & set(self.enhanced))
        cl.addSpacing(16)
        cl.addWidget(self.small_pdf)
        self.small_pdf.setVisible(self.small_applies)
        cl.addStretch()
        rows = [("Mode", self.mode, "mode"), ("Flip", self.flip, "flip"), ("Back", self.back, "back"),
                ("Bleed", self.bleed, "bleed"), ("Card spacing", self.gap, "gap"),
                ("Back offset", offw, "offset"), ("", checks, None)]
        self._topic_of = {}
        for label, w, topic in rows:
            form.addRow(label, w)
            if topic:
                self._watch(w, topic)
        self._watch(self.crop, "crop")
        self._watch(self.reverse, "reverse")
        self._watch(self.small_pdf, "small")
        left.addWidget(box)

        self.diagram = DiagramWidget()
        left.addWidget(self.diagram, 1)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        self.caption.setMinimumHeight(70)
        self.caption.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        left.addWidget(self.caption)

        head = QHBoxLayout()
        head.addWidget(QLabel("Preview"))
        self.which = QComboBox()
        for t in selections:
            self.which.addItem(config.CARD_TYPES[t].label, t)
        self.which.setVisible(len(selections) > 1)
        head.addWidget(self.which)
        head.addStretch()
        self.info = QLabel()
        self.info.setObjectName("muted")
        head.addWidget(self.info)
        right.addLayout(head)
        pv = QHBoxLayout()
        self.pv_front = SheetPreview("Sheet 1 – front")
        self.pv_back = SheetPreview("Sheet 1 – back")
        pv.addWidget(self.pv_front)
        pv.addWidget(self.pv_back)
        right.addLayout(pv, 1)
        hint = QLabel("Print at 100% / Actual size – turn off “Fit to page”.")
        hint.setObjectName("muted")
        right.addWidget(hint)

        btns = QHBoxLayout()
        cal = QPushButton("Calibration sheet…")
        cal.setToolTip("Test front/back alignment with a 2-page sheet before using cardstock")
        cal.clicked.connect(self._calibration)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        n = len(selections)
        self.save = QPushButton("Save PDF…" if n == 1 else f"Save {n} PDFs…")
        self.save.setObjectName("accent")
        self.save.setDefault(True)
        self.save.clicked.connect(self.accept)
        btns.addWidget(cal)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(self.save)
        right.addLayout(btns)

        self._load(prefs)
        for w in (self.mode, self.flip, self.which):
            w.currentIndexChanged.connect(self._changed)
        self.back_browse.clicked.connect(self._browse_back)
        self.back_default.clicked.connect(self._default_back)
        for w in (self.bleed, self.gap, self.off_x, self.off_y):
            w.valueChanged.connect(self._changed)
        for w in (self.crop, self.reverse, self.small_pdf):
            w.toggled.connect(self._changed)
        self.small_pdf.toggled.connect(lambda: self._pin("small"))
        self.mode.currentIndexChanged.connect(lambda: self._pin("mode"))
        self.flip.currentIndexChanged.connect(lambda: self._pin("flip"))
        self.bleed.valueChanged.connect(lambda: self._pin("bleed"))
        self.gap.valueChanged.connect(lambda: self._pin("gap"))
        for w in (self.off_x, self.off_y):
            w.valueChanged.connect(lambda: self._pin("offset"))
        self.crop.toggled.connect(lambda: self._pin("crop"))
        self.reverse.toggled.connect(lambda: self._pin("reverse"))
        self._current_topic = self._pinned = "mode"
        self._quiet_until = 0.0
        self._changed()

    # -- prefs <-> widgets --
    def _load(self, p: dict):
        self.mode.setCurrentIndex(max(self.mode.findData(p.get("mode", "duplex")), 0))
        self.flip.setCurrentIndex(max(self.flip.findData(p.get("flip", "long")), 0))
        self.bleed.setValue(float(p.get("bleed", 0)))
        self.gap.setValue(float(p.get("gap", 0)))
        self.off_x.setValue(float(p.get("off_x", 0)))
        self.off_y.setValue(float(p.get("off_y", 0)))
        self.crop.setChecked(bool(p.get("crop", True)))
        self.reverse.setChecked(bool(p.get("reverse", False)))
        self.use_custom = bool(p.get("use_custom_back") and self.custom_back and self.custom_back.exists())
        self._show_back()
        self.small_pdf.setChecked(bool(p.get("small_pdf", False)))

    def result_prefs(self) -> dict:
        return {"mode": self.mode.currentData(), "flip": self.flip.currentData(),
                "bleed": self.bleed.value(), "gap": self.gap.value(),
                "off_x": self.off_x.value(), "off_y": self.off_y.value(),
                "crop": self.crop.isChecked(), "reverse": self.reverse.isChecked(),
                "custom_back": str(self.custom_back) if self.custom_back else None,
                "use_custom_back": self.use_custom,
                # a hidden tick (nothing it would affect) keeps the saved choice
                "small_pdf": self.small_pdf.isChecked() if self.small_applies
                else bool(self.prefs.get("small_pdf", False))}

    def settings(self, card_type: str) -> service.PrintSettings:
        s = settings_from_prefs({**self.prefs, **self.result_prefs()}, card_type)
        s.enhanced = s.enhanced and bool(self.enhanced)
        return s

    # -- interaction --
    def _watch(self, w: QWidget, topic: str):
        for x in [w, *w.findChildren(QWidget)]:
            self._topic_of[x] = topic
            x.installEventFilter(self)

    def eventFilter(self, obj, ev):  # noqa: N802 - Qt API
        """The diagram follows the setting you last used ("pinned"). Hovering another setting
        previews it; moving off reverts. Right after a change, hover is ignored briefly so a
        closing dropdown that leaves the mouse over another control doesn't steal the diagram."""
        topic = self._topic_of.get(obj)
        if topic is None or not obj.isEnabled():
            return False
        t = ev.type()
        if t == QEvent.FocusIn:
            self._pin(topic)
        elif t == QEvent.Enter and _time.monotonic() >= self._quiet_until:
            self._topic(topic, pin=False)
        elif t == QEvent.Leave and self._current_topic != self._pinned:
            self._topic(self._pinned, pin=False)
        return False

    def _pin(self, topic: str):
        self._pinned = topic
        self._quiet_until = _time.monotonic() + 0.8
        self._topic(topic)

    def _topic(self, topic: str, pin: bool = True):
        if topic == "flip" and not self.flip.isEnabled():
            topic = "mode"
        if pin:
            self._pinned = topic
        self._current_topic = topic
        state = self._state()
        self.diagram.show_topic(topic, state)
        cap = CAPTIONS[topic]
        self.caption.setText(cap[state["mode"]] if topic == "mode" else
                             cap[state["flip"]] if topic == "flip" else cap)

    def _state(self) -> dict:
        t = self.which.currentData() or next(iter(self.selections))
        w, h = config.CARD_TYPES[t].size_mm
        return {"mode": self.mode.currentData(), "flip": self.flip.currentData(),
                "bleed": self.bleed.value(), "gap": self.gap.value(),
                "offset": (self.off_x.value(), self.off_y.value()),
                "crop": self.crop.isChecked(), "reverse": self.reverse.isChecked(), "ratio": w / h,
                "small_pdf": self.small_pdf.isChecked()}

    def _show_back(self):
        if self.use_custom and self.custom_back:
            self.back_label.setText(self.custom_back.name)
            self.back_label.setToolTip(str(self.custom_back))
        else:
            self.back_label.setText("Default – red hex placeholder")
            self.back_label.setToolTip("Original placeholder art built into the app")
        self.back_default.setVisible(self.use_custom)

    def _browse_back(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose back image", str(self.custom_back or ""),
                                              "Images (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.custom_back, self.use_custom = Path(path), True
            self._show_back()
            self._changed()
        self._pin("back")

    def _default_back(self):
        self.use_custom = False
        self._show_back()
        self._changed()
        self._pin("back")

    def _calibration(self):
        self.calibration_requested.emit()

    def _thumb(self, path: Path | None) -> QPixmap | None:
        if path is None:
            return None
        key = str(path)
        if key not in self._thumbs:
            img = QImage(key)
            self._thumbs[key] = QPixmap.fromImage(img.scaledToWidth(min(img.width(), 400),
                                                                    Qt.SmoothTransformation)) if not img.isNull() else None
        return self._thumbs[key]

    def _pdf_estimate(self) -> tuple[float, float]:
        """(full quality, smaller PDF) MB for the selected full-quality enhanced cards, all PDFs together."""
        full = small = 0.0
        for t, sel in self.selections.items():
            px = enhance.print_width_px(t, enhance.PRINT_DPI) ** 2 / config.CARD_TYPES[t].size_mm[0] \
                * config.CARD_TYPES[t].size_mm[1]
            n = sum(1 for c, _ in sel if c.code in self.enhanced
                    and self.enhanced[c.code].suffix.lower() not in (".jpg", ".jpeg"))
            full += n * px * LOSSLESS_BYTES_PER_PX / 1e6
            small += n * px * JPEG_BYTES_PER_PX / 1e6
        return full, small

    def _back_pixmap(self, s: service.PrintSettings) -> QPixmap | None:
        w, h = s.card_size
        if s.back:
            return self._thumb(Path(s.back))
        key = (w, h, s.bleed)
        if key not in self._backs:
            self._backs[key] = _pil_to_pixmap(backs.render_back(w, h, dpi=70, bleed_mm=s.bleed))
        return self._backs[key]

    def _changed(self, *_):
        mode = self.mode.currentData()
        self.reverse.setEnabled(mode == "split")
        duplex = mode == "duplex"
        if duplex and self.flip.currentData() != "long":
            self.flip.blockSignals(True)
            self.flip.setCurrentIndex(self.flip.findData("long"))
            self.flip.blockSignals(False)
        self.flip.setEnabled(mode == "split")
        self.flip.setToolTip("Duplex printing: keep your printer on “flip on long edge”." if duplex else
                             "Fronts only: nothing is printed on the back." if mode == "single" else "")
        t = self.which.currentData() or next(iter(self.selections))
        s = self.settings(t)
        fronts = [self.enhanced.get(c.code) or self.images.get(c.code) for c, q in self.selections[t]
                  for _ in range(q)]
        try:
            g = fit_grid(*config.LETTER_MM, *s.card_size, bleed=s.bleed, margin=s.margin, gap=s.gap)
        except ValueError as exc:
            self.pv_front.set_data(None, [], empty_text=str(exc))
            self.pv_back.set_data(None, [], empty_text="")
            self.save.setEnabled(False)
            return
        self.save.setEnabled(True)
        first = [self._thumb(pth) for pth in fronts[:g.per_page]]
        self.pv_front.set_data(g, first, crop=s.crop_marks)
        if s.mode == "single":
            self.pv_back.set_data(None, [], empty_text="Fronts only – nothing printed on the back")
        else:
            bp = self._back_pixmap(s)
            self.pv_back.set_data(g, [bp] * len(first), back=True, offset=s.back_offset, flip=s.flip,
                                  crop=s.crop_marks, images_include_bleed=s.back is None)
        sheets = service.sheets_needed(s, len(fronts))
        self.info.setText(f"{len(fronts)} card{'s' * (len(fronts) != 1)} · {g.per_page} per sheet · {sheets} sheet{'s' * (sheets != 1)}"
                          f"{' · cards rotated to fit' if g.rotated else ''}"
                          + (f" · AI-enhanced: {self.ai_count[0]} of {self.ai_count[1]}" if self.ai_count[0] else ""))
        self.diagram.pdf_mb = self._pdf_estimate()
        self.diagram.back_pixmap = self._back_pixmap(s) if s.mode != "single" else None
        self._topic(getattr(self, "_current_topic", "mode"))


def settings_from_prefs(p: dict, card_type: str) -> service.PrintSettings:
    custom = Path(p["custom_back"]) if p.get("use_custom_back") and p.get("custom_back") else None
    return service.PrintSettings(card_type=card_type, mode=p.get("mode", "duplex"), flip=p.get("flip", "long"),
                                 bleed=float(p.get("bleed", 0)), gap=float(p.get("gap", 0)), back=custom,
                                 back_offset=(float(p.get("off_x", 0)), float(p.get("off_y", 0))),
                                 crop_marks=bool(p.get("crop", True)), reverse_backs=bool(p.get("reverse", False)),
                                 enhanced=bool(p.get("ai_enabled", False)), small_pdf=bool(p.get("small_pdf", False)))

"""Sheet geometry: how many cards fit, where they go, and where their backs go.

All units are millimetres with the origin at the page's bottom-left (PDF style).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    x: float      # bottom-left of the trim box (card edge, excluding bleed)
    y: float
    w: float      # trim size as placed on the sheet (swapped if rotated)
    h: float
    rot: int      # degrees CCW the card art is rotated on the sheet (0 or 90 / -90 / 180)


@dataclass(frozen=True)
class Grid:
    page_w: float
    page_h: float
    card_w: float
    card_h: float
    bleed: float
    cols: int
    rows: int
    rotated: bool
    x0: float     # bottom-left of the whole block of cells (including bleed)
    y0: float
    gap: float = 0.0  # white space between neighbouring cells (card spacing)

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    @property
    def placed_w(self) -> float:
        return self.card_h if self.rotated else self.card_w

    @property
    def placed_h(self) -> float:
        return self.card_w if self.rotated else self.card_h

    @property
    def cell_w(self) -> float:
        return self.placed_w + 2 * self.bleed

    @property
    def cell_h(self) -> float:
        return self.placed_h + 2 * self.bleed

    @property
    def block_w(self) -> float:
        return self.cols * self.cell_w + (self.cols - 1) * self.gap

    @property
    def block_h(self) -> float:
        return self.rows * self.cell_h + (self.rows - 1) * self.gap

    def _slot(self, col: int, row_from_top: int, rot: int) -> Slot:
        x = self.x0 + col * (self.cell_w + self.gap) + self.bleed
        y = self.y0 + (self.rows - 1 - row_from_top) * (self.cell_h + self.gap) + self.bleed
        return Slot(x, y, self.placed_w, self.placed_h, rot)

    def front_slots(self) -> list[Slot]:
        rot = 90 if self.rotated else 0
        return [self._slot(c, r, rot) for r in range(self.rows) for c in range(self.cols)]

    def back_slots(self, flip: str = "long") -> list[Slot]:
        """Back position for each front slot, for the given duplex flip edge.

        long:  sheet turned over its long (vertical) edge  -> columns mirror
        short: sheet turned over its short (top) edge      -> rows mirror
        Rotation keeps each back's "up" aligned with its front's "up".
        """
        front_rot = 90 if self.rotated else 0
        out = []
        for r in range(self.rows):
            for c in range(self.cols):
                if flip == "long":
                    out.append(self._slot(self.cols - 1 - c, r, (-front_rot) % 360))
                elif flip == "short":
                    out.append(self._slot(c, self.rows - 1 - r, (front_rot + 180) % 360))
                else:
                    raise ValueError("flip must be 'long' or 'short'")
        return out

    def cut_lines(self) -> tuple[list[float], list[float]]:
        """Distinct x and y positions of card trim edges, for crop marks."""
        xs, ys = set(), set()
        for s in self.front_slots():
            xs.update((round(s.x, 3), round(s.x + s.w, 3)))
            ys.update((round(s.y, 3), round(s.y + s.h, 3)))
        return sorted(xs), sorted(ys)


def fit_grid(page_w: float, page_h: float, card_w: float, card_h: float,
             bleed: float = 0.0, margin: float = 5.0, gap: float = 0.0) -> Grid:
    """Pick the card orientation that fits the most cards inside the margins.

    Cells touch (shared cut lines) when bleed is 0; with bleed each card gets its
    own bleed area so neighbours never print into each other. gap adds plain white space
    between neighbouring cards (card spacing) on top of any bleed.
    """
    usable_w, usable_h = page_w - 2 * margin, page_h - 2 * margin
    best = None
    for rotated in (False, True):
        pw, ph = (card_h, card_w) if rotated else (card_w, card_h)
        cols = int((usable_w + gap + 1e-9) // (pw + 2 * bleed + gap))
        rows = int((usable_h + gap + 1e-9) // (ph + 2 * bleed + gap))
        if cols * rows == 0:
            continue
        if best is None or cols * rows > best[0] * best[1]:
            best = (cols, rows, rotated, pw, ph)
    if best is None:
        raise ValueError(f"a {card_w}x{card_h}mm card (bleed {bleed}mm) does not fit the page")
    cols, rows, rotated, pw, ph = best
    block_w = cols * (pw + 2 * bleed) + (cols - 1) * gap
    block_h = rows * (ph + 2 * bleed) + (rows - 1) * gap
    return Grid(page_w, page_h, card_w, card_h, bleed, cols, rows, rotated,
                x0=(page_w - block_w) / 2, y0=(page_h - block_h) / 2, gap=gap)

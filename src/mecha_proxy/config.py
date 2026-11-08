"""Static configuration: card types, physical sizes, source site layout.

Physical sizes are community measurements / aspect-ratio estimates and can be
overridden at runtime with --size. Update here once confirmed with a ruler.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import __version__

SITE_BASE = "https://global.bandai-hobby.net/en-us/site/gundam-assemble/"
# The site's CDN rejects non-browser user agents outright (even robots.txt), so we
# use a browser-style string that still names this tool honestly.
USER_AGENT = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mecha-Assemble-Proxy-Helper/{__version__}"
# Where the app itself is published (update check, About box)
REPO = "Shabbypenguin/Mecha-Assemble-Proxy-Helper"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"  # skips drafts/pre-releases
REQUEST_DELAY_S = 0.5  # politeness delay between network requests
TIMEOUT_S = 30

LETTER_MM = (215.9, 279.4)
MM_PER_PT = 25.4 / 72.0


@dataclass(frozen=True)
class CardType:
    key: str            # "unit" | "tactics"
    width_mm: float     # as the card is read (landscape units are wider than tall)
    height_mm: float
    list_path: str      # list page path relative to SITE_BASE
    img_section: str    # path segment in image URLs
    code_pattern: str   # regex a card code must match, e.g. ex02-u03 / ex02-t08 / ex02-u02a
    front_variant: bool # unit cards expose a separate img_card_front_* image
    size_note: str
    label: str = ""     # human label, e.g. "Unit cards"
    stats: tuple[str, ...] = ()  # numeric stats shown on the card (filterable ranges)

    @property
    def size_mm(self) -> tuple[float, float]:
        return (self.width_mm, self.height_mm)


CARD_TYPES: dict[str, CardType] = {
    "unit": CardType(
        key="unit",
        width_mm=148.0,
        height_mm=105.0,
        list_path="unitlist/?search=&tag=all&view=card&category=all",
        img_section="unitlist",
        code_pattern=r"^[a-z]{2}\d{2}-u\d{2,3}$",
        front_variant=True,
        size_note="A6 landscape, community-reported; matches image aspect 800x567. Unconfirmed.",
        label="Unit cards",
        stats=("hp", "vp", "tl"),
    ),
    "tactics": CardType(
        key="tactics",
        width_mm=63.0,
        height_mm=88.0,
        list_path="tacticscard/?search=&category=all&tag=all",
        img_section="tacticscard",
        code_pattern=r"^[a-z]{2}\d{2}-t\d{2,3}$",
        front_variant=False,
        size_note="Estimated from image aspect 746x1044 (poker size). Unconfirmed.",
        label="Tactics cards",
    ),
    "other": CardType(
        key="other",
        width_mm=88.0,
        height_mm=63.0,
        list_path="othercard/?search=&category=all&tag=all",
        img_section="othercard",
        code_pattern=r"^[a-z]{2}\d{2}-[a-z]\d{2,3}[a-z]$",
        front_variant=False,
        size_note="Token cards (e.g. remote weapons). Estimated from image aspect 793x567 "
                  "(poker size, landscape). Unconfirmed.",
        label="Other cards",
        stats=("hp", "vp"),
    ),
}


def parse_size(text: str) -> tuple[float, float]:
    """'148x105' -> (148.0, 105.0) in mm."""
    try:
        w, h = (float(p) for p in text.lower().replace("mm", "").split("x"))
    except ValueError as exc:
        raise ValueError(f"size must look like 148x105 (mm), got {text!r}") from exc
    if w <= 0 or h <= 0:
        raise ValueError("size must be positive")
    return w, h

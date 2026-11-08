"""Deck codes: a plain-text list of cards to share (e.g. over Discord).

Written as one card per line, quantity then code:

    1x ST01-U02
    2x ST01-T02

Reading is forgiving, because people type these by hand. All of these mean one or two
copies of ST01-T01:

    1x st01-t01    1 x ST01-T01    1xst01t01    st01t01    ST01_T01 x1    2 st01-t01 Epic Shot

Anything else on a line (card names, comments, bullets) is ignored. Only codes the app
knows are selected; the rest are reported back.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

MAX_CHARS = 20_000      # a deck is a few hundred characters; refuse pasted novels
MAX_LINES = 500
MAX_QTY = 99            # same limit as the copies box on each card

# set (2 letters + 2 digits), optional separator, kind letter + 2-3 digits, optional suffix
# letter (token cards, e.g. ex01-r01a). An "x" may touch the front ("1xst01-u01"); any other
# letter in front means this is part of a longer word, not a code.
# A trailing "x2" ("st01-t01x2") is a quantity, so the code may end right before "x<digit>".
_CODE = re.compile(r"(?<![a-wyz0-9])([a-z]{2})\s*(\d{2})[\s_\-–—.]*([a-z])\s*(\d{2,3})([a-z]?)"
                   r"(?=$|[^a-z0-9]|x\s*\d)")
_QTY_BEFORE = re.compile(r"(\d{1,3})\s*[x*]?\s*$")
_QTY_AFTER = re.compile(r"^\s*(?:[x*]\s*(\d{1,3})|(\d{1,3})\s*[x*])(?!\d)")
_NEXT_CODE = re.compile(r"\s*[a-z]{2}\s*\d{2}")
_COMMENT = ("#", "//", ";")


@dataclass
class ParseResult:
    cards: dict[str, int] = field(default_factory=dict)  # code -> copies, in first-seen order
    unknown: list[str] = field(default_factory=list)      # well-formed codes the app doesn't know
    ignored: int = 0                                      # non-empty lines with no card code
    truncated: bool = False                               # input was cut to the size limits

    @property
    def total(self) -> int:
        return sum(self.cards.values())


def format_code(selection: list[tuple[str, int]]) -> str:
    """[(code, copies)] -> deck code text, one "Nx CODE" line per card."""
    return "\n".join(f"{qty}x {code.upper()}" for code, qty in selection if qty > 0)


def _clean(text: str) -> tuple[str, bool]:
    """Normalise pasted text: full-width/odd characters to plain ones, no control characters."""
    truncated = len(text) > MAX_CHARS
    text = unicodedata.normalize("NFKC", text[:MAX_CHARS]).replace("×", "x")
    text = "".join(ch if ch in "\n\t" or unicodedata.category(ch)[0] != "C" else " " for ch in text)
    lines = text.lower().splitlines()
    if len(lines) > MAX_LINES:
        lines, truncated = lines[:MAX_LINES], True
    return "\n".join(lines), truncated


def parse(text: str, known: set[str] | frozenset[str]) -> ParseResult:
    """Read a deck code. known = lower-case card codes the app has (e.g. "st01-u01")."""
    res = ParseResult()
    cleaned, res.truncated = _clean(text or "")
    unknown_seen: set[str] = set()
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(_COMMENT):
            continue
        found_any = False
        pos = 0
        while (m := _CODE.search(line, pos)) is not None:
            found_any = True
            base = f"{m[1]}{m[2]}-{m[3]}{m[4]}"
            code, end = base + m[5], m.end()
            if m[5] and code not in known and base in known:
                code, end = base, m.end(5) - 1  # "st01-t01x2": the x starts a quantity, not a suffix
            qty = 1
            if (before := _QTY_BEFORE.search(line[pos:m.start()])) is not None:
                qty = int(before[1])
            elif (after := _QTY_AFTER.match(line[end:])) is not None and not (
                    after[2] and _NEXT_CODE.match(line[end + after.end():])):  # "3x" before the next code
                qty = int(after[1] or after[2])
                end += after.end()
            pos = end
            if qty == 0:  # "0x CODE": explicitly none
                continue
            qty = min(qty, MAX_QTY)
            if code in known:
                res.cards[code] = min(res.cards.get(code, 0) + qty, MAX_QTY)
            elif code not in unknown_seen:
                unknown_seen.add(code)
                res.unknown.append(code.upper())
        if not found_any:
            res.ignored += 1
    return res

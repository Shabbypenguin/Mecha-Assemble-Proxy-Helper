"""Card filtering, independent of any UI.

Rules
  - Search text: every word must appear somewhere in the card (name, code, model
    number, tags, weapon/ability names and effect text).
  - Traits and ability/effect types: a card must have ALL chosen values.
  - Set and signature tag: a card has exactly one of each, so choosing several
    means "any of these" (ALL would always be empty).
  - Tactics deck rules: a tactics card with a signature tag needs the unit with that
    signature; one with a trait needs a unit with that trait; one with neither works with
    any unit. "Who can use it" groups cards by that rule (any of the chosen groups);
    "Usable by" shows the cards at least one of the chosen units can use.
  - Stat ranges (HP/VP/TL): inclusive; only applied when narrowed from the full range.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .scraper import Card, search_text

# category key -> (label, how multiple chips combine)
CATEGORIES = {
    "set": ("Set", "any"),
    "signature": ("Signature tag", "any"),
    "who": ("Who can use it", "any"),
    "trait": ("Traits", "all"),
    "kind": ("Ability type", "all"),
}
# "Who can use it" groups for tactics cards
WHO_ANY = "Any unit"
WHO_SIGNATURE = "Signature unit only"
WHO_TRAIT = "{} units"  # e.g. "Londo Bell units"
STAT_LABELS = {"hp": "HP", "vp": "VP", "tl": "TL (timeline)"}


def card_values(card: Card, category: str) -> set[str]:
    d = card.details or {}
    if category == "set":
        return {card.set.upper()}
    if category == "signature":
        return {d["signature"]} if d.get("signature") else set()
    if category == "who":
        if card.type != "tactics" or not d:  # details not fetched yet: unknown, not "any unit"
            return set()
        out = {WHO_SIGNATURE} if d.get("signature") else set()
        out |= {WHO_TRAIT.format(t) for t in d.get("traits", [])}
        return out or {WHO_ANY}
    if category == "trait":
        return set(d.get("traits", []))
    if category == "kind":
        kinds = {k.capitalize() for a in d.get("abilities", []) for k in a.get("kinds", [])}
        if d.get("effect_type"):
            kinds.add(d["effect_type"].capitalize())
        return kinds
    raise KeyError(category)


def usable_by(tactic: Card, unit: Card) -> bool:
    """Deck rule: can this unit field this tactics card?"""
    t, u = tactic.details or {}, unit.details or {}
    if not t:
        return False
    sig, traits = t.get("signature"), set(t.get("traits", []))
    if not sig and not traits:
        return True
    return bool(sig and sig == u.get("signature")) or bool(traits & set(u.get("traits", [])))


def unit_labels(units: list[Card]) -> dict[str, Card]:
    """Display name -> unit card, for the "Usable by" picker.

    Names that repeat (ignoring case, e.g. "Gundam" and "GUNDAM") get their card code too.
    """
    names = Counter(u.name.casefold() for u in units)
    return {(u.name if names[u.name.casefold()] == 1 else f"{u.name} [{u.label}]"): u
            for u in units if u.details}


def options(cards: list[Card], category: str) -> Counter:
    """Every value in a category with how many cards have it."""
    c = Counter()
    for card in cards:
        c.update(card_values(card, category))
    return c


def stat_bounds(cards: list[Card], stat: str) -> tuple[int, int] | None:
    vals = [c.details.get(stat) for c in cards if isinstance((c.details or {}).get(stat), int)]
    return (min(vals), max(vals)) if vals else None


@dataclass
class FilterState:
    chosen: dict[str, set[str]] = field(default_factory=dict)       # category -> values
    ranges: dict[str, tuple[int, int]] = field(default_factory=dict)  # stat -> (lo, hi), narrowed only
    text: str = ""
    usable_by: list[Card] = field(default_factory=list)              # tactics: any of these units

    def is_empty(self) -> bool:
        return (not self.text.strip() and not self.ranges and not any(self.chosen.values())
                and not self.usable_by)


def matches(card: Card, f: FilterState, haystack: str | None = None) -> bool:
    for cat, values in f.chosen.items():
        if not values:
            continue
        have = card_values(card, cat)
        if CATEGORIES[cat][1] == "all":
            if not values <= have:
                return False
        elif not values & have:
            return False
    if f.usable_by and not any(usable_by(card, u) for u in f.usable_by):
        return False
    d = card.details or {}
    for stat, (lo, hi) in f.ranges.items():
        v = d.get(stat)
        if not isinstance(v, int) or not lo <= v <= hi:
            return False
    words = f.text.lower().split()
    if words:
        hay = haystack if haystack is not None else search_text(card)
        if not all(w in hay for w in words):
            return False
    return True

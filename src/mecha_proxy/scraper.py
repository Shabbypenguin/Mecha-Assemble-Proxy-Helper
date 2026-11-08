"""Build a card manifest from the publisher's public card-list pages.

Only metadata and image URLs are collected; images themselves go through the
local cache, straight from the publisher to the user's machine.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

from . import config
from .config import CardType

CODE_RE = re.compile(r"^[a-z]{2}\d{2}-[a-z]\d{2,3}[a-z]?$")
PAGE_RE = re.compile(r"[?&]page_id=(\d+)")


@dataclass
class Card:
    code: str          # lowercase, e.g. "ex02-u03"
    type: str          # "unit" | "tactics"
    set: str           # "ex02"
    name: str
    list_image: str    # image shown in the list
    print_image: str   # best image for printing (front variant for units)
    details: dict = field(default_factory=dict)  # stats, tags, card text (from the card's modal)

    @property
    def label(self) -> str:
        return self.code.upper()

    def image_relpath(self, url: str) -> str:
        """Readable cache location, e.g. 'unit/EX02/EX02-U03 Darilbalde.webp'."""
        ext = PurePosixPath(urlparse(url).path).suffix or ".webp"
        suffix = " (thumbnail)" if url == self.list_image != self.print_image else ""
        return f"{self.type}/{self.set.upper()}/{self.label} {safe_filename(self.name)}{suffix}{ext}"


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(text: str, limit: int = 60) -> str:
    """Make a card name safe as a Windows/macOS/Linux file name."""
    cleaned = " ".join(_UNSAFE.sub("", text).split()).rstrip(". ")
    return cleaned[:limit].rstrip(". ") or "card"


class _CardListParser(HTMLParser):
    """Collects (code, name, img src) from <li class="p-cardlist__item"> blocks."""

    def __init__(self, img_section: str):
        super().__init__(convert_charrefs=True)
        self.img_marker = f"/{img_section}/card/img_card_"
        self.items: list[dict] = []
        self._cur: dict | None = None
        self._li_depth = 0
        self._in_name = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "li":
            if self._cur is None and "p-cardlist__item" in classes:
                self._cur = {"code": (a.get("data-category") or "").strip(), "name": "", "img": ""}
                self._li_depth = 1
            elif self._cur is not None:
                self._li_depth += 1
        elif self._cur is not None:
            if tag == "img" and not self._cur["img"] and self.img_marker in (a.get("src") or ""):
                self._cur["img"] = a["src"]
                self._cur.setdefault("alt", a.get("alt") or "")
            elif tag == "p" and "p-cardlist__name" in classes:
                self._in_name = True

    def handle_endtag(self, tag):
        if self._cur is None:
            return
        if tag == "p":
            self._in_name = False
        elif tag == "li":
            self._li_depth -= 1
            if self._li_depth == 0:
                self.items.append(self._cur)
                self._cur = None

    def handle_data(self, data):
        if self._cur is not None and self._in_name:
            self._cur["name"] += data


def parse_card_list(html: str, ctype: CardType, page_url: str) -> list[Card]:
    p = _CardListParser(ctype.img_section)
    p.feed(html)
    cards = []
    for it in p.items:
        if not it["img"]:
            continue
        img = urljoin(page_url, it["img"])
        code = it["code"].lower()
        if not CODE_RE.match(code):  # fall back to the code embedded in the filename
            m = re.search(r"img_card_([a-z0-9]+-[a-z]\d+[a-z]?)_", img)
            code = m.group(1) if m else ""
        if not re.match(ctype.code_pattern, code):
            continue
        name = " ".join((it["name"] or it.get("alt", "")).split())
        print_img = img.replace("/img_card_", "/img_card_front_") if ctype.front_variant else img
        cards.append(Card(code=code, type=ctype.key, set=code.split("-")[0], name=name,
                          list_image=img, print_image=print_img))
    return cards


def page_count(html: str) -> int:
    nums = [int(n) for n in PAGE_RE.findall(html)]
    return max(nums, default=1)


def scrape_type(cache, ctype: CardType, log=print) -> list[Card]:
    first_url = urljoin(config.SITE_BASE, ctype.list_path)
    html = cache.get_text(first_url)
    pages = page_count(html)
    cards = parse_card_list(html, ctype, first_url)
    for n in range(2, pages + 1):
        url = f"{first_url}&page_id={n}"
        cards += parse_card_list(cache.get_text(url), ctype, url)
    # de-dupe, keep first occurrence
    seen, unique = set(), []
    for c in cards:
        if c.code not in seen:
            seen.add(c.code)
            unique.append(c)
    log(f"{ctype.key}: {len(unique)} cards across {pages} page(s)")
    if not unique:
        raise RuntimeError(f"no {ctype.key} cards found - the site layout may have changed")
    return sorted(unique, key=lambda c: c.code)


def modal_url(ctype: CardType, code: str) -> str:
    folder = ctype.list_path.split("/")[0]
    extra = "&view=card" if ctype.front_variant else ""
    return urljoin(config.SITE_BASE, f"{folder}/modal.php?cardid={code}{extra}&modal=true")


# Details that matter for play; "where" (which products include the card) can grow with
# reprints without the card itself changing.
CHANGE_FIELDS = ("hp", "vp", "tl", "signature", "traits", "weapons", "abilities",
                 "effect_type", "effect", "critical", "model_no")
FIELD_LABELS = {"hp": "HP", "vp": "VP", "tl": "TL", "signature": "signature", "traits": "traits",
                "weapons": "weapons", "abilities": "abilities", "effect_type": "effect type",
                "effect": "effect text", "critical": "critical", "model_no": "model number"}
# If more than this share of known cards "change" in one update, the site layout most likely
# changed (our reader got different text, not Bandai): keep the old details, flag nothing.
MAX_CHANGE_SHARE = 0.5


def changed_fields(old: dict, new: dict) -> list[str]:
    return [f for f in CHANGE_FIELDS if (old or {}).get(f) != (new or {}).get(f)]


def build_manifest(cache, types: list[str], log=print, details: bool = True,
                   refresh_details: bool = False, on_step=None) -> dict:
    """Scrape the card lists; fetch each card's details once (or again if refresh_details).

    With refresh_details, cards whose play details differ from the saved ones are recorded in
    manifest["changes"][code] = {"date", "fields"} (kept until the card changes again).
    on_step(label, status, done, total) reports detail fetching progress.
    """
    manifest = cache.load_manifest() or {"cards": {}}
    changes = manifest.setdefault("changes", {})
    pending: dict[str, tuple[Card, dict, list[str]]] = {}  # code -> (card, old details, fields)
    rechecked = 0
    for key in types:
        ctype = config.CARD_TYPES[key]
        known = {c["code"]: c.get("details") or {} for c in manifest["cards"].get(key, [])}
        cards = scrape_type(cache, ctype, log=log)
        for c in cards:
            c.details = known.get(c.code, {})
        if details:
            todo = [c for c in cards if refresh_details or not c.details]
            for i, c in enumerate(todo, 1):
                old = c.details
                try:
                    new = parse_modal(cache.get_text(modal_url(ctype, c.code)))
                    status = "details"
                except Exception as exc:  # noqa: BLE001 - details are optional; keep going
                    new, status = None, f"details unavailable ({exc.__class__.__name__})"
                if new:
                    if old:
                        rechecked += 1
                        if fields := changed_fields(old, new):
                            pending[c.code] = (c, old, fields)
                            status = "changed: " + ", ".join(FIELD_LABELS[f] for f in fields)
                    c.details = new
                if on_step:
                    on_step(c.label, status, i, len(todo))
        manifest["cards"][key] = [asdict(c) for c in cards]
    if pending and len(pending) > MAX_CHANGE_SHARE * rechecked:
        log(f"warning: {len(pending)} of {rechecked} cards look different – the site layout probably "
            "changed, so card details were left as they were and nothing was flagged")
        for c, old, _ in pending.values():
            c.details = old
        for key in types:  # write the kept details back
            by_code = {c.code: c for c, _, _ in pending.values()}
            manifest["cards"][key] = [asdict(by_code[d["code"]]) if d["code"] in by_code else d
                                      for d in manifest["cards"][key]]
        pending = {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for code, (c, _, fields) in pending.items():
        changes[code] = {"date": now, "fields": fields}
        log(f"{c.label} was updated by Bandai: {', '.join(FIELD_LABELS[f] for f in fields)}")
    manifest["last_changed"] = sorted(pending)  # what this run found (for the after-update banner)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["source"] = config.SITE_BASE
    return manifest


def cards_from_manifest(manifest: dict, key: str) -> list[Card]:
    return [Card(**c) for c in manifest.get("cards", {}).get(key, [])]


# ---- card details (modal page) ----------------------------------------------------
class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, attrs, parent, []

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        out = []
        for ch in self.children:
            out.append(ch if isinstance(ch, str) else ch.text())
        return " ".join(" ".join(out).split())

    def find_all(self, cls: str | None = None, tag: str | None = None) -> list["_Node"]:
        found = []
        for ch in self.children:
            if isinstance(ch, _Node):
                if (cls is None or cls in ch.classes) and (tag is None or ch.tag == tag):
                    found.append(ch)
                found += ch.find_all(cls, tag)
        return found

    def find(self, cls: str | None = None, tag: str | None = None) -> "_Node | None":
        hits = self.find_all(cls, tag)
        return hits[0] if hits else None


class _TreeBuilder(HTMLParser):
    VOID = {"img", "br", "hr", "input", "meta", "link", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in self.VOID:
            self.cur = node

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if data.strip():
            self.cur.children.append(data)


def _int(text: str) -> int | None:
    m = re.search(r"-?\d+", text or "")
    return int(m.group()) if m else None


def _dash_none(text: str) -> str:
    t = " ".join((text or "").split())
    return "" if t in ("", "-") else t


def parse_modal(html: str) -> dict:
    """Extract stats, tags and card text from a card's modal page (units, tactics, tokens)."""
    tb = _TreeBuilder()
    tb.feed(html)
    root = tb.root.find("pg-cardmodal")
    if root is None:
        raise ValueError("no card details on page")
    d: dict = {}

    name_p = root.find("p-cardmodal__name")
    if name_p is not None:
        num = name_p.find("p-cardmodal__num")
        d["model_no"] = num.text() if num is not None else ""

    for key, cls in (("hp", "p-cardmodal_statusHp"), ("vp", "p-cardmodal_statusVp"),
                     ("tl", "p-cardmodal_statusTl")):
        box = root.find(cls)
        if box is not None:
            val = box.find("p-cardmodal__info-txt")
            if val is not None and _int(val.text()) is not None:
                d[key] = _int(val.text())

    sig = root.find("p-cardmodal__signatureTag")
    if sig is not None:
        tag = sig.find("p-cardmodal__tagName")
        d["signature"] = _dash_none(tag.text()) if tag is not None else ""

    fac = root.find("p-cardmodal__factionTag")
    d["traits"] = [t for t in (_dash_none(n.text()) for n in fac.find_all("p-cardmodal__tagName"))
                   if t] if fac is not None else []

    et = root.find("p-cardmodal__effectType")
    if et is not None:
        val = et.find("p-cardmodal__info-txt")
        d["effect_type"] = _dash_none(val.text()).lower() if val is not None else ""

    weapons, effect_text = [], None
    for wrap in root.find_all("p-cardmodal__weaponWrap"):
        title = wrap.find(tag="h3")
        title_txt = title.text() if title is not None else ""
        crit = wrap.find("p-cardmodal__weapon-Critical")
        crit_txt = _dash_none(crit.find(tag="dd").text()) if crit is not None and crit.find(tag="dd") else ""
        name = wrap.find("p-cardmodal__info-weaponName")
        if name is not None:
            eff = wrap.find("p-cardmodal__weapon-effect")
            weapons.append({"name": name.text(),
                            "effect": _dash_none(eff.find(tag="dd").text()) if eff is not None and eff.find(tag="dd") else "",
                            "critical": crit_txt})
        elif title_txt.lower() == "effect":  # tactics card: the card's own effect text
            body = wrap.find("p-cardmodal__info-txt")
            effect_text = {"effect": _dash_none(body.text()) if body is not None else "", "critical": crit_txt}
    if weapons:
        d["weapons"] = weapons
    if effect_text:
        d.update(effect_text)

    abilities = []
    for wrap in root.find_all("p-cardmodal__abilityWrap"):
        cats = [img.attrs.get("alt", "").strip().lower()
                for img in wrap.find_all(tag="img") if img.attrs.get("alt", "").strip()]
        name = wrap.find("p-cardmodal__info-description-name")
        eff = wrap.find("p-cardmodal__ability-effect")
        abilities.append({"name": name.text() if name is not None else "", "kinds": cats,
                          "effect": _dash_none(eff.find(tag="dd").text()) if eff is not None and eff.find(tag="dd") else ""})
    if abilities:
        d["abilities"] = abilities

    where = root.find("p-cardmodal__wheretogetit")
    if where is not None:
        d["where"] = [" ".join(a.text().split()) for a in where.find_all(tag="a")]

    rel = root.find("c-relatedCard__btn")
    if rel is not None:
        m = re.search(r"[?&]modal=([a-z0-9-]+)", rel.attrs.get("href", ""))
        if m:
            d["related"] = m.group(1)
    return d


def search_text(card: Card) -> str:
    """Everything searchable about a card, lowercased."""
    d = card.details or {}
    parts = [card.code, card.name, card.set, d.get("model_no", ""), d.get("signature", ""),
             d.get("effect_type", ""), d.get("effect", ""), d.get("critical", ""),
             " ".join(d.get("traits", []))]
    for w in d.get("weapons", []):
        parts += [w.get("name", ""), w.get("effect", ""), w.get("critical", "")]
    for a in d.get("abilities", []):
        parts += [a.get("name", ""), a.get("effect", ""), " ".join(a.get("kinds", []))]
    return " ".join(p for p in parts if p).lower()

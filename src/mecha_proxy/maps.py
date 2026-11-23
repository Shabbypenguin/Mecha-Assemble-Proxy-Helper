"""Maps: Bandai's printable play maps (Data → Enrich gameplay), in the user's paper size.

Every map is offered twice on the page, once for A4 and once for US Letter ("Print on A4 paper
and assemble." / "Print on letter-size paper and assemble."). The app lists each map once and
keeps the copy for the chosen paper size. A map PDF is a set of tiles printed edge to edge and
put together, so it is opened as published – never resized.

Only PDF entries whose title says MAP count; other downloads on the page (e.g. Rock Terrain, a
zip of 3D-printer files) are skipped by what they are, not by their names. File names carry dates
(261030_…), so maps are tracked by title + paper size, and the link is re-read from the page.
"""
from __future__ import annotations

import hashlib
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
from .scenarios import is_pdf

LIST_PATH = "data/enrich-gameplay/"
GENERAL_TITLE = "Map"          # the entry with no scenario name above it
StepFn = Callable[[str, str, int, int], None]


@dataclass
class MapFile:
    """One download on the page: a map in one paper size."""
    title: str                   # "[ST01] SLEEPING LEVIATHAN" or "Map"
    paper: str                   # "a4" | "letter"
    url: str
    updated: str | None = None   # "2026.11.20"
    size: str | None = None      # "7.28MB"

    @property
    def filename(self) -> str:
        name = Path(unquote(urlparse(self.url).path)).name
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name) or "map.pdf"


@dataclass
class Map:
    """A map as listed in the app: its title and the file for each paper size Bandai offers."""
    title: str
    files: dict[str, MapFile]

    def file(self, paper: str) -> MapFile | None:
        return self.files.get(paper)

    @property
    def display(self) -> str:
        m = re.match(r"\[([A-Z0-9]+)\]\s*(.+)", self.title)
        if m:
            return f"{m.group(2).title()} [{m.group(1)}]"
        return "General map" if self.title == GENERAL_TITLE else self.title.title()


# ---- reading the page ------------------------------------------------------------------------
class _Parser(HTMLParser):
    """<a class="c-dlBtn__listLink" href> … subTtl (map name) … MAP … listAttention … Last Updated … size."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._cur: dict | None = None
        self._depth = 0
        self._field: str | None = None
        self._stack: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "a" and "c-dlBtn__listLink" in cls:
            self._cur = {"href": a.get("href"), "name": [], "title": [], "note": [], "updated": [], "size": []}
            self._depth, self._field, self._stack = 1, None, []
            return
        if self._cur is None:
            return
        if tag == "a":
            self._depth += 1
        field = None
        if "pg-enrichgameplay__subTtl" in cls:
            field = "name"
        elif "c-dlBtn__listAttention" in cls:
            field = "note"
        elif "pg-enrichgameplay__mainTtl" in cls:
            field = "title"
        elif "c-dlBtn__listsupplementLastupTxt" in cls:
            field = "updated"
        elif "c-dlBtn__listsupplementSizeTxt" in cls:
            field = "size"
        if tag not in ("br", "img"):
            self._stack.append(self._field)
            if field:
                self._field = field

    def handle_endtag(self, tag):
        if self._cur is None:
            return
        if tag == "a":
            self._depth -= 1
            if self._depth == 0:
                self.items.append(self._cur)
                self._cur, self._field = None, None
            return
        if tag not in ("br", "img") and self._stack:
            self._field = self._stack.pop()

    def handle_data(self, data):
        if self._cur is not None and self._field:
            text = " ".join(data.split())
            if text:
                self._cur[self._field].append(text)


def _paper_of(note: str, url: str) -> str | None:
    text = f"{note} {Path(unquote(urlparse(url).path)).stem}".lower()
    if re.search(r"\bletter\b|_letter\b|letter-size", text):
        return "letter"
    if re.search(r"\ba4\b|_a4\b", text):
        return "a4"
    return None


def parse_list(html: str, base: str = config.SITE_BASE) -> list[Map]:
    p = _Parser()
    p.feed(html)
    maps: dict[str, Map] = {}
    for it in p.items:
        href = (it["href"] or "").strip()
        url = urljoin(base, href) if href else ""
        if not url.lower().split("?")[0].endswith(".pdf"):
            continue  # zips (3D-printer files) and anything else that isn't a printable map
        if "MAP" not in " ".join(it["title"]).upper().split():
            continue
        paper = _paper_of(" ".join(it["note"]), url)
        if paper is None:
            continue
        title = " ".join(it["name"]).strip() or GENERAL_TITLE
        updated = next((m.group(0) for t in it["updated"] if (m := re.search(r"\d{4}\.\d{2}\.\d{2}", t))), None)
        size = " ".join(it["size"]).strip() or None
        maps.setdefault(title, Map(title, {})).files[paper] = MapFile(title, paper, url, updated, size)
    return list(maps.values())


def fetch_list(cache) -> list[Map]:
    return parse_list(cache.get_text(urljoin(config.SITE_BASE, LIST_PATH)))


# ---- local copies ----------------------------------------------------------------------------
def folder(cache) -> Path:
    return Path(cache.root) / "maps"


def _state_path(cache) -> Path:
    return Path(cache.meta_dir) / "maps.json"


def load_state(cache) -> dict:
    """{"list": [map...], "files": {"title|paper": {"file", "url", "updated", "sha256"}}, "checked_at"}."""
    try:
        return json.loads(_state_path(cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"list": [], "files": {}}


def _save_state(cache, state: dict) -> None:
    atomic_write(_state_path(cache), json.dumps(state, indent=1).encode("utf-8"))


def _to_dict(m: Map) -> dict:
    return {"title": m.title, "files": {k: asdict(v) for k, v in m.files.items()}}


def saved_list(cache) -> list[Map]:
    out = []
    for d in load_state(cache).get("list", []):
        try:
            out.append(Map(d["title"], {k: MapFile(**v) for k, v in d.get("files", {}).items()}))
        except (KeyError, TypeError):
            continue
    return out


def _key(title: str, paper: str) -> str:
    return f"{title}|{paper}"


def local_pdf(cache, title: str, paper: str) -> Path | None:
    info = load_state(cache).get("files", {}).get(_key(title, paper))
    if not info:
        return None
    path = folder(cache) / info["file"]
    return path if path.is_file() else None


def refresh_list(cache) -> list[Map]:
    maps = fetch_list(cache)
    state = load_state(cache)
    state["list"] = [_to_dict(m) for m in maps]
    state["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_state(cache, state)
    return maps


def _download(cache, f: MapFile, state: dict) -> str:
    """Fetch one map file if it's new or changed. Returns "up to date" / "downloaded" / "updated"."""
    files = state.setdefault("files", {})
    info = files.get(_key(f.title, f.paper))
    have = info and (folder(cache) / info["file"]).is_file()
    if have and info.get("url") == f.url and info.get("updated") == f.updated:
        return "up to date"
    cache._throttle()
    resp = cache.session.get(f.url, timeout=max(config.TIMEOUT_S, 180))
    resp.raise_for_status()
    data = resp.content
    if not is_pdf(data):
        raise RuntimeError(f"the site returned something that isn't a PDF for the {f.title} map")
    folder(cache).mkdir(parents=True, exist_ok=True)
    atomic_write(folder(cache) / f.filename, data)
    if info and info.get("file") != f.filename:
        (folder(cache) / info["file"]).unlink(missing_ok=True)  # the previous version
    files[_key(f.title, f.paper)] = {"file": f.filename, "url": f.url, "updated": f.updated,
                                     "sha256": hashlib.sha256(data).hexdigest()}
    return "updated" if have else "downloaded"


def update(cache, paper: str, log=print, on_step: StepFn | None = None,
           should_stop: Callable[[], bool] | None = None) -> tuple[list[Map], list[str]]:
    """Refresh the list and download new or changed maps in one paper size. Returns (maps, changed)."""
    maps = fetch_list(cache)
    state = load_state(cache)
    todo = [m.file(paper) for m in maps if m.file(paper)]
    changed = []
    for i, f in enumerate(todo, 1):
        if should_stop and should_stop():
            break
        status = _download(cache, f, state)
        _save_state(cache, state)
        if status != "up to date":
            changed.append(f.title)
            log(f"map {f.title} ({config.paper_label(paper)}): {status}")
        if on_step:
            on_step(f"Map: {f.title}", status, i, len(todo))
    state["list"] = [_to_dict(m) for m in maps]
    state["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_state(cache, state)
    return maps, changed


def get(cache, title: str, paper: str, log=print) -> tuple[Path, str]:
    """The newest copy of one map in one paper size (for opening a map in the other size)."""
    try:
        maps = refresh_list(cache)
    except Exception as exc:  # noqa: BLE001 - offline: fall back to what we have
        have = local_pdf(cache, title, paper)
        if have:
            return have, "Couldn't reach Bandai's site, so this is the copy downloaded earlier."
        raise RuntimeError(f"couldn't reach Bandai's site to download this map ({exc})") from exc
    m = next((x for x in maps if x.title == title), None)
    f = m.file(paper) if m else None
    if f is None:
        have = local_pdf(cache, title, paper)
        if have:
            return have, "Bandai's page no longer offers this map, so this is the copy downloaded earlier."
        raise RuntimeError(f"Bandai doesn't offer this map in {config.paper_label(paper)} size")
    state = load_state(cache)
    log(f"Getting the {m.display} map ({config.paper_label(paper)}, {f.size or 'PDF'})…")
    status = _download(cache, f, state)
    _save_state(cache, state)
    path = local_pdf(cache, title, paper)
    return path, "" if status == "up to date" else f"Downloaded ({f.updated})."


def new_on_site(cache, paper: str) -> list[str]:
    """Maps offered in this paper size that aren't saved here yet (light startup check)."""
    have = set(load_state(cache).get("files", {}))
    return [m.display for m in fetch_list(cache) if m.file(paper) and _key(m.title, paper) not in have]


def total_mb(maps: list[Map], paper: str) -> float | None:
    sizes = []
    for m in maps:
        f = m.file(paper)
        if f and f.size and (num := re.match(r"([\d.]+)\s*MB", f.size, re.I)):
            sizes.append(float(num.group(1)))
    return round(sum(sizes)) if sizes else None


# ---- preview -----------------------------------------------------------------------------------
BIG_IMAGE_PX = 2_000_000   # JPEGs larger than this are shrunk for previews (only for the preview!)


def _preview_pdf(pdf_path: Path) -> bytes | str:
    """A preview-only copy of the PDF with its huge JPEGs decoded at 1/8 size.

    Bandai's maps lay one 5000 × 4400 px terrain photo under every tile; rendering it full size
    for each of 16 tiles takes about a minute, the shrunk copy a couple of seconds and looks the
    same at preview size. Falls back to the original file if anything goes wrong.
    """
    import io

    from PIL import Image
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, NumberObject
    try:
        writer = PdfWriter(clone_from=str(pdf_path))
        done, seen = set(), set()

        def images(resources):
            """Image XObjects in these resources, including inside forms (each object once)."""
            for ref in (resources or {}).get("/XObject", {}).values():
                obj = ref.get_object()
                if id(obj) in seen:
                    continue
                seen.add(id(obj))
                if obj.get("/Subtype") == "/Form":
                    yield from images(obj.get("/Resources"))
                elif obj.get("/Subtype") == "/Image":
                    yield obj

        for page in writer.pages:
            for img in images(page.get("/Resources")):
                filters = img.get("/Filter")
                filters = list(filters) if isinstance(filters, list) else [filters]
                if (filters != ["/DCTDecode"]
                        or int(img.get("/Width", 0)) * int(img.get("/Height", 0)) < BIG_IMAGE_PX):
                    continue
                done.add(id(img))
                pic = Image.open(io.BytesIO(img._data))
                pic.draft(pic.mode, (pic.width // 8, pic.height // 8))  # JPEG decodes at 1/8 scale: fast
                pic = pic.copy()
                out = io.BytesIO()
                pic.save(out, "JPEG", quality=85)
                img._data = out.getvalue()
                img[NameObject("/Width")] = NumberObject(pic.width)
                img[NameObject("/Height")] = NumberObject(pic.height)
        if not done:
            return str(pdf_path)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - previews are optional; render the original instead
        return str(pdf_path)


def thumbnail(pdf_path: Path, out: Path, width: int = 420) -> Path | None:
    """Contact sheet of the map's tiles in page order (up to 16), to recognise it at a glance."""
    try:
        import pypdfium2 as pdfium
        from PIL import Image
        doc = pdfium.PdfDocument(_preview_pdf(pdf_path))
        n = min(len(doc), 16)
        cols = min(n, 8)
        rows = -(-n // cols)
        gap = 3
        tile_w = (width - gap * (cols - 1)) // cols
        imgs = []
        for i in range(n):
            page = doc[i]
            imgs.append(page.render(scale=tile_w / page.get_size()[0]).to_pil().convert("RGB"))
        doc.close()
        tile_h = max(im.height for im in imgs)
        sheet = Image.new("RGB", (cols * tile_w + gap * (cols - 1), rows * tile_h + gap * (rows - 1)), (60, 60, 60))
        for i, im in enumerate(imgs):
            sheet.paste(im, ((i % cols) * (tile_w + gap), (i // cols) * (tile_h + gap)))
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out)
        return out
    except Exception:  # noqa: BLE001 - previews are optional
        return None


def thumb_path(cache, pdf: Path) -> Path:
    return Path(cache.meta_dir) / "map-thumbs" / (pdf.stem + ".png")


def thumb_ready(cache, pdf: Path) -> Path | None:
    """The preview if it exists and is newer than the PDF (no rendering here: safe on the UI thread)."""
    t = thumb_path(cache, pdf)
    return t if t.is_file() and t.stat().st_mtime >= pdf.stat().st_mtime else None


def make_missing_thumbs(cache, paper: str, should_stop: Callable[[], bool] | None = None) -> int:
    """Build previews for downloaded maps in this paper size that don't have a current one."""
    made = 0
    for m in saved_list(cache):
        if should_stop and should_stop():
            break
        pdf = local_pdf(cache, m.title, paper)
        if pdf is not None and thumb_ready(cache, pdf) is None and thumbnail(pdf, thumb_path(cache, pdf)):
            made += 1
    return made

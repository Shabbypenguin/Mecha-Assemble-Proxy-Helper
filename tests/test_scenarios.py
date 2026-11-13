"""Scenarios: list page, downloads, spread splitting and goal-card extraction.

Uses synthetic PDFs built with reportlab (no Bandai files in the repo): an A3-landscape
"spread" with a goal card drawn like Bandai's - a light card with a red ribbon down its
left edge covering 55.9% of its height, at whatever scale.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from mecha_proxy import config, scenarios as S
from mecha_proxy.cache import Cache

FIX = Path(__file__).parent / "fixtures"
MM = S.MM


def make_spread(path: Path, card_scale: float | None = 0.966, card_at=(225.0, 57.7), extra_ribbon=False,
                text=True) -> Path:
    """A3 landscape page; optional goal card (88x63 * scale) with its top-left at card_at (mm from top-left)."""
    from reportlab.pdfgen import canvas
    w, h = 420 * MM, 297 * MM
    c = canvas.Canvas(str(path), pagesize=(w, h))
    c.setFillColorRGB(0.93, 0.93, 0.93)          # marble-ish background
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 1)                   # blue marker just right of the middle: must not leak to page 1
    c.rect(212 * MM, 140 * MM, 6 * MM, 6 * MM, fill=1, stroke=0)
    c.setFillColorRGB(0, 0.6, 0)                 # green marker just left of the middle: must not leak to page 2
    c.rect(202 * MM, 140 * MM, 6 * MM, 6 * MM, fill=1, stroke=0)
    if text:
        c.setFillColorRGB(0, 0, 0)
        c.drawString(20 * MM, 280 * MM, "Scenario title")
        c.drawString(225 * MM, 243 * MM, "6. Place the scenario goal card within sight of both players.")
    if card_scale:
        cw, ch = 88 * MM * card_scale, 63 * MM * card_scale
        x, top = card_at[0] * MM, h - card_at[1] * MM
        c.setFillColorRGB(0.97, 0.97, 0.97)
        c.rect(x, top - ch, cw, ch, fill=1, stroke=0)
        c.setFillColorRGB(0.78, 0.07, 0.11)       # ribbon
        c.rect(x, top - ch * S.GOAL_RIBBON_SHARE, ch * 0.063, ch * S.GOAL_RIBBON_SHARE, fill=1, stroke=0)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawString(x + cw * 0.2, top - ch * 0.5, "= 5 VP")
    if extra_ribbon:                              # a second red ribbon somewhere: ambiguous -> no card
        c.setFillColorRGB(0.78, 0.07, 0.11)
        c.rect(30 * MM, 60 * MM, 3 * MM, 30 * MM, fill=1, stroke=0)
    c.showPage()
    c.save()
    return path


def render(path: Path, page: int, dpi: int = 40) -> Image.Image:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    img = doc[page].render(scale=dpi / 72).to_pil().convert("RGB")
    doc.close()
    return img


def count(img: Image.Image, test) -> int:
    b = img.convert("RGB").tobytes()
    return sum(1 for i in range(0, len(b), 3) if test((b[i], b[i + 1], b[i + 2])))


def is_blue(p):
    return p[2] > 180 and p[0] < 80 and p[1] < 80


def is_green(p):
    return p[1] > 120 and p[0] < 60 and p[2] < 60


# ---- list page ------------------------------------------------------------------------------------
def test_parse_list():
    scs = S.parse_list((FIX / "scenario_list.html").read_text())
    assert [s.title for s in scs] == ["SAMPLE ONE", "SAMPLE TWO", "SAMPLE THREE", "SAMPLE FOUR",
                                      "OPERATION SAMPLE FIVE"]
    one = scs[0]
    assert one.set_name == "STARTER SET 01" and one.updated == "2026.11.13" and one.size == "6.35MB"
    assert one.url.startswith(config.SITE_BASE) and one.url.endswith("SAMPLE ONE.pdf")
    assert one.filename == "ST01_EN_scenario01_SAMPLE ONE.pdf" and one.available
    soon = scs[3]
    assert not soon.available and soon.url is None and soon.updated is None and soon.set_name == "DELUXE SET 01"
    assert scs[4].set_name == "ORGANIZED PLAY"  # title split over two lines in the HTML


def test_filename_is_safe():
    sc = S.Scenario("X", "S", "https://example.test/pdf/a%3Cb%3E%3A..%2Fc.pdf", "2026.01.01")
    assert "/" not in sc.filename and "\\" not in sc.filename and "<" not in sc.filename and ":" not in sc.filename


# ---- downloading -------------------------------------------------------------------------------------
class _Resp:
    def __init__(self, status, content=b"", text=""):
        self.status_code, self.content, self.text, self.encoding = status, content, text, "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Sess:
    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def get(self, url, timeout=None, headers=None, **k):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            return _Resp(404)
        return _Resp(200, content=r if isinstance(r, bytes) else b"", text=r if isinstance(r, str) else "")


def _site(tmp_path, html=None, pdf_bytes=None):
    html = html or (FIX / "scenario_list.html").read_text()
    scs = S.parse_list(html)
    pdf_bytes = pdf_bytes or make_spread(tmp_path / "src.pdf").read_bytes()
    routes = {config.SITE_BASE + S.LIST_PATH: html}
    routes.update({s.url: pdf_bytes for s in scs if s.available})
    return routes


def test_update_downloads_and_skips_unchanged(tmp_path):
    routes = _site(tmp_path)
    cache = Cache(tmp_path / "home", session=_Sess(routes), delay_s=0, log=lambda *a: None)
    scs, changed = S.update(cache, log=lambda m: None)
    assert changed == ["SAMPLE ONE", "SAMPLE TWO", "SAMPLE THREE"]
    for sc in scs[:3]:
        assert S.local_pdf(cache, sc).read_bytes().startswith(b"%PDF")
    assert S.local_pdf(cache, scs[3]) is None  # coming soon
    assert [s.title for s in S.saved_list(cache)] == [s.title for s in scs]
    # nothing changed on the site: only the list page is fetched again
    cache.session = sess = _Sess(routes)
    assert S.update(cache, log=lambda m: None)[1] == []
    assert sess.calls == [config.SITE_BASE + S.LIST_PATH]
    # Bandai updates one scenario (new "Last Updated" date): just that one is downloaded again
    routes2 = _site(tmp_path, html=routes[config.SITE_BASE + S.LIST_PATH].replace(
        "2026.11.13", "2026.12.01", 1))
    cache.session = _Sess(routes2)
    assert S.update(cache, log=lambda m: None)[1] == ["SAMPLE ONE"]


def test_update_refuses_non_pdf(tmp_path):
    routes = _site(tmp_path, pdf_bytes=b"<html>error page</html>")
    cache = Cache(tmp_path / "home", session=_Sess(routes), delay_s=0, log=lambda *a: None)
    with pytest.raises(RuntimeError, match="isn't a PDF"):
        S.update(cache, log=lambda m: None)


def test_new_on_site(tmp_path):
    routes = _site(tmp_path)
    cache = Cache(tmp_path / "home", session=_Sess(routes), delay_s=0, log=lambda *a: None)
    assert S.new_on_site(cache) == ["SAMPLE ONE", "SAMPLE TWO", "SAMPLE THREE"]
    S.update(cache, log=lambda m: None)
    assert S.new_on_site(cache) == []


# ---- Letter PDF ------------------------------------------------------------------------------------------
def test_spread_splits_into_two_letter_pages(tmp_path):
    from pypdf import PdfReader
    src = make_spread(tmp_path / "s.pdf", card_scale=None)
    out, notes = S.build_letter_pdf([src], tmp_path / "out.pdf", log=lambda m: None)
    pages = PdfReader(str(out)).pages
    assert len(pages) == 2 and not notes
    for p in pages:
        assert (round(float(p.mediabox.width) / MM, 1), round(float(p.mediabox.height) / MM, 1)) == (215.9, 279.4)
    left, right = render(out, 0), render(out, 1)
    assert count(left, is_green) > 0 and count(left, is_blue) == 0     # each half shows only its own side
    assert count(right, is_blue) > 0 and count(right, is_green) == 0
    # 5 mm margin all round (about 91%): the edges stay paper-white
    w, h = left.size
    edge = [left.getpixel((x, 1)) for x in range(w)] + [left.getpixel((1, y)) for y in range(h)]
    assert all(min(px) > 245 for px in edge)


@pytest.mark.parametrize("scale,at", [(0.966, (225.0, 57.7)), (0.617, (240.3, 62.7))])
def test_goal_card_found_at_any_scale(tmp_path, scale, at):
    src = make_spread(tmp_path / "s.pdf", card_scale=scale, card_at=at, text=False)  # no text layer needed
    rect = S.find_goal_card(src)
    assert rect is not None
    x0, y0, x1, y1 = (v / MM for v in rect)
    assert abs(x0 - at[0]) < 0.4 and abs((297 - y1) - at[1]) < 0.4           # top-left corner
    assert abs((x1 - x0) - 88 * scale) < 0.8 and abs((y1 - y0) - 63 * scale) < 0.6


def test_goal_card_skipped_when_unsure(tmp_path):
    assert S.find_goal_card(make_spread(tmp_path / "a.pdf", card_scale=None)) is None       # no card
    assert S.find_goal_card(make_spread(tmp_path / "b.pdf", extra_ribbon=True)) is None      # two ribbons


def test_goal_card_page(tmp_path):
    from pypdf import PdfReader
    good = make_spread(tmp_path / "good.pdf", card_scale=0.617, card_at=(240.3, 62.7))
    none = make_spread(tmp_path / "none.pdf", card_scale=None)
    out, notes = S.build_letter_pdf([good, none], tmp_path / "out.pdf", goal_cards=True, log=lambda m: None)
    assert len(PdfReader(str(out)).pages) == 2 + 1 + 2   # goal card page only where one was found
    assert notes == ["none: goal card not found, so it was left out"]
    # two copies at 88 x 63 mm: measure the ribbons on the goal-card page
    img = render(out, 2, dpi=150)
    strips = S._red_strips(img, 150 / 25.4)
    assert len(strips) == 2
    for x0, y0, x1, y1 in strips:
        card_h = (y1 - y0) / (150 / 25.4) / S.GOAL_RIBBON_SHARE
        assert abs(card_h - 63.0) < 0.6
    assert "Print at 100%" in PdfReader(str(out)).pages[2].extract_text()


def test_thumbnail(tmp_path):
    src = make_spread(tmp_path / "s.pdf")
    th = S.thumbnail(src, tmp_path / "t" / "s.png", width=200)
    with Image.open(th) as im:
        assert im.width == 200 and im.height > im.width   # left page only: portrait


def test_cli_build(tmp_path, capsys):
    from mecha_proxy import cli
    routes = _site(tmp_path)
    home = tmp_path / "home"
    cache = Cache(home, session=_Sess(routes), delay_s=0, log=lambda *a: None)
    S.update(cache, log=lambda m: None)
    out = tmp_path / "sc.pdf"
    assert cli.main(["--home", str(home), "scenarios", "build", "--only", "one,three", "--goal-cards",
                     "--out", str(out)]) == 0
    from pypdf import PdfReader
    assert len(PdfReader(str(out)).pages) == 6
    assert cli.main(["--home", str(home), "scenarios", "list"]) == 0
    listing = capsys.readouterr().out
    assert "SAMPLE FOUR" in listing and "coming soon" in listing
    state = json.loads((home / ".cache" / "scenarios.json").read_text())
    assert len(state["files"]) == 3


def test_red_strip_ignores_small_red_text():
    img = Image.new("RGB", (400, 300), (240, 240, 240))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.rectangle((50, 50, 120, 62), fill=(200, 20, 30))    # wide red heading bar: not a ribbon
    d.rectangle((300, 40, 309, 160), fill=(200, 20, 30))  # tall narrow: the ribbon
    assert S._red_strips(img, 2.0) == [(300, 40, 310, 161)]

"""Rules menu: rule documents downloaded when picked, laid out as full pages or a booklet."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from mecha_proxy import cli, config, rulesdocs as R, sitecheck
from mecha_proxy.cache import Cache

from .test_scenarios import _Sess

FIX = Path(__file__).parent / "fixtures"
LIST_URL = config.SITE_BASE + R.LIST_PATH
MM = 72 / 25.4


def make_doc(path: Path, pages: int = 5, cover: bool = True, edge_bar: bool = False) -> Path:
    """A Word-style Letter document: optional full-bleed cover, then text pages with ~1in margins."""
    from reportlab.pdfgen import canvas
    w, h = config.LETTER_MM[0] * MM, config.LETTER_MM[1] * MM
    c = canvas.Canvas(str(path), pagesize=(w, h))
    for i in range(pages):
        if cover and i == 0:
            c.setFillColorRGB(0.7, 0.1, 0.1)
            c.rect(0, 0, w, h, fill=1, stroke=0)
            c.setFillColorRGB(1, 1, 1)
            c.drawCentredString(w / 2, h / 2, "SAMPLE COVER")
        else:
            if edge_bar:  # decorative bar running off the top of the page
                c.setFillColorRGB(0.6, 0.1, 0.1)
                c.rect(0, h - 2 * MM, w, 2 * MM, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica", 11)
            for line in range(40):  # text block from 25 mm to 190 mm across, 20 mm to 265 mm down
                c.drawString(25 * MM, h - 20 * MM - line * 6 * MM, f"Page {i + 1} line {line} " + "x" * 70)
        c.showPage()
    c.save()
    return path


def _site(doc_bytes: bytes, html: str | None = None) -> dict:
    html = html or (FIX / "rules_list.html").read_text()
    routes = {LIST_URL: html}
    for d in R.parse_list(html):
        if d.available:
            routes[d.url] = doc_bytes
    return routes


def _cache(tmp_path, routes) -> Cache:
    return Cache(tmp_path / "home", session=_Sess(routes), delay_s=0, log=lambda *a: None)


def _quiet(*a):
    pass


def test_list_parses_like_scenarios():
    docs = R.parse_list((FIX / "rules_list.html").read_text())
    assert [(d.title, d.available, d.updated, d.size) for d in docs] == [
        ("SAMPLE RULEBOOK", True, "2026.11.06", "3.28MB"),
        ("SAMPLE PLAY GUIDE", True, "2026.11.06", "2.02MB"),   # title split over a <br>
        ("SAMPLE COMING SOON", False, None, None)]
    assert docs[0].filename == "SAMPLE Rulebook_en_1106.pdf"


def test_get_latest_downloads_once_then_follows_new_versions(tmp_path):
    doc = make_doc(tmp_path / "d.pdf").read_bytes()
    routes = _site(doc)
    cache = _cache(tmp_path, routes)
    assert R.saved_list(cache) == []
    path, note = R.get_latest(cache, "SAMPLE RULEBOOK", log=_quiet)
    assert path.name == "SAMPLE Rulebook_en_1106.pdf" and path.read_bytes() == doc
    assert note.startswith("Downloaded the latest version")
    assert [d.title for d in R.saved_list(cache)][:2] == ["SAMPLE RULEBOOK", "SAMPLE PLAY GUIDE"]
    # picked again, nothing new: only the list page is read
    cache.session = sess = _Sess(routes)
    assert R.get_latest(cache, "SAMPLE RULEBOOK", log=_quiet) == (path, "")
    assert sess.calls == [LIST_URL]
    # Bandai posts a new version under a new, dated file name: it replaces the old file
    html = (FIX / "rules_list.html").read_text().replace("Rulebook_en_1106", "Rulebook_en_1120").replace(
        "2026.11.06", "2026.11.20", 1)
    cache.session = _Sess(_site(doc, html))
    new, note = R.get_latest(cache, "SAMPLE RULEBOOK", log=_quiet)
    assert new.name == "SAMPLE Rulebook_en_1120.pdf" and not path.exists()
    assert "new version" in note and "2026.11.20" in note


def test_offline_uses_the_earlier_copy(tmp_path):
    routes = _site(make_doc(tmp_path / "d.pdf").read_bytes())
    cache = _cache(tmp_path, routes)
    path, _ = R.get_latest(cache, "SAMPLE RULEBOOK", log=_quiet)
    cache.session = _Sess({})  # site unreachable (404 everywhere)
    again, note = R.get_latest(cache, "SAMPLE RULEBOOK", log=_quiet)
    assert again == path and "Couldn't reach Bandai's site" in note
    with pytest.raises(RuntimeError, match="couldn't reach"):
        R.get_latest(cache, "SAMPLE PLAY GUIDE", log=_quiet)  # never downloaded


def test_not_a_pdf_is_refused(tmp_path):
    cache = _cache(tmp_path, _site(b"<html>error page</html>"))
    with pytest.raises(RuntimeError, match="isn't a PDF"):
        R.get_latest(cache, "SAMPLE RULEBOOK", log=_quiet)
    assert R.local_copy(cache, "SAMPLE RULEBOOK") is None


def test_content_boxes(tmp_path):
    boxes = R.content_boxes(make_doc(tmp_path / "d.pdf", pages=3, edge_bar=True))
    w, h = config.LETTER_MM[0] * MM, config.LETTER_MM[1] * MM
    assert boxes[0] == pytest.approx((0.0, 0.0, w, h))  # full-bleed cover: whole page
    assert boxes[1] == boxes[2]                    # text pages share one rect
    x0, y0, x1, y1 = (v / MM for v in boxes[1])
    assert 21 < x0 < 25 and y1 < 279.4 - 10        # margins trimmed; the edge bar is left out
    assert (x1 - x0) < 185 and (y1 - y0) < 260


@pytest.mark.parametrize("paper,size_pt", [("letter", (612, 792)), ("a4", (595, 842))])
def test_full_pages_print_at_100_percent(tmp_path, paper, size_pt):
    src = make_doc(tmp_path / "d.pdf", pages=5)
    out, info = R.build_pdf(src, tmp_path / "out.pdf", paper=paper, layout="pages", log=_quiet)
    pages = PdfReader(str(out)).pages
    assert len(pages) == 5 and info["sheets"] == 5 and info["scale"] == 100
    assert {(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in pages} == {size_pt}
    assert "Page 3 line 0" in pages[2].extract_text()
    assert "100% / Actual size" in R.how_to_print(info)


def test_booklet_imposition(tmp_path):
    src = make_doc(tmp_path / "d.pdf", pages=6, cover=False)
    out, info = R.build_pdf(src, tmp_path / "b.pdf", paper="letter", layout="booklet", log=_quiet, guide=False)
    pages = PdfReader(str(out)).pages
    assert info["sheets"] == 2 and len(pages) == 4      # 6 pages padded to 8 = 2 sheets, front + back
    assert round(float(pages[0].mediabox.width)) == 792  # landscape
    assert 60 <= info["scale"] <= 80                    # trimmed margins: bigger than a plain 65%

    def order(page):
        text = page.extract_text()
        found = [(text.find(f"Page {n} line 0 "), n) for n in range(1, 7) if f"Page {n} line 0 " in text]
        return [n for _, n in sorted(found)]
    # sheet 1: front = [8 (blank), 1], back = [2, 7 (blank)]; sheet 2: front = [6, 3], back = [4, 5]
    assert [order(p) for p in pages] == [[1], [2], [6, 3], [4, 5]]
    assert "Sheet 1 of 2 - front" in pages[0].extract_text() and "Sheet 2 of 2 - back" in pages[3].extract_text()
    assert "flip on short edge" in R.how_to_print(info) and "assembly guide" not in R.how_to_print(info)


def test_booklet_guide_sheet_keeps_pairs(tmp_path):
    src = make_doc(tmp_path / "d.pdf", pages=6, cover=False)
    out, info = R.build_pdf(src, tmp_path / "b.pdf", paper="a4", layout="booklet", log=_quiet)
    pages = PdfReader(str(out)).pages
    assert len(pages) == 6                                  # guide + its blank back, then 2 sheets
    assert "How to make this booklet" in pages[0].extract_text() and "flip on short" in pages[0].extract_text()
    assert pages[1].extract_text().strip() == ""
    assert "Page 1 line 0" in pages[2].extract_text()
    assert round(float(pages[0].mediabox.width)) == 842     # landscape like the sheets
    assert "assembly guide" in R.how_to_print(info)


def test_booklet_manual_reversed(tmp_path):
    src = make_doc(tmp_path / "d.pdf", pages=8, cover=False)
    first, info = R.build_pdf(src, tmp_path / "rb.pdf", layout="booklet", log=_quiet, mode="manual",
                              reverse=True)
    fronts, backs = info["files"]
    assert first == fronts and fronts.name == "rb_fronts.pdf" and backs.name == "rb_backs.pdf"
    f, b = PdfReader(str(fronts)).pages, PdfReader(str(backs)).pages
    assert len(f) == len(b) == 3                            # guide sheet + 2 sheets in each file
    assert "rb_fronts.pdf" in f[0].extract_text() and "rb_backs.pdf" in f[0].extract_text()
    # reversed: last sheet's back first, the guide's blank back last
    assert "Sheet 2 of 2 - back" in b[0].extract_text() and "Sheet 1 of 2 - back" in b[1].extract_text()
    assert b[2].extract_text().strip() == ""
    assert "turned over along its short edge" in R.how_to_print(info)


def test_guide_wording_follows_settings():
    from mecha_proxy.rulesdocs import _guide_steps
    duplex = " ".join(t for _, t in _guide_steps("duplex", False, 14, None))
    manual = " ".join(t for _, t in _guide_steps("manual", True, 3, ("a_fronts.pdf", "a_backs.pdf")))
    assert "flip on short edge" in duplex and "Trim them" in duplex
    assert "a_fronts.pdf" in manual and "short edge" in manual and "untick" in manual and "Trim" not in manual
    assert R.guide_image("letter", "duplex", False, None, width=400).width == 400


def test_thick_booklet_hint():
    info = {"layout": "booklet", "sheets": 38, "pages": 150, "scale": 70, "paper": "letter"}
    assert "thick stack" in R.how_to_print(info)
    assert "thick" not in R.how_to_print({**info, "sheets": 14})


def test_cli_rules(tmp_path, monkeypatch, capsys):
    routes = _site(make_doc(tmp_path / "d.pdf", pages=4).read_bytes())
    monkeypatch.setattr("mecha_proxy.cache.requests.Session", lambda: _Sess(routes))
    monkeypatch.setattr(Cache, "_throttle", lambda self: None)
    home = str(tmp_path / "home")
    assert cli.main(["--home", home, "rules", "list"]) == 0
    assert "SAMPLE RULEBOOK" in capsys.readouterr().out
    out = tmp_path / "book.pdf"
    assert cli.main(["--home", home, "rules", "build", "--doc", "rulebook", "--booklet", "--paper", "a4",
                     "--out", str(out)]) == 0
    assert len(PdfReader(str(out)).pages) == 4 and "Booklet: 1 A4 sheet" in capsys.readouterr().out
    assert cli.main(["--home", home, "rules", "build", "--doc", "rulebook", "--booklet", "--manual",
                     "--no-guide", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "book_fronts.pdf" in printed and "book_backs.pdf" in printed
    assert len(PdfReader(str(tmp_path / "book_backs.pdf")).pages) == 1
    with pytest.raises(SystemExit, match="matches 2 documents"):
        cli.main(["--home", home, "rules", "build", "--doc", "sample"])


def test_site_check_flags_broken_rules_page(tmp_path):
    from .test_sitecheck import _cache as sc_cache, _site as sc_site
    routes = sc_site()
    routes[LIST_URL] = "<html><body>redesigned</body></html>"
    r = sitecheck.run(sc_cache(tmp_path, routes), log=_quiet)
    assert r.problems == ["rules: list page has no downloadable documents - the page layout may have changed"]
    assert json.loads(json.dumps(r.counts)).get("rules") is None

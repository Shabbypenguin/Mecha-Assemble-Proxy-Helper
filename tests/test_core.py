"""Offline tests: parsing, caching behaviour, layout math, PDF output."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from mecha_proxy import backs, config, pdf, scraper
from mecha_proxy.cache import Cache
from mecha_proxy.layout import fit_grid

FIX = Path(__file__).parent / "fixtures"
UNIT, TACTICS = config.CARD_TYPES["unit"], config.CARD_TYPES["tactics"]


def _png(w=80, h=57, color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "WEBP")
    return buf.getvalue()


class FakeResp:
    def __init__(self, status, content=b"", headers=None, text=""):
        self.status_code, self.content, self.headers, self.text = status, content, headers or {}, text
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves images with an ETag and honours If-None-Match like a real server."""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def get(self, url, headers=None, timeout=None):
        headers = headers or {}
        self.calls.append((url, dict(headers)))
        r = self.routes.get(url)
        if r is None:
            return FakeResp(404)
        if isinstance(r, str):
            return FakeResp(200, text=r)
        if headers.get("If-None-Match") == '"v1"':
            return FakeResp(304)
        return FakeResp(200, r, {"ETag": '"v1"'})


# ---- scraper --------------------------------------------------------------------
def test_parse_unit_list():
    cards = scraper.parse_card_list((FIX / "unitlist.html").read_text(), UNIT, config.SITE_BASE)
    assert [c.code for c in cards] == ["ex02-u03", "st01-u01", "ex01-u01"]
    c = cards[2]
    assert c.name == "Sample Unit (Mode & Variant)" and c.set == "ex01"
    assert c.print_image.endswith("/unitlist/card/img_card_front_ex01-u01_en.webp")
    assert c.list_image.endswith("/unitlist/card/img_card_ex01-u01_en.webp")


def test_parse_tactics_and_pages():
    html = (FIX / "tactics_p1.html").read_text()
    cards = scraper.parse_card_list(html, TACTICS, config.SITE_BASE)
    assert [c.code for c in cards] == ["ex02-t09", "ex02-t08"]
    assert cards[0].print_image == cards[0].list_image  # no front variant for tactics
    assert scraper.page_count(html) == 3
    assert scraper.page_count((FIX / "unitlist.html").read_text()) == 1


def test_scrape_follows_pagination(tmp_path):
    base = config.SITE_BASE + TACTICS.list_path
    routes = {base: (FIX / "tactics_p1.html").read_text(),
              base + "&page_id=2": (FIX / "tactics_p2.html").read_text(),
              base + "&page_id=3": (FIX / "tactics_p2.html").read_text()}  # duplicate is de-duped
    cache = Cache(tmp_path, session=FakeSession(routes), delay_s=0, log=lambda *a: None)
    cards = scraper.scrape_type(cache, TACTICS, log=lambda *a: None)
    assert [c.code for c in cards] == ["ex02-t08", "ex02-t09", "st01-t05"]


# ---- cache ----------------------------------------------------------------------
def test_cache_downloads_once(tmp_path):
    url = "https://example.test/img_card_front_st01-u01_en.webp"
    sess = FakeSession({url: _png()})
    cache = Cache(tmp_path, session=sess, delay_s=0, log=lambda *a: None)
    p1 = cache.fetch_image(url)
    cache.save_index()
    assert p1.exists() and len(sess.calls) == 1

    # new process, same cache dir: no network at all
    cache2 = Cache(tmp_path, session=sess, delay_s=0, log=lambda *a: None)
    assert cache2.fetch_image(url) == p1 and len(sess.calls) == 1

    # explicit refresh: conditional request, 304, nothing re-downloaded
    assert cache2.fetch_image(url, refresh=True) == p1
    assert sess.calls[-1][1].get("If-None-Match") == '"v1"'
    assert cache2.stats["not_modified"] == 1 and cache2.stats["downloaded"] == 0


def test_cache_records_missing_and_detects_tamper(tmp_path):
    url_missing = "https://example.test/img_card_front_xx01-u01_en.webp"
    url = "https://example.test/img_card_st01-u02_en.webp"
    sess = FakeSession({url: _png()})
    cache = Cache(tmp_path, session=sess, delay_s=0, log=lambda *a: None)
    assert cache.fetch_image(url_missing) is None
    n = len(sess.calls)
    assert cache.fetch_image(url_missing) is None and len(sess.calls) == n  # remembered 404

    p = cache.fetch_image(url)
    p.write_bytes(b"corrupt")
    assert cache.cached(url) is None  # hash mismatch -> treated as not cached
    assert cache.fetch_image(url).read_bytes() != b"corrupt"


def test_cache_rejects_non_image(tmp_path):
    url = "https://example.test/x.webp"
    cache = Cache(tmp_path, session=FakeSession({url: b"<html>error</html>"}), delay_s=0)
    with pytest.raises(RuntimeError):
        cache.fetch_image(url)


# ---- layout ---------------------------------------------------------------------
def test_layout_counts():
    pw, ph = config.LETTER_MM
    g = fit_grid(pw, ph, 148, 105)
    assert (g.cols, g.rows, g.rotated) == (1, 2, False)
    g = fit_grid(pw, ph, 63, 88)
    assert (g.cols, g.rows, g.per_page) == (3, 3, 9)
    g = fit_grid(pw, ph, 63, 88, bleed=3)
    assert g.per_page == 6 and g.rotated
    g = fit_grid(pw, ph, 148, 105, bleed=3)
    assert g.per_page == 2


def test_back_slots_mirror():
    pw, ph = config.LETTER_MM
    g = fit_grid(pw, ph, 63, 88)
    f, bl, bs = g.front_slots(), g.back_slots("long"), g.back_slots("short")
    # long-edge flip: x mirrors around the page centre, y unchanged
    for a, b in zip(f, bl):
        assert a.y == pytest.approx(b.y)
        assert a.x + a.w / 2 == pytest.approx(pw - (b.x + b.w / 2))
    # short-edge flip: y mirrors, x unchanged
    for a, b in zip(f, bs):
        assert a.x == pytest.approx(b.x)
        assert a.y + a.h / 2 == pytest.approx(ph - (b.y + b.h / 2))
    assert all(s.rot == 180 for s in bs)


# ---- output ---------------------------------------------------------------------
def _pages(path: Path) -> int:
    pypdf = pytest.importorskip("pypdf")
    return len(pypdf.PdfReader(str(path)).pages)


def _fronts(tmp_path, n, size=(800, 567)):
    paths = []
    for i in range(n):
        p = tmp_path / f"card{i}.webp"
        Image.new("RGB", size, (30 * i % 255, 80, 160)).save(p, "WEBP")
        paths.append(p)
    return paths


@pytest.mark.parametrize("mode,files,pages", [("duplex", 1, 4), ("split", 2, 4), ("single", 1, 2)])
def test_build_modes(tmp_path, mode, files, pages):
    fronts = _fronts(tmp_path, 3)  # 3 unit cards -> 2 sheets
    back = backs.back_path(tmp_path, 148, 105, 0, dpi=60)
    job = pdf.PrintJob(fronts=fronts, labels=["A", "B", "C"], card_w=148, card_h=105, back=back)
    out = pdf.build(job, mode, tmp_path / "out" / "cards.pdf", tmp_path, log=lambda *a: None)
    assert len(out) == files
    assert sum(_pages(p) for p in out) == pages


def test_build_with_bleed_and_rotation(tmp_path):
    fronts = _fronts(tmp_path, 7, size=(746, 1044))
    back = backs.back_path(tmp_path, 63, 88, 3, dpi=60)
    job = pdf.PrintJob(fronts=fronts, labels=list("ABCDEFG"), card_w=63, card_h=88, back=back, bleed=3)
    out = pdf.build(job, "duplex", tmp_path / "t.pdf", tmp_path, log=lambda *a: None)
    assert _pages(out[0]) == 4  # 6 per sheet with bleed -> 2 sheets x (front+back)


def test_back_render_size():
    img = backs.render_back(148, 105, dpi=100, bleed_mm=2)
    assert img.size == (round(152 / 25.4 * 100), round(109 / 25.4 * 100))


def test_calibration(tmp_path):
    out = pdf.build_calibration(63, 88, tmp_path / "new" / "dir" / "cal.pdf", flip="short")
    assert _pages(out) == 2


# ---- sorted cache layout --------------------------------------------------------
def test_image_relpath_sorted_and_safe():
    cards = scraper.parse_card_list((FIX / "unitlist.html").read_text(), UNIT, config.SITE_BASE)
    c = cards[2]
    assert c.image_relpath(c.print_image) == "unit/EX01/EX01-U01 Sample Unit (Mode & Variant).webp"
    assert c.image_relpath(c.list_image).endswith("EX01-U01 Sample Unit (Mode & Variant) (thumbnail).webp")
    assert scraper.safe_filename('Bad: name/with*chars?.') == "Bad namewithchars"
    assert scraper.safe_filename("...") == "card"


def test_flat_cache_is_migrated_without_redownload(tmp_path):
    url = "https://example.test/img_card_front_st01-u01_en.webp"
    sess = FakeSession({url: _png()})
    cache = Cache(tmp_path, session=sess, delay_s=0, log=lambda *a: None)
    flat = cache.fetch_image(url)  # old v0.1 behaviour: no rel -> flat file
    assert flat.parent == cache.images_dir
    calls = len(sess.calls)

    rel = "unit/ST01/ST01-U01 Sample.webp"
    moved = cache.fetch_image(url, rel=rel)
    assert moved == cache.images_dir / rel and moved.exists() and not flat.exists()
    assert len(sess.calls) == calls  # moved, not downloaded
    assert cache.index[url]["file"] == rel

    # card renamed on the site -> file follows, still no download
    moved2 = cache.cached(url, "unit/ST01/ST01-U01 Renamed.webp")
    assert moved2.exists() and not moved.exists() and len(sess.calls) == calls


def test_real_session_sends_tool_user_agent(tmp_path):
    import requests
    cache = Cache(tmp_path, session=requests.Session(), delay_s=0)
    assert cache.session.headers["User-Agent"] == config.USER_AGENT


def test_progress_plain_and_tty():
    from mecha_proxy.cli import Progress

    class Buf(io.StringIO):
        def __init__(self, tty):
            super().__init__()
            self._tty = tty

        def isatty(self):
            return self._tty

    plain = Buf(False)
    p = Progress(2, plain)
    p.step("ST01-U01", "downloaded")
    p.step("ST01-U02", "cached")
    assert plain.getvalue() == "[1/2] ST01-U01  downloaded\n[2/2] ST01-U02  cached\n"

    tty = Buf(True)
    p = Progress(2, tty)
    p.step("ST01-U01", "downloaded")
    p.close()  # interrupted after 1 of 2
    out = tty.getvalue()
    assert out.startswith("\r[1/2]") and out.endswith("\n") and out.count("\n") == 1


# ---- portable home --------------------------------------------------------------
def test_app_home_resolution(tmp_path, monkeypatch):
    from mecha_proxy import paths
    monkeypatch.delenv("MECHA_PROXY_HOME", raising=False)
    # running from this source checkout -> project folder
    assert (paths.app_home() / "pyproject.toml").is_file()
    monkeypatch.setenv("MECHA_PROXY_HOME", str(tmp_path / "env"))
    assert paths.app_home() == (tmp_path / "env").resolve()
    assert paths.app_home(str(tmp_path / "cli")) == (tmp_path / "cli").resolve()


def test_cache_layout(tmp_path):
    cache = Cache(tmp_path, delay_s=0)
    assert cache.images_dir == tmp_path / "cards"
    assert cache.index_path == tmp_path / ".cache" / "index.json"


def test_migrate_legacy_moves_without_redownload(tmp_path, monkeypatch):
    from mecha_proxy import paths
    legacy = tmp_path / "AppData" / "MechaAssembleProxyHelper" / "Cache"
    monkeypatch.setattr(paths, "legacy_cache_dir", lambda: legacy)
    url = "https://example.test/img_card_front_st01-u01_en.webp"
    sess = FakeSession({url: _png()})

    # simulate a v0.1.1 cache: images/ + index.json at the legacy root
    old = Cache(legacy, session=sess, delay_s=0, log=lambda *a: None)
    old.images_dir = legacy / "images"
    old.index_path = legacy / "index.json"
    old.fetch_image(url, rel="unit/ST01/ST01-U01 Sample.webp")
    old.save_index()
    calls = len(sess.calls)

    home = tmp_path / "portable"
    assert paths.migrate_legacy(home, log=lambda *a: None)
    assert not legacy.exists() and not legacy.parent.exists()
    new = Cache(home, session=sess, delay_s=0, log=lambda *a: None)
    p = new.fetch_image(url, rel="unit/ST01/ST01-U01 Sample.webp")
    assert p == home / "cards" / "unit" / "ST01" / "ST01-U01 Sample.webp"
    assert len(sess.calls) == calls  # nothing downloaded again
    assert not paths.migrate_legacy(home, log=lambda *a: None)  # idempotent


# ---- output naming --------------------------------------------------------------
def test_output_naming(tmp_path):
    from datetime import date

    from mecha_proxy import service
    d = date(2026, 11, 7)
    one = service.default_output(tmp_path, ["unit"], today=d)
    assert one.name == "Mecha-Assemble-Proxy-Helper_2026-11-07.pdf"
    assert service.output_paths(one, ["unit"]) == {"unit": one}

    both = service.output_paths(one, ["unit", "tactics"])
    assert both["unit"].name == "Mecha-Assemble-Proxy-Helper_2026-11-07_units.pdf"
    assert both["tactics"].name == "Mecha-Assemble-Proxy-Helper_2026-11-07_tactics.pdf"

    # an existing file (including a split-mode sibling) bumps the number instead of overwriting
    (tmp_path / "Mecha-Assemble-Proxy-Helper_2026-11-07_tactics_fronts.pdf").write_bytes(b"x")
    nxt = service.default_output(tmp_path, ["unit", "tactics"], today=d)
    assert nxt.name == "Mecha-Assemble-Proxy-Helper_2026-11-07_2.pdf"
    cal = service.default_output(tmp_path, ["unit"], suffix="calibration_units", today=d)
    assert cal.name == "Mecha-Assemble-Proxy-Helper_2026-11-07_calibration_units.pdf"


# ---- card details ---------------------------------------------------------------
def test_parse_modal_unit():
    d = scraper.parse_modal((FIX / "modal_unit.html").read_text())
    assert (d["hp"], d["vp"], d["tl"]) == (12, 7, 2)
    assert d["model_no"] == "SMP-02" and d["signature"] == "Sample Signature"
    assert d["traits"] == ["Sample Pilot", "Sample Faction"]
    assert [w["name"] for w in d["weapons"]] == ["Sample Claw", "Sample Rifle"]
    assert d["weapons"][0]["effect"] == "" and d["weapons"][0]["critical"] == "Roll 2 additional dice."
    assert [(a["name"], a["kinds"]) for a in d["abilities"]] == [
        ("Sample Lock", ["response"]), ("Sample Direct", ["command"]), ("Hover", ["ongoing"])]
    assert d["related"] == "ex02-u02a" and d["where"] == ["SAMPLE PACK 02 [EX02]"]


def test_parse_modal_tactics_and_other():
    t = scraper.parse_modal((FIX / "modal_tactics.html").read_text())
    assert t["effect_type"] == "response" and t["signature"] == "" and t["traits"] == ["Sample Faction"]
    assert t["effect"].startswith("[Before Attack Roll]") and t["critical"] == ""
    assert "hp" not in t and "weapons" not in t
    o = scraper.parse_modal((FIX / "modal_other.html").read_text())
    assert (o["hp"], o["vp"]) == (1, 2) and "tl" not in o
    assert o["traits"] == ["Sample Faction", "Remote Weapon System"] and o["related"] == "ex02-u02"
    with pytest.raises(ValueError):
        scraper.parse_modal("<html><body>nothing</body></html>")


def test_search_text_covers_everything():
    c = scraper.Card("ex02-u02", "unit", "ex02", "Sample Unit Beta", "u", "u",
                     scraper.parse_modal((FIX / "modal_unit.html").read_text()))
    txt = scraper.search_text(c)
    for needle in ("sample faction", "sample signature", "smp-02", "slow", "sample rifle", "ongoing"):
        assert needle in txt


def test_other_card_codes_and_modal_urls():
    other = config.CARD_TYPES["other"]
    html = (FIX / "unitlist.html").read_text().replace("unitlist/card", "othercard/card").replace(
        "ex02-u03", "ex02-u03a").replace("EX02-U03", "EX02-U03A")
    codes = [c.code for c in scraper.parse_card_list(html, other, config.SITE_BASE)]
    assert codes == ["ex02-u03a"]  # plain unit codes are not "other" cards
    assert scraper.modal_url(UNIT, "st01-u01").endswith("unitlist/modal.php?cardid=st01-u01&view=card&modal=true")
    assert scraper.modal_url(other, "ex02-u02a").endswith("othercard/modal.php?cardid=ex02-u02a&modal=true")


def test_manifest_fetches_details_once(tmp_path):
    base = config.SITE_BASE
    routes = {base + UNIT.list_path: (FIX / "unitlist.html").read_text()}
    for code in ("ex02-u03", "st01-u01", "ex01-u01"):
        routes[scraper.modal_url(UNIT, code)] = (FIX / "modal_unit.html").read_text()
    sess = FakeSession(routes)
    cache = Cache(tmp_path, session=sess, delay_s=0, log=lambda *a: None)
    m = scraper.build_manifest(cache, ["unit"], log=lambda *a: None)
    assert all(c["details"]["hp"] == 12 for c in m["cards"]["unit"])
    cache.save_manifest(m)
    n = len(sess.calls)
    scraper.build_manifest(cache, ["unit"], log=lambda *a: None)
    assert len(sess.calls) == n + 1  # only the list page again, no modal re-fetch


# ---- filters --------------------------------------------------------------------
def _fcard(code, signature, traits, hp, kinds, name="X", effect_type=None):
    d = {"signature": signature, "traits": traits, "hp": hp, "vp": 5, "tl": 2,
         "abilities": [{"name": "A", "kinds": kinds, "effect": "apply [Slow]" if "ongoing" in kinds else ""}]}
    if effect_type:
        d["effect_type"] = effect_type
    return scraper.Card(code, "unit", code[:4], name, "u", "u", d)


def test_filters_rules():
    from mecha_proxy import filters as F
    a = _fcard("st01-u01", "Hero", ["Federation", "Mass Produced"], 10, ["command"], name="Alpha")
    b = _fcard("ex01-u01", "Duelist", ["Federation"], 15, ["ongoing"], name="Bravo")
    c = _fcard("ex02-u01", "Hunter", ["Zeon"], 13, ["command", "ongoing"], name="Charlie")
    cards = [a, b, c]
    run = lambda **kw: [x.code for x in cards if F.matches(x, F.FilterState(**kw))]

    assert F.options(cards, "trait") == {"Federation": 2, "Mass Produced": 1, "Zeon": 1}
    assert F.options(cards, "kind") == {"Command": 2, "Ongoing": 2}
    assert F.stat_bounds(cards, "hp") == (10, 15)
    assert run() == ["st01-u01", "ex01-u01", "ex02-u01"]
    # traits: ALL
    assert run(chosen={"trait": {"Federation", "Mass Produced"}}) == ["st01-u01"]
    assert run(chosen={"trait": {"Federation", "Zeon"}}) == []
    # ability types: ALL
    assert run(chosen={"kind": {"Command", "Ongoing"}}) == ["ex02-u01"]
    # sets / signatures: ANY (one per card)
    assert run(chosen={"set": {"ST01", "EX02"}}) == ["st01-u01", "ex02-u01"]
    assert run(chosen={"signature": {"Hero", "Hunter"}}) == ["st01-u01", "ex02-u01"]
    # ranges, inclusive
    assert run(ranges={"hp": (13, 15)}) == ["ex01-u01", "ex02-u01"]
    # search everything, all words
    assert run(text="federation") == ["st01-u01", "ex01-u01"]
    assert run(text="slow bravo") == ["ex01-u01"]
    assert run(text="ex02") == ["ex02-u01"]
    # combined
    assert run(chosen={"trait": {"Federation"}}, ranges={"hp": (12, 20)}) == ["ex01-u01"]


def test_filters_tactics_effect_type_and_no_details():
    from mecha_proxy import filters as F
    t = _fcard("st01-t01", "", ["Zeon"], None, [], effect_type="response")
    bare = scraper.Card("st01-u09", "unit", "st01", "Bare", "u", "u")
    assert F.card_values(t, "kind") == {"Response"}
    assert F.card_values(t, "signature") == set()  # a unit card without a signature
    # tactics deck rules: who can use it / usable by
    T = lambda code, d: scraper.Card(code, "tactics", "st01", code, "u", "u", d)  # noqa: E731
    U = lambda code, name, sig, traits: scraper.Card(code, "unit", "st01", name, "u", "u",  # noqa: E731
                                                     {"signature": sig, "traits": traits})
    generic = T("t-gen", {"signature": "", "traits": [], "effect": "x"})
    tied = T("t-sig", {"signature": "Wing of Destruction", "traits": []})
    londo = T("t-lb", {"signature": "", "traits": ["Londo Bell"]})
    unfetched = scraper.Card("t-none", "tactics", "st01", "No details", "u", "u")
    tactics = [generic, tied, londo, unfetched]
    assert F.card_values(generic, "who") == {F.WHO_ANY}
    assert F.card_values(tied, "who") == {F.WHO_SIGNATURE}
    assert F.card_values(londo, "who") == {"Londo Bell units"}
    assert F.card_values(unfetched, "who") == set()  # unknown, not "any unit"

    wing = U("st01-u02", "Wing Gundam Zero (EW)", "Wing of Destruction", ["Heero Yuy"])
    nu = U("st01-u05", "Nu Gundam", "Legendary Hero", ["Londo Bell", "Amuro Ray"])
    grunt = U("st01-u09", "Jegan", "Final Stand", ["Earth Federation"])
    run = lambda **kw: [c.code for c in tactics if F.matches(c, F.FilterState(**kw))]  # noqa: E731
    assert run(usable_by=[wing]) == ["t-gen", "t-sig"]
    assert run(usable_by=[nu]) == ["t-gen", "t-lb"]
    assert run(usable_by=[grunt]) == ["t-gen"]
    assert run(usable_by=[wing, nu]) == ["t-gen", "t-sig", "t-lb"]  # any of the chosen units
    assert run(chosen={"who": {F.WHO_ANY}}) == ["t-gen"]
    assert run(chosen={"who": {F.WHO_SIGNATURE, "Londo Bell units"}}) == ["t-sig", "t-lb"]
    assert run(usable_by=[wing], chosen={"who": {F.WHO_SIGNATURE}}) == ["t-sig"]  # both narrow
    assert not F.FilterState(usable_by=[wing]).is_empty()
    twin = U("ex01-u01", "JEGAN", "", [])  # same name, different capitals
    assert set(F.unit_labels([wing, grunt, twin])) == {"Wing Gundam Zero (EW)", "Jegan [ST01-U09]",
                                                       "JEGAN [EX01-U01]"}
    assert not F.matches(bare, F.FilterState(ranges={"hp": (1, 99)}))  # unknown stat can't satisfy a range
    assert F.matches(bare, F.FilterState(text="bare"))


def test_frozen_home_falls_back_when_not_writable(tmp_path, monkeypatch):
    import sys as _sys

    from mecha_proxy import paths
    monkeypatch.delenv("MECHA_PROXY_HOME", raising=False)
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "executable", str(exe_dir / "app.exe"))
    monkeypatch.setattr(_sys, "platform", "win32")  # the portable layout (macOS always uses Documents)
    assert paths.app_home() == exe_dir.resolve()
    monkeypatch.setattr(paths, "_writable", lambda folder: False)
    monkeypatch.setattr(paths, "user_documents_dir", lambda: str(tmp_path / "Docs"))
    assert paths.app_home() == tmp_path / "Docs" / "Mecha-Assemble-Proxy-Helper"


def test_card_spacing_layout():
    pw, ph = config.LETTER_MM
    g = fit_grid(pw, ph, 63, 88, gap=0)
    assert g.per_page == 9
    g = fit_grid(pw, ph, 63, 88, gap=3)       # 3x63 + 2x3 = 195 <= 205.9 wide; 3x88 + 6 = 270 > 269.4 tall
    assert (g.cols, g.rows) == (3, 2) or g.per_page == 8
    s = g.front_slots()
    same_row = [b for b in s if abs(b.y - s[0].y) < 1e-6]
    assert same_row[1].x - (same_row[0].x + same_row[0].w) == pytest.approx(3.0)  # exactly the gap
    xs, _ = g.cut_lines()
    assert len(xs) == 2 * g.cols  # every card has its own left and right cut
    assert g.block_w == pytest.approx(g.cols * g.cell_w + (g.cols - 1) * 3)
    # spacing combines with bleed: gap is measured between bleed areas
    g = fit_grid(pw, ph, 148, 105, bleed=2, gap=4)
    a, b = g.front_slots()[:2]
    assert (a.y - 2) - (b.y + b.h + 2) == pytest.approx(4.0)


def test_build_with_spacing(tmp_path):
    fronts = _fronts(tmp_path, 4, size=(746, 1044))
    back = backs.back_path(tmp_path, 63, 88, 0, dpi=60)
    job = pdf.PrintJob(fronts=fronts, labels=list("ABCD"), card_w=63, card_h=88, back=back, gap=5)
    out = pdf.build(job, "duplex", tmp_path / "s.pdf", tmp_path, log=lambda *a: None)
    assert _pages(out[0]) == 2


def test_edge_extend_bleed():
    im = Image.new("RGB", (10, 6), (0, 0, 255))
    im.putpixel((0, 0), (255, 0, 0))      # red top-left corner
    for x in range(10):
        im.putpixel((x, 5), (0, 255, 0))  # green bottom row
    out = pdf.edge_extend(im, 3, 2)
    assert out.size == (16, 10)
    assert out.getpixel((0, 0)) == (255, 0, 0)       # corner repeats the corner pixel
    assert out.getpixel((8, 9)) == (0, 255, 0)       # bottom bleed repeats the bottom row
    assert out.getpixel((8, 4)) == (0, 0, 255)       # original content in place
    assert pdf.edge_extend(im, 0, 0) is im

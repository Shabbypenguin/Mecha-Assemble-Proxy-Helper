"""Weekly Bandai site check (offline: a fake site built from the test fixtures)."""
from __future__ import annotations

import json
from pathlib import Path

from mecha_proxy import cli, config, scenarios, scraper, sitecheck
from mecha_proxy.cache import Cache

FIX = Path(__file__).parent / "fixtures"
BASE = config.SITE_BASE
UNIT, TACTICS, OTHER = (config.CARD_TYPES[k] for k in ("unit", "tactics", "other"))
PDF = b"%PDF-1.7\n% fake scenario\n"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "


class _Resp:
    def __init__(self, status, body=b"", text="", ctype=""):
        self.status_code, self.content, self.text, self.encoding = status, body, text, "utf-8"
        self.headers = {"Content-Type": ctype} if ctype else {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        yield self.content[:chunk_size]

    def close(self):
        self.closed = True


class _Sess:
    """routes: url -> str (HTML page) | (bytes, content type)."""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def get(self, url, timeout=None, stream=False, **k):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            return _Resp(404)
        if isinstance(r, str):
            return _Resp(200, text=r, ctype="text/html")
        return _Resp(200, body=r[0], ctype=r[1])


def _other_list() -> str:
    return (FIX / "unitlist.html").read_text().replace("unitlist/card", "othercard/card").replace(
        "ex02-u03", "ex02-u03a").replace("EX02-U03", "EX02-U03A")


def _site() -> dict:
    routes: dict = {
        BASE + UNIT.list_path: (FIX / "unitlist.html").read_text(),
        BASE + TACTICS.list_path: (FIX / "tactics_p1.html").read_text(),
        BASE + TACTICS.list_path + "&page_id=2": (FIX / "tactics_p2.html").read_text(),
        BASE + TACTICS.list_path + "&page_id=3": (FIX / "tactics_p2.html").read_text(),
        BASE + OTHER.list_path: _other_list(),
    }
    for ctype, html_key in ((UNIT, "unit"), (TACTICS, "tactics"), (OTHER, "other")):
        page = routes[BASE + ctype.list_path]
        cards = scraper.parse_card_list(page, ctype, BASE)
        if ctype is TACTICS:
            cards += scraper.parse_card_list(routes[BASE + TACTICS.list_path + "&page_id=2"], ctype, BASE)
        for c in cards:
            routes[scraper.modal_url(ctype, c.code)] = (FIX / f"modal_{html_key}.html").read_text()
            routes[c.print_image] = (WEBP, "image/webp")
    html = (FIX / "scenario_list.html").read_text()
    routes[BASE + scenarios.LIST_PATH] = html
    for sc in scenarios.parse_list(html):
        if sc.available:
            routes[sc.url] = (PDF, "application/pdf")
    return routes


def _cache(tmp_path, routes) -> Cache:
    return Cache(tmp_path / "home", session=_Sess(routes), delay_s=0, log=lambda *a: None)


def _quiet(*a):
    pass


def test_healthy_site_passes(tmp_path):
    cache = _cache(tmp_path, _site())
    r = sitecheck.run(cache, log=_quiet)
    assert r.ok, r.problems
    assert r.counts == {"unit": 3, "tactics": 3, "other": 1, "scenarios": 3}
    assert "site-check:ok" in sitecheck.report(r)
    # light on Bandai: list pages, a few details/images per type, the scenario list and its PDFs
    assert len(cache.session.calls) <= 30


def test_samples_spread_first_middle_last():
    assert sitecheck._samples(list(range(10))) == [0, 4, 9]  # round(4.5) -> 4 (banker's rounding)
    assert sitecheck._samples([1, 2]) == [1, 2]


def test_broken_pieces_are_each_reported(tmp_path):
    routes = _site()
    routes[BASE + UNIT.list_path] = "<html><body>redesigned</body></html>"  # list layout changed
    tactic = scraper.parse_card_list(routes[BASE + TACTICS.list_path], TACTICS, BASE)[0]
    routes[scraper.modal_url(TACTICS, tactic.code)] = "<html><body>no modal</body></html>"
    routes[tactic.print_image] = (b"<html>", "text/html")  # image URL now serves a page
    sc = next(s for s in scenarios.parse_list(routes[BASE + scenarios.LIST_PATH]) if s.available)
    del routes[sc.url]  # dead PDF link
    r = sitecheck.run(_cache(tmp_path, routes), log=_quiet)
    text = "\n".join(r.problems)
    assert "unit: card list didn't load or parse" in text
    assert f"tactics {tactic.code}: card details didn't load or parse" in text
    assert f"tactics {tactic.code}: image URL didn't return an image" in text
    assert f"scenario {sc.title}: PDF returned HTTP 404" in text
    assert not r.ok and "unit" not in r.counts


def test_missing_fields_are_reported(tmp_path):
    routes = _site()
    page = (FIX / "modal_unit.html").read_text().replace("p-cardmodal_statusHp", "p-cardmodal_statusXx")
    card = scraper.parse_card_list(routes[BASE + UNIT.list_path], UNIT, BASE)[0]
    routes[scraper.modal_url(UNIT, card.code)] = page
    r = sitecheck.run(_cache(tmp_path, routes), log=_quiet)
    assert r.problems == [f"unit {card.code}: card details are missing hp"]


def test_count_drop_against_baseline(tmp_path):
    r = sitecheck.run(_cache(tmp_path, _site()), baseline={"unit": 10, "tactics": 3, "gone": 5}, log=_quiet)
    assert r.problems == ["unit: count dropped from 10 to 3 since the last good check "
                          "(part of the list may no longer be read)"]
    # small changes and growth are fine
    assert sitecheck.run(_cache(tmp_path, _site()), baseline={"unit": 3, "tactics": 2}, log=_quiet).ok


def test_fingerprint_stable_and_report_lists_counts(tmp_path):
    a = sitecheck.Result(problems=["b", "a"], counts={"unit": 3}, baseline={"unit": 10})
    b = sitecheck.Result(problems=["a", "b"])
    assert a.fingerprint == b.fingerprint
    md = sitecheck.report(a)
    assert "- b" in md and "| unit | 10 | 3 |" in md and f"site-check:{a.fingerprint}" in md


def test_cli_baseline_roundtrip(tmp_path, monkeypatch, capsys):
    routes = _site()
    monkeypatch.setattr("mecha_proxy.cache.requests.Session", lambda: _Sess(routes))
    monkeypatch.setattr(Cache, "_throttle", lambda self: None)
    home, counts, md = tmp_path / "home", tmp_path / "c" / "counts.json", tmp_path / "report.md"
    args = ["--home", str(home), "site-check", "--baseline", str(counts), "--save-counts", str(counts),
            "--report", str(md)]
    assert cli.main(args) == 0  # first run: no baseline yet
    assert json.loads(counts.read_text())["counts"]["unit"] == 3
    assert "site check passed" in capsys.readouterr().out
    # next week page 2+ of tactics stops being read: below the baseline and reported; baseline kept
    counts.write_text(json.dumps({"counts": {"tactics": 30}}))
    assert cli.main(args) == 1
    assert "count dropped from 30 to 3" in md.read_text()
    assert json.loads(counts.read_text())["counts"] == {"tactics": 30}

"""A Bandai page redesign must never make saved lists (and so tabs, menus, cards) disappear."""
from __future__ import annotations

from pathlib import Path

import pytest

from mecha_proxy import config, rulesdocs, scenarios, scraper
from mecha_proxy.cache import Cache

from .test_scenarios import _Sess

FIX = Path(__file__).parent / "fixtures"
EMPTY = "<html><body>a brand new layout</body></html>"


def _cache(tmp_path, routes):
    return Cache(tmp_path / "home", session=_Sess(routes), delay_s=0, log=lambda *a: None)


@pytest.mark.parametrize("module, fixture", [(scenarios, "scenario_list.html"), (rulesdocs, "rules_list.html")])
def test_empty_page_keeps_saved_list(tmp_path, module, fixture):
    url = config.SITE_BASE + module.LIST_PATH
    html = (FIX / fixture).read_text()
    routes = {url: html}
    from .test_scenarios import make_spread
    pdf = make_spread(tmp_path / "s.pdf").read_bytes()
    for d in module.parse_list(html):
        if d.available:
            routes[d.url] = pdf
    cache = _cache(tmp_path, routes)
    if module is scenarios:
        module.update(cache, log=lambda *a: None)
    else:
        module.refresh_list(cache)
    before = [d.title for d in module.saved_list(cache)]
    assert before
    cache.session = _Sess({url: EMPTY})
    with pytest.raises(scenarios.LayoutChanged):
        module.update(cache, log=lambda *a: None) if module is scenarios else module.refresh_list(cache)
    assert [d.title for d in module.saved_list(cache)] == before


def test_card_list_shrinking_keeps_saved_cards(tmp_path, monkeypatch):
    """Page 2 of a list stops being read: the cards from it stay instead of vanishing."""
    tac = config.CARD_TYPES["tactics"]
    full = [scraper.Card(code=f"st01-t{i:02d}", type="tactics", set="st01", name=f"T{i}",
                         list_image=f"https://x/img_card_st01-t{i:02d}_en.webp",
                         print_image=f"https://x/img_card_st01-t{i:02d}_en.webp") for i in range(1, 11)]
    pages = {"now": full}
    monkeypatch.setattr(scraper, "scrape_type", lambda cache, ctype, log=print: list(pages["now"]))
    cache = _cache(tmp_path, {})
    logs = []
    m = scraper.build_manifest(cache, ["tactics"], log=logs.append, details=False)
    cache.save_manifest(m)
    pages["now"] = full[:3] + [scraper.Card(code="st01-t99", type="tactics", set="st01", name="New",
                                            list_image="https://x/n.webp", print_image="https://x/n.webp")]
    m = scraper.build_manifest(cache, ["tactics"], log=logs.append, details=False)
    codes = [c["code"] for c in m["cards"]["tactics"]]
    assert len(codes) == 11 and "st01-t99" in codes and "st01-t10" in codes
    assert m["kept_previous"] == ["tactics"] and any("were kept" in line for line in logs)
    # a small change (one card fewer) is just accepted
    cache.save_manifest({"cards": {"tactics": [c.__dict__ for c in full]}})
    pages["now"] = full[:9]
    m = scraper.build_manifest(cache, ["tactics"], log=lambda *a: None, details=False)
    assert len(m["cards"]["tactics"]) == 9 and "kept_previous" not in m
    assert tac.key == "tactics"

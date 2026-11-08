"""0.4: deck codes and Bandai card-change detection."""
from __future__ import annotations

from mecha_proxy import config, deckcode, scraper, service
from mecha_proxy.cache import Cache

from .test_core import FIX, FakeSession
from .test_core import _png as webp

UNIT = config.CARD_TYPES["unit"]
KNOWN = {"st01-u01", "st01-u02", "st01-t01", "st01-t02", "ex01-r01a"}


# ---- deck codes -----------------------------------------------------------------
def parse(text):
    r = deckcode.parse(text, KNOWN)
    return r.cards


def test_deck_code_formats_quantity_and_code_only():
    assert deckcode.format_code([("st01-u02", 1), ("st01-t02", 2), ("st01-t01", 0)]) == "1x ST01-U02\n2x ST01-T02"


def test_deck_code_reads_many_spellings():
    for text in ("1xst01-u01", "1 x st01-u01", "1x ST01-U01", "st01u01", "ST01_U01", "st01 u01", "1× st01-u01",
                 "ｓｔ０１－ｕ０１", "- st01-u01 Wing Gundam", "ST01-U01 x1"):
        assert parse(text) == {"st01-u01": 1}, text
    for text in ("2x st01u01", "2 st01-u01", "st01-u01 x2", "st01-u01x2", "ST01-U01 2x", "2 * st01-u01"):
        assert parse(text) == {"st01-u01": 2}, text
    assert parse("ex01-r01a") == {"ex01-r01a": 1} and parse("ex01-r01ax3") == {"ex01-r01a": 3}
    assert parse("st01-t01 3x st01-u01") == {"st01-t01": 1, "st01-u01": 3}  # "3x" belongs to the next card
    assert parse("1x st01-u01\n1x ST01-U01") == {"st01-u01": 2}  # repeats add up
    assert parse("0x st01-u01") == {}
    assert parse("999 st01-u01") == {"st01-u01": deckcode.MAX_QTY}


def test_deck_code_ignores_noise_and_reports_unknown():
    r = deckcode.parse("# my deck\n// notes\nhello there\n1x zz99-u99\nabst01-u01\n1x st01-t01\n\n", KNOWN)
    assert r.cards == {"st01-t01": 1}
    assert r.unknown == ["ZZ99-U99"]
    assert r.ignored == 2  # "hello there" and "abst01-u01" (code glued to a word)
    assert r.total == 1


def test_deck_code_sanitises_input():
    r = deckcode.parse("1x st01\x00-u01\x1b[31m​", KNOWN)  # control / zero-width characters
    assert r.cards == {"st01-u01": 1}
    big = deckcode.parse("st01-u01\n" * (deckcode.MAX_LINES + 50), KNOWN)
    assert big.truncated and big.cards["st01-u01"] == deckcode.MAX_QTY
    assert deckcode.parse("x" * (deckcode.MAX_CHARS + 10), KNOWN).truncated
    assert deckcode.parse("", KNOWN).cards == {} and deckcode.parse(None, KNOWN).cards == {}


# ---- Bandai changes -------------------------------------------------------------------
def _site(hp_by_code=None, where="SAMPLE PACK 02"):
    hp_by_code = hp_by_code or {}
    modal = (FIX / "modal_unit.html").read_text()
    routes = {config.SITE_BASE + UNIT.list_path: (FIX / "unitlist.html").read_text()}
    for code in ("ex02-u03", "st01-u01", "ex01-u01"):
        html = modal.replace('u-text--bold">12<', f'u-text--bold">{hp_by_code.get(code, 12)}<')
        routes[scraper.modal_url(UNIT, code)] = html.replace("SAMPLE PACK 02", where)
    for c in scraper.parse_card_list(routes[config.SITE_BASE + UNIT.list_path], UNIT, config.SITE_BASE):
        routes[c.print_image] = webp()
    return routes


def _cache(tmp_path, routes):
    return Cache(tmp_path, session=FakeSession(routes), delay_s=0, log=lambda *a: None)


def test_detail_changes_are_flagged(tmp_path):
    cache = _cache(tmp_path, _site())
    cache.save_manifest(scraper.build_manifest(cache, ["unit"], log=lambda *a: None))
    cache.session = FakeSession(_site(where="REPRINT PACK 09"))  # only "where to get it" changed
    m = scraper.build_manifest(cache, ["unit"], log=lambda *a: None, refresh_details=True)
    assert m["changes"] == {} and m["last_changed"] == []
    cache.save_manifest(m)

    cache.session = FakeSession(_site({"st01-u01": 13}))  # errata: HP 12 -> 13
    logs = []
    m = scraper.build_manifest(cache, ["unit"], log=logs.append, refresh_details=True)
    assert m["last_changed"] == ["st01-u01"]
    assert m["changes"]["st01-u01"]["fields"] == ["hp"]
    assert next(c for c in m["cards"]["unit"] if c["code"] == "st01-u01")["details"]["hp"] == 13
    assert any("ST01-U01 was updated by Bandai: HP" in s for s in logs)


def test_site_layout_change_flags_nothing(tmp_path):
    cache = _cache(tmp_path, _site())
    cache.save_manifest(scraper.build_manifest(cache, ["unit"], log=lambda *a: None))
    cache.session = FakeSession(_site({"ex02-u03": 1, "st01-u01": 1, "ex01-u01": 1}))  # every card "changed"
    logs = []
    m = scraper.build_manifest(cache, ["unit"], log=logs.append, refresh_details=True)
    assert m["changes"] == {} and m["last_changed"] == []
    assert all(c["details"]["hp"] == 12 for c in m["cards"]["unit"])  # old details kept
    assert any("layout" in s for s in logs)


def test_full_update_rechecks_only_changed_images(tmp_path, monkeypatch):
    real = service.refresh_manifest
    monkeypatch.setattr(service, "refresh_manifest",
                        lambda cache, types, **kw: real(cache, ["unit"], **kw))  # test site has units only
    cache = _cache(tmp_path, _site())
    service.full_update(cache, log=lambda m: None)
    cache.session = sess = FakeSession(_site({"st01-u01": 13}))
    manifest, missing, changed = service.full_update(cache, log=lambda m: None)
    assert changed == ["st01-u01"] and not missing
    image_calls = [(u, h) for u, h in sess.calls if u.endswith(".webp")]
    assert len(image_calls) == 1 and "st01-u01" in image_calls[0][0]
    assert image_calls[0][1].get("If-None-Match")  # a cheap "has it changed?" request


def test_print_history_and_changed_since_printed(tmp_path):
    cache = Cache(tmp_path, delay_s=0, log=lambda *a: None)
    assert service.load_printed(cache) == {}
    service.record_printed(cache, ["st01-u01", "st01-t01"])
    printed = service.load_printed(cache)
    assert set(printed) == {"st01-u01", "st01-t01"}
    later = "9999-01-01T00:00:00+00:00"
    manifest = {"changes": {"st01-u01": {"date": later, "fields": ["hp"]},
                            "st01-t01": {"date": "2000-01-01T00:00:00+00:00", "fields": ["effect"]},
                            "st01-u02": {"date": later, "fields": ["hp"]}}}
    # u01 changed after printing; t01 changed before it was printed; u02 was never printed
    assert service.changed_since_printed(manifest, printed) == ["st01-u01"]


# ---- 0.5: app update check ---------------------------------------------------------------
class _ApiResp:
    def __init__(self, status, data=None, bad_json=False):
        self.status_code, self._data, self._bad = status, data, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._data


class _ApiSession:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, headers))
        if self.exc:
            raise self.exc
        return self.resp


def test_parse_version():
    assert service.parse_version("v0.10.2") == (0, 10, 2) > service.parse_version("0.9.9")
    assert service.parse_version("1.0") == (1, 0)
    for bad in ("", "v1.0-beta", "latest", "1..2"):
        assert service.parse_version(bad) is None


def test_latest_release():
    rel = {"tag_name": "v0.6.0", "html_url": config.RELEASES_URL + "/tag/v0.6.0", "draft": False,
           "prerelease": False}
    sess = _ApiSession(_ApiResp(200, rel))
    assert service.latest_release(sess, current="0.5.0") == ("0.6.0", config.RELEASES_URL + "/tag/v0.6.0")
    assert sess.calls[0][0] == config.LATEST_RELEASE_API
    assert "@" not in str(sess.calls[0][1])  # no personal details sent
    assert service.latest_release(sess, current="0.6.0") is None   # same version
    assert service.latest_release(sess, current="0.10.0") is None  # 0.10 > 0.6, not string order
    assert service.latest_release(_ApiSession(_ApiResp(200, {**rel, "prerelease": True})), "0.5.0") is None
    assert service.latest_release(_ApiSession(_ApiResp(200, {**rel, "tag_name": "nightly"})), "0.5.0") is None
    # a link anywhere but our own repo is replaced with the releases page
    odd = _ApiSession(_ApiResp(200, {**rel, "html_url": "https://evil.example/download"}))
    assert service.latest_release(odd, "0.5.0") == ("0.6.0", config.RELEASES_URL)
    # failures are silent
    assert service.latest_release(_ApiSession(_ApiResp(404)), "0.5.0") is None
    assert service.latest_release(_ApiSession(_ApiResp(403)), "0.5.0") is None  # rate limited
    assert service.latest_release(_ApiSession(_ApiResp(200, bad_json=True)), "0.5.0") is None
    assert service.latest_release(_ApiSession(exc=OSError("offline")), "0.5.0") is None


# ---- 0.6: macOS app data location -------------------------------------------------------------
def test_mac_app_keeps_data_in_documents(tmp_path, monkeypatch):
    import sys

    from mecha_proxy import paths
    monkeypatch.delenv("MECHA_PROXY_HOME", raising=False)
    monkeypatch.setattr(paths, "user_documents_dir", lambda: str(tmp_path / "Documents"))
    app = tmp_path / "Mecha-Assemble-Proxy-Helper.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)  # writable, but inside the bundle: still not used
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "Mecha-Assemble-Proxy-Helper"))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.app_home() == tmp_path / "Documents" / "Mecha-Assemble-Proxy-Helper"
    monkeypatch.setattr(sys, "platform", "win32")  # Windows keeps the portable layout
    assert paths.app_home() == app.resolve()

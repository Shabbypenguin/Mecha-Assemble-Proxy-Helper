"""GUI smoke tests, run headless. Skipped when PySide6 isn't installed."""
from __future__ import annotations

import hashlib
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import asdict  # noqa: E402
from pathlib import Path  # noqa: E402

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mecha_proxy import gui, print_dialog, service  # noqa: E402
from mecha_proxy.cache import Cache  # noqa: E402
from mecha_proxy.scraper import Card  # noqa: E402

DETAILS = {
    "st01-u01": {"hp": 15, "vp": 8, "tl": 2, "signature": "Hero", "traits": ["Pilot A", "Federation"],
                 "abilities": [{"name": "Charge", "kinds": ["command"], "effect": "Move 2."}]},
    "st01-u02": {"hp": 10, "vp": 4, "tl": 1, "signature": "Grunt", "traits": ["Federation", "Mass Produced"],
                 "abilities": [{"name": "Hold", "kinds": ["ongoing"], "effect": "apply [Slow]"}]},
    "ex01-u01": {"hp": 13, "vp": 7, "tl": 3, "signature": "Ace", "traits": ["Zeon"], "abilities": []},
    "st01-t01": {"effect_type": "command", "traits": [], "signature": "Hero",
                 "effect": "Gain [Strength +1]."},
    "st01-t02": {"effect_type": "response", "traits": ["Federation"], "effect": "Cancel."},
}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A library with 3 unit cards (2 downloaded) and 2 tactics cards, with details."""
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *a, **k: gui.QMessageBox.No)
    monkeypatch.setattr(gui.service, "latest_release", lambda *a, **k: None)  # no GitHub calls in tests
    cache = Cache(tmp_path, delay_s=0, log=lambda *a: None)
    cards = {"unit": [], "tactics": [], "other": []}
    for t, codes, size in [("unit", ["st01-u01", "st01-u02", "ex01-u01"], (800, 567)),
                           ("tactics", ["st01-t01", "st01-t02"], (746, 1044))]:
        for code in codes:
            url = f"https://example.test/img_card_{code}_en.webp"
            c = Card(code, t, code[:4], f"Name {code}", url, url, DETAILS[code])
            cards[t].append(asdict(c))
            if code != "ex01-u01":
                p = cache.local_path(url, c.image_relpath(url))
                p.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", size, (120, 40, 40)).save(p, "WEBP")
                cache.index[url] = {"file": c.image_relpath(url),
                                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    cache.save_index()
    cache.save_manifest({"cards": cards})
    return tmp_path


def test_window_loads_and_selects(app, home):
    w = gui.MainWindow(str(home))
    unit = w.card_tabs["unit"]
    assert len(unit.grid.tiles) == 3
    assert unit.grid.tiles[0].image_path is not None
    assert "not downloaded" in unit.grid.tiles[2].img.text()
    assert not w.btn_print.isEnabled()

    unit.grid.tiles[0].set_selected(True)
    unit.grid.tiles[1].qty.setValue(2)  # editing qty selects the card
    assert w._selection("unit")[1][1] == 2
    assert "Unit cards: 3 (2 different) → 2 sheets" in w.summary.text()
    assert w.btn_print.isEnabled()
    unit.btn_clear.click()
    assert w._selection("unit") == []
    w.close()


def test_filters_panel(app, home):
    w = gui.MainWindow(str(home))
    tab = w.card_tabs["unit"]
    fp = tab.filters
    shown = lambda: [t.card.code for t in tab.grid.visible_tiles()]
    assert fp.boxes["trait"]._options == {"Pilot A": 1, "Federation": 2, "Mass Produced": 1, "Zeon": 1}

    fp.boxes["trait"].add("Federation")
    assert shown() == ["st01-u01", "st01-u02"]
    fp.boxes["trait"].add("Mass Produced")  # must have ALL
    assert shown() == ["st01-u02"] and fp.count.text() == "Showing 1 of 3"
    fp.boxes["trait"].remove("Mass Produced")
    fp.ranges["hp"].lo.setValue(12)
    assert shown() == ["st01-u01"]
    fp.reset()
    assert len(shown()) == 3

    tab.search.setText("slow")  # searches ability text too
    tab.apply_filter()
    assert shown() == ["st01-u02"]
    tab.search.setText("")
    tab.apply_filter()

    tab.grid.tiles[2].set_selected(True)
    fp.selected_only.setChecked(True)
    assert fp.selected_only.text() == "Show currently selected only"
    assert shown() == ["ex01-u01"]
    tab.search.setText("name")
    fp.reset_btn.click()  # clears filters AND the search box
    assert tab.search.text() == "" and len(shown()) == 3

    assert "signature" not in fp.boxes  # units: no signature box
    ttab = w.card_tabs["tactics"]
    tac = ttab.filters
    assert tac.boxes["kind"]._options == {"Command": 1, "Response": 1}
    assert not tac.ranges  # tactics cards have no HP/VP/TL
    assert set(tac.boxes) == {"usable", "who", "set", "kind"}  # no raw Traits/Signature boxes on tactics
    who = tac.boxes["who"]
    assert who._options == {"Signature unit only": 1, "Federation units": 1}
    usable = tac.boxes["usable"]
    by_code = {u.code: lbl for lbl, u in tac._units.items()}
    # st01-u01: signature "Hero" + trait Federation -> both cards; st01-u02: Federation -> t02; ex01-u01: none
    assert {code: usable._options[lbl] for code, lbl in by_code.items()} == \
        {"st01-u01": 2, "st01-u02": 1, "ex01-u01": 0}
    vis = lambda: [t.card.code for t in ttab.grid.visible_tiles()]  # noqa: E731
    usable.add(by_code["st01-u02"])
    ttab.apply_filter()
    assert vis() == ["st01-t02"]
    usable.add(by_code["st01-u01"])
    ttab.apply_filter()
    assert vis() == ["st01-t01", "st01-t02"]
    who.add("Signature unit only")  # both boxes narrow together
    ttab.apply_filter()
    assert vis() == ["st01-t01"]
    tac.reset_btn.click()
    assert len(vis()) == 2 and not usable.values() and not who.values()
    w.close()


def test_chipbox_typeahead(app):
    from collections import Counter

    from mecha_proxy.widgets import ChipBox
    box = ChipBox("Traits")
    box.set_options(Counter({"Asticassia": 3, "Earth Federation": 6, "Zeon": 2}))
    assert "Asticassia  (3)" in box.model.stringList()
    box.edit.setText("asti")
    box._enter()  # single partial match -> added
    assert box.values() == {"Asticassia"} and box.edit.text() == ""
    assert all("Asticassia" not in s for s in box.model.stringList())  # chosen value leaves the list
    box.edit.setText("Zeon  (2)")  # what the completer leaves in the box after a pick
    box._picked("Zeon  (2)")
    app.processEvents()
    assert box.values() == {"Asticassia", "Zeon"} and box.edit.text() == ""
    box.remove("Zeon")
    assert box.values() == {"Asticassia"}


def test_print_dialog_preview_and_prefs(app, home):
    w = gui.MainWindow(str(home))
    w.card_tabs["unit"].grid.tiles[0].set_selected(True)
    w.card_tabs["tactics"].grid.tiles[0].set_selected(True)
    sel = {t: s for t in w.card_tabs if (s := w._selection(t))}
    dlg = print_dialog.PrintDialog(w, sel, w.images, w.prefs)
    assert dlg.save.text() == "Save 2 PDFs…" and dlg.which.isVisible() is False or dlg.which.count() == 2
    dlg.mode.setCurrentIndex(dlg.mode.findData("split"))
    dlg.bleed.setValue(3)
    assert dlg.reverse.isEnabled()
    dlg.which.setCurrentIndex(1)  # tactics preview: bleed 3 -> 6 per sheet, rotated
    assert "6 per sheet" in dlg.info.text() and "rotated" in dlg.info.text()
    for topic in print_dialog.CAPTIONS:  # every diagram paints without error
        dlg._topic(topic)
        dlg.diagram.grab()
    dlg.pv_front.grab()
    dlg.pv_back.grab()
    p = dlg.result_prefs()
    assert (p["mode"], p["bleed"]) == ("split", 3.0)
    s = dlg.settings("tactics")
    assert s.mode == "split" and s.bleed == 3.0
    w.close()


def test_build_covers_all_tabs(app, home, monkeypatch):
    w = gui.MainWindow(str(home))
    w.card_tabs["unit"].grid.tiles[0].set_selected(True)
    w.tabs.setCurrentIndex(1)  # looking at tactics, nothing selected there
    assert w.btn_print.isEnabled()
    assert "Unit cards: 1" in w.summary.text() and "Tactics" not in w.summary.text()

    captured = {}
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName",
                        lambda parent, title, default, flt: (captured.setdefault("default", default), flt))
    monkeypatch.setattr(w, "_report_written", lambda written, *a, **k: captured.__setitem__("w", written))
    w.run_build()
    w.task.wait(20000)
    app.processEvents()
    assert [p.name for p in captured["w"]] == [Path(captured["default"]).name]  # units only, no suffix

    captured.clear()
    w.card_tabs["tactics"].grid.tiles[0].set_selected(True)
    w.run_build()
    w.task.wait(20000)
    app.processEvents()
    stem = Path(captured["default"]).stem
    assert sorted(p.name for p in captured["w"]) == [f"{stem}_tactics.pdf", f"{stem}_units.pdf"]
    w.close()


def test_details_tooltip():
    c = Card("st01-u02", "unit", "st01", "Name", "u", "u", DETAILS["st01-u02"])
    tip = gui.details_html(c)
    assert "HP</b> 10" in tip and "Mass Produced" in tip and "[Ongoing]" in tip
    assert "No details yet" in gui.details_html(Card("x", "unit", "st01", "N", "u", "u"))


def test_hover_preview_size_capped(app, home):
    from PySide6.QtCore import QPoint

    from mecha_proxy.widgets import HoverPreview
    w = gui.MainWindow(str(home))
    assert "signature" not in w.card_tabs["unit"].filters.boxes
    tile = w.card_tabs["unit"].grid.tiles[0]
    hp = HoverPreview.get()
    hp.show_for(tile.image_path, QPoint(100, 100), round(tile.img_w * gui.HOVER_SCALE))
    assert hp.pixmap().width() <= 560  # 2.8x the 200 px thumbnail, never past the 800 px source
    hp.show_for(tile.image_path, QPoint(100, 100), 5000)
    assert hp.pixmap().width() <= 800  # never enlarged past the source image
    hp.hide()
    w.close()


def test_menus_and_sidebar(app, home):
    w = gui.MainWindow(str(home))
    titles = [a.text() for a in w.menuBar().actions()]
    assert titles == ["&File", "&Cards", "&Enhance", "&Help"]
    cards_menu = w.menuBar().actions()[1].menu()
    names = [a.text() for a in cards_menu.actions() if a.text()]
    assert any("Update cards" in n for n in names) and any("Calibration" in n for n in names)
    assert any("cards &folder" in n for n in names)
    assert not w.act_print.isEnabled()
    w.card_tabs["unit"].grid.tiles[0].set_selected(True)
    assert w.act_print.isEnabled() and w.btn_print.isEnabled()
    assert not hasattr(w, "btn_cal") and not hasattr(w, "size_note")
    w.close()


def test_update_banner(app, home, monkeypatch):
    w = gui.MainWindow(str(home))
    # ex01-u01 is in the list but its image isn't downloaded -> banner (no network needed)
    assert not w.banner.isHidden() and "1 card not downloaded yet" in w.banner.text()

    monkeypatch.setattr(service, "new_on_site", lambda cache, manifest: ["EX03-U01", "EX03-U02"])
    w.check_site(manual=True)
    w._checker.wait(10000)
    app.processEvents()
    app.processEvents()
    assert "2 new cards on the site (EX03-U01, EX03-U02)" in w.banner.text()
    assert w.prefs["last_check"] > 0

    w.act_auto.setChecked(False)
    assert w.prefs["auto_check"] is False
    w.prefs.update({"mode": "split"})  # print dialog saves must not drop the startup-check pref
    assert w.prefs["auto_check"] is False and "last_check" in w.prefs
    w.close()


def test_new_on_site_compares_codes(tmp_path):
    from mecha_proxy import config
    base = config.SITE_BASE
    fix = Path(__file__).parent / "fixtures"
    routes = {base + config.CARD_TYPES["unit"].list_path: (fix / "unitlist.html").read_text()}
    from tests.test_core import FakeSession
    cache = Cache(tmp_path, session=FakeSession(routes), delay_s=0, log=lambda *a: None)
    manifest = {"cards": {"unit": [{"code": "st01-u01"}, {"code": "ex02-u03"}]}}
    assert service.new_on_site(cache, manifest) == ["EX01-U01"]  # other types' pages 404 -> skipped


def _marker_mm(app, tmp_path, bleed):
    """Distance between two marker lines (25% / 75% of the card) in the sheet preview, in mm."""
    import numpy as np
    from PIL import ImageDraw
    from PySide6.QtGui import QPixmap

    from mecha_proxy import config
    from mecha_proxy.layout import fit_grid
    src = tmp_path / "marker.png"
    if not src.exists():
        im = Image.new("RGB", (800, 567), (230, 230, 230))
        d = ImageDraw.Draw(im)
        for x in (200, 600):
            d.rectangle([x - 3, 0, x + 3, 566], fill=(0, 0, 0))
        im.save(src)
    pv = print_dialog.SheetPreview("t")
    pv.resize(600, 800)
    g = fit_grid(*config.LETTER_MM, 148, 105, bleed=bleed)
    pv.set_data(g, [QPixmap(str(src))], crop=False)
    out = tmp_path / f"pv{bleed}.png"
    pv.grab().toImage().save(str(out))
    a = np.asarray(Image.open(out).convert("RGB")).astype(int)
    white = a.min(axis=2) > 250
    cols = np.where(white.any(axis=0))[0]
    x0, x1 = cols[0], cols[-1]
    s = (x1 - x0 + 1) / g.page_w
    top = np.where(white[:, x0 + 3])[0][0]
    slot = g.front_slots()[0]
    row = a[int(top + (g.page_h - (slot.y + slot.h / 2)) * s), x0:x1].sum(axis=1) // 3
    dark = np.where(row < 40)[0]
    groups = np.split(dark, np.where(np.diff(dark) > 2)[0] + 1)
    c = [gg.mean() for gg in groups if len(gg)]
    return (c[-1] - c[0]) / s


def test_preview_bleed_keeps_card_size(app, tmp_path):
    """Regression: bleed made the preview stretch the card instead of adding edge colour around it."""
    sizes = [_marker_mm(app, tmp_path, b) for b in (0, 1, 3)]
    assert all(abs(x - 74) < 1.0 for x in sizes), sizes


def test_print_dialog_controls(app, home):
    w = gui.MainWindow(str(home))
    w.card_tabs["unit"].grid.tiles[0].set_selected(True)
    sel = {t: s for t in w.card_tabs if (s := w._selection(t))}
    d = print_dialog.PrintDialog(w, sel, w.images, w.prefs)
    d.mode.setCurrentIndex(d.mode.findData("duplex"))
    assert not d.flip.isEnabled() and d.flip.currentData() == "long"
    d._topic("flip")
    assert d._current_topic == "mode"  # disabled flip never takes over the diagram
    d.mode.setCurrentIndex(d.mode.findData("split"))
    assert d.flip.isEnabled() and d.reverse.isEnabled()
    assert "Default" in d.back_label.text() and d.back_default.isHidden()
    # pinned topic survives a hover-and-leave over another control
    d._pin("bleed")
    d._quiet_until = 0
    from PySide6.QtCore import QEvent
    d.eventFilter(d.crop, QEvent(QEvent.Enter))
    assert d._current_topic == "crop"
    d.eventFilter(d.crop, QEvent(QEvent.Leave))
    assert d._current_topic == "bleed"
    # right after a change, hovering elsewhere is ignored
    d._pin("mode")
    d.eventFilter(d.crop, QEvent(QEvent.Enter))
    assert d._current_topic == "mode"
    w.close()


def test_no_stray_windows_at_startup(app, home):
    """Card tiles must never flash up as separate windows while the grid is built."""
    from PySide6.QtCore import QEvent, QObject

    shown = []

    class Spy(QObject):
        def eventFilter(self, o, e):  # noqa: N802
            if e.type() == QEvent.Show and isinstance(o, gui.QWidget) and o.isWindow():
                shown.append(type(o).__name__)
            return False

    spy = Spy()
    app.installEventFilter(spy)
    try:
        w = gui.MainWindow(str(home))
        w.show()
        app.processEvents()
        w.card_tabs["unit"].grid.set_predicate(lambda t: True)  # re-filtering mustn't pop windows either
        app.processEvents()
    finally:
        app.removeEventFilter(spy)
    assert shown == ["MainWindow"]
    w.close()


def test_deck_list_import_export(app, home, monkeypatch):
    from mecha_proxy import gui_deck

    shown = []
    monkeypatch.setattr(gui.QMessageBox, "exec", lambda box: shown.append(box) or 0)
    w = gui.MainWindow(str(home))
    assert not w.btn_export.isEnabled() and w.btn_import.isEnabled()  # nothing selected yet
    tiles = {t.card.code: t for tab in w.card_tabs.values() for t in tab.grid.tiles}
    tiles["st01-u02"].qty.setValue(2)
    tiles["st01-t01"].set_selected(True)
    assert w.btn_export.isEnabled()
    w.btn_export.click()  # same button on every tab: exports all tabs
    assert QApplication.clipboard().text() == "2x ST01-U02\n1x ST01-T01"
    assert shown and "Copied 3 cards to the clipboard" in shown[0].text()
    assert "2x ST01-U02" in shown[0].informativeText()

    # import: the box opens empty every time, even with a deck on the clipboard
    dialogs = []
    sloppy = "my deck:\n1xst01-u01\nst01t02 x3\n1 x ZZ99-U99"

    def fake_exec(dlg):
        dialogs.append(dlg)
        assert dlg.text.toPlainText() == ""
        dlg.text.setPlainText(sloppy)  # what the user pastes
        return gui_deck.PasteDeckDialog.Accepted
    monkeypatch.setattr(gui_deck.PasteDeckDialog, "exec", fake_exec)
    w.btn_import.click()
    dlg = dialogs[0]
    assert dlg.windowTitle() == "Import deck list"
    assert "4 cards" in dlg.summary.text() and "ZZ99-U99" in dlg.summary.text()
    sel = {c.code: q for t in w.card_tabs for c, q in w._selection(t)}
    assert sel == {"st01-u01": 1, "st01-t02": 3}  # old picks (st01-u02, st01-t01) replaced
    w.paste_deck_code()  # second opening: empty again (checked in fake_exec)
    assert len(dialogs) == 2
    w.close()


def test_bandai_updates_banner_and_filter(app, home):
    from mecha_proxy import service
    from mecha_proxy.cache import Cache

    cache = Cache(home, delay_s=0, log=lambda *a: None)
    service.record_printed(cache, ["st01-u01", "st01-t01"])
    m = cache.load_manifest()
    m["changes"] = {"st01-u01": {"date": "9999-01-01T00:00:00+00:00", "fields": ["hp", "abilities"]},
                    "st01-u02": {"date": "9999-01-01T00:00:00+00:00", "fields": ["traits"]}}
    cache.save_manifest(m)

    w = gui.MainWindow(str(home))
    unit = w.card_tabs["unit"]
    tiles = {t.card.code: t for t in unit.grid.tiles}
    assert tiles["st01-u01"].updated.isVisibleTo(tiles["st01-u01"])
    assert "HP, abilities" in tiles["st01-u01"].updated.toolTip()
    assert not tiles["ex01-u01"].updated.isVisibleTo(tiles["ex01-u01"])

    # only the printed card that changed is in the banner
    assert w.banner.isVisibleTo(w) and "1 card you've printed (ST01-U01)" in w.banner.text()
    w._banner_clicked()
    assert w.current_type == "unit"
    assert {c.code for c, _ in w._selection("unit")} == {"st01-u01"}  # ready to reprint
    assert "printed" not in w.banner.text()  # looked at -> banner moves on
    w.close()
    w2 = gui.MainWindow(str(home))
    assert "printed" not in w2.banner.text()  # stays dismissed after a restart
    w2.close()


def test_app_update_banner(app, home, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    w = gui.MainWindow(str(home))
    w._app_update = ("9.9.9", "https://github.com/Shabbypenguin/Mecha-Assemble-Proxy-Helper/releases/tag/v9.9.9")
    w.refresh_banner()
    assert w.banner.isVisibleTo(w) and "Version 9.9.9" in w.banner.text()
    w._banner_clicked()
    assert opened == [w._app_update[1]]
    assert w.prefs["skip_app_version"] == "9.9.9"
    assert "9.9.9" not in w.banner.text()  # not nagged again for this version
    w._app_update = ("10.0.0", "https://github.com/Shabbypenguin/Mecha-Assemble-Proxy-Helper/releases/tag/v10.0.0")
    w.refresh_banner()
    assert "Version 10.0.0" in w.banner.text()  # a newer one is announced again
    w.close()


def test_scenarios_tab(app, home, monkeypatch, tmp_path):
    from pypdf import PdfReader

    from mecha_proxy import scenarios as S
    from mecha_proxy.cache import Cache

    from .test_scenarios import _Sess, _site
    routes = _site(tmp_path)
    cache = Cache(home, session=_Sess(routes), delay_s=0, log=lambda *a: None)
    S.update(cache, log=lambda m: None)

    shown = []
    monkeypatch.setattr(gui.QMessageBox, "exec", lambda box: shown.append(box.text()) or 0)
    w = gui.MainWindow(str(home))
    assert w.tabs.tabText(w.scen_index) == "Scenarios (3)"
    rows = w.scen_tab.rows
    assert [r.sc.title for r in rows][:3] == ["SAMPLE ONE", "SAMPLE TWO", "SAMPLE THREE"]
    assert not rows[3].check.isEnabled()  # coming soon
    assert all(r.check.isEnabled() for r in rows[:3])

    w.tabs.setCurrentIndex(w.scen_index)  # sidebar swaps to the scenario panel
    assert w.filter_stack.currentWidget() is w.scen_panel and not w.card_controls.isVisibleTo(w)
    assert w.current_type == "unit"         # card actions still know a card type
    assert not w.scen_panel.btn_save.isEnabled()
    rows[0].check.setChecked(True)
    rows[2].check.setChecked(True)
    assert "2 scenarios selected → 6 Letter pages" in w.scen_panel.summary.text()
    w.scen_panel.goal_cards.setChecked(False)
    assert "→ 4 Letter pages" in w.scen_panel.summary.text() and w.prefs["scen_goal_cards"] is False
    w.scen_panel.goal_cards.setChecked(True)

    out = tmp_path / "scen.pdf"
    monkeypatch.setattr(w, "_ask_output", lambda *a, **k: out)
    w.scen_panel.btn_save.click()
    while w.task is not None:
        w.task.wait(20000)
        app.processEvents()
    app.processEvents()
    assert len(PdfReader(str(out)).pages) == 6
    assert shown and "88 × 63 mm" in shown[-1]

    w.tabs.setCurrentIndex(0)
    assert w.card_controls.isVisibleTo(w)
    w.close()

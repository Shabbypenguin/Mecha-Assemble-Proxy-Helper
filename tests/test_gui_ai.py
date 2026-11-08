"""GUI tests for the AI enhancement parts, with the stand-in upscaler from test_enhance."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from mecha_proxy import enhance, gui, print_dialog  # noqa: E402

from .test_enhance import install_fake_tools  # noqa: E402
from .test_gui import home  # noqa: E402,F401 - fixture


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait(app, w):
    while w.task is not None:
        w.task.wait(20000)
        app.processEvents()
    app.processEvents()


@pytest.fixture
def win(app, home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(enhance, "supported", lambda: True)
    w = gui.MainWindow(str(home))
    yield w
    w.close()


def test_off_by_default(win):
    assert not win.prefs["ai_enabled"]
    assert not win.act_ai.isChecked() and win.act_ai.text() == "Enable AI enhancement…"
    assert not win.act_enhance_all.isEnabled() and not win.act_space.isEnabled()
    assert not win.card_tabs["unit"].btn_enh.isEnabled()
    assert all(not t.badge.isVisibleTo(t) for t in win.card_tabs["unit"].grid.tiles)
    assert "Kim2091" in enhance.CREDITS_HTML


def test_not_windows_cannot_enable(app, home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(enhance, "supported", lambda: False)
    w = gui.MainWindow(str(home))
    assert not w.act_ai.isEnabled() and "Windows" in w.act_ai.toolTip()
    w.close()


def test_enhance_badge_toggle_and_print(app, win, monkeypatch):
    install_fake_tools(win.cache.meta_dir)
    win.prefs["ai_enabled"] = True
    win._ai_sync_menu()
    assert win.act_enhance_all.isEnabled() and win.act_space.isEnabled()

    win.enhance_all()
    _wait(app, win)
    assert len(win.enhanced) == 2  # the two downloaded unit cards; tactics are opt-in
    assert "2 enhanced" in win.status.text()
    tiles = {t.card.code: t for t in win.card_tabs["unit"].grid.tiles}
    assert tiles["st01-u01"].badge.isVisibleTo(tiles["st01-u01"])
    assert not tiles["ex01-u01"].badge.isVisibleTo(tiles["ex01-u01"])  # not downloaded
    btn = win.card_tabs["unit"].btn_enh
    assert btn.isEnabled() and not btn.isChecked()

    btn.setChecked(True)  # grid + hover switch to enhanced, other tabs follow
    assert win.card_tabs["tactics"].btn_enh.isChecked() and win.prefs["show_enhanced"]
    assert tiles["st01-u01"].image_path == win.enhanced["st01-u01"]
    assert win.store.thumb(tiles["st01-u01"].card).stat().st_size < 200_000
    btn.setChecked(False)
    assert tiles["st01-u01"].image_path != win.enhanced["st01-u01"]

    # print dialog: no AI controls, enhanced used because AI is on; Smaller PDF offered
    tiles["st01-u01"].set_selected(True)
    sel = {"unit": win._selection("unit")}
    dlg = print_dialog.PrintDialog(win, sel, win.images, win.prefs, enhanced=win.enhanced)
    assert not hasattr(dlg, "use_enhanced") and not hasattr(dlg, "sharpness")
    assert "AI-enhanced: 1 of 1" in dlg.info.text()
    assert dlg.small_pdf.isVisibleTo(dlg) and not dlg.small_pdf.isChecked()
    full, small = dlg.diagram.pdf_mb
    assert 3 < full < 7 and 0.7 < small < 1.6
    s = dlg.settings("unit")
    assert s.enhanced and s.enhanced_dpi == 400 and not s.small_pdf
    dlg.small_pdf.setChecked(True)
    assert dlg.settings("unit").small_pdf and dlg.result_prefs()["small_pdf"]
    dlg.close()

    # AI switched off: originals print, even though enhanced copies exist
    win.prefs["ai_enabled"] = False
    assert not win.settings("unit").enhanced
    win.prefs["ai_enabled"] = True


def test_print_dialog_without_enhanced(app, win):
    win.card_tabs["unit"].grid.tiles[0].set_selected(True)
    dlg = print_dialog.PrintDialog(win, {"unit": win._selection("unit")}, win.images, win.prefs)
    assert not dlg.small_pdf.isVisibleTo(dlg)  # nothing it would affect
    assert dlg.result_prefs()["small_pdf"] is False
    assert not win.settings("unit").enhanced  # nothing to use -> not requested
    dlg.close()


def test_space_saving_popups(app, win, monkeypatch):
    install_fake_tools(win.cache.meta_dir)
    win.prefs["ai_enabled"] = True
    win._ai_sync_menu()
    win.enhance_all()
    _wait(app, win)
    seen = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: (seen.append(a[2]), QMessageBox.Ok)[1])

    win._ai_toggle_space_saving(True)
    _wait(app, win)
    assert win.prefs["ai_space_saving"] and win.act_space.isChecked()
    assert "400 DPI" in seen[-1]
    assert all(p.suffix == ".jpg" for p in win.enhanced.values())

    win._ai_toggle_space_saving(False)
    _wait(app, win)
    assert "Nothing needs to be downloaded" in seen[-1]
    assert not win.prefs["ai_space_saving"]
    assert all(p.suffix == ".webp" for p in win.enhanced.values())


def test_cancelled_popup_changes_nothing(app, win, monkeypatch):
    install_fake_tools(win.cache.meta_dir)
    win.prefs["ai_enabled"] = True
    win._ai_sync_menu()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)
    win._ai_toggle_space_saving(True)
    assert not win.prefs["ai_space_saving"] and not win.act_space.isChecked()
    win.prefs["ai_enabled"] = False
    win._ai_toggle_enabled(True)  # tools already installed -> enabled without a download or prompt
    assert win.task is None and win.prefs["ai_enabled"] and win.act_ai.isChecked()


def test_enable_prompt_then_install(app, win, monkeypatch):
    texts, installs = [], []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: (texts.append(a[2]), QMessageBox.Cancel)[1])
    win._ai_toggle_enabled(True)
    assert "MB" in texts[0] and "never uploaded" in texts[0] and "Kim2091" in texts[0]
    assert win.task is None and not win.prefs["ai_enabled"]  # cancelled: nothing downloaded

    def fake_install(self, presets, session=None, on_progress=None, **k):
        # only the files installed() looks for; the upscaler itself never runs in this test,
        # so this works on Windows too (unlike install_fake_tools, which skips there)
        installs.append(presets)
        self.models.mkdir(parents=True, exist_ok=True)
        self.exe.write_bytes(b"stub")
        for ext in ("param", "bin"):
            (self.models / f"{enhance.PRESETS['ultramix'].model}.{ext}").write_bytes(b"x")
        self.marker.write_text("{}")
    monkeypatch.setattr(enhance.Tools, "install", fake_install)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Ok)
    win._ai_toggle_enabled(True)
    _wait(app, win)
    assert installs == [["ultramix"]] and win.prefs["ai_enabled"] and win.act_ai.isChecked()


def test_gpu_error_shows_message(app, win, monkeypatch):
    install_fake_tools(win.cache.meta_dir)
    win.prefs["ai_enabled"] = True
    win._ai_sync_menu()
    monkeypatch.setenv("FAKE_GPU_FAIL", "1")
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, msg: errors.append(msg))
    win.enhance_all()
    _wait(app, win)
    assert errors and "Vulkan" in errors[0]


def test_remove_tools_keeps_images(app, win, monkeypatch):
    tools = install_fake_tools(win.cache.meta_dir)
    win.prefs["ai_enabled"] = True
    win._ai_sync_menu()
    win.enhance_all()
    _wait(app, win)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Ok)
    win._ai_remove_tools()
    assert not tools.exe.exists() and not win.prefs["ai_enabled"]
    assert win.store.count() == 2 and win.act_rm_enh.isEnabled()
    win._ai_remove_enhanced()
    assert win.store.count() == 0 and not win.enhanced
    assert not win.card_tabs["unit"].btn_enh.isEnabled()


def test_compare_dialog(app, win, monkeypatch):
    install_fake_tools(win.cache.meta_dir)
    win.prefs["ai_enabled"] = True
    win._ai_sync_menu()
    win.enhance_all()
    _wait(app, win)
    shown = []
    from mecha_proxy import gui_ai
    monkeypatch.setattr(gui_ai.CompareDialog, "exec", lambda self: shown.append(self) or 0)
    tile = win.card_tabs["unit"].grid.tiles[0]
    tile.compare_requested.emit(tile.card)
    assert shown and "lossless" in shown[0].findChildren(gui_ai.QLabel)[-1].text()
    v = shown[0].view
    shown[0].slider.setValue(250)
    assert v.split == 0.25
    v.resize(800, 600)
    v.zoom_at(gui_ai.QPointF(400, 300), 4.0)
    assert abs(v.zoom - 4.0) < 1e-6
    v.zoom_at(gui_ai.QPointF(400, 300), 0.01)  # can't zoom out past fit
    assert v.zoom == 1.0 and v._view is None

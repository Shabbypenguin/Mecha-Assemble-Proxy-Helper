"""AI enhancement parts of the desktop app: Enhance menu, prompts, compare window.

Mixed into MainWindow (see gui.py); uses its task runner, prefs and card tabs.
"""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QActionGroup, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QSlider, QVBoxLayout,
                               QWidget)

from . import config, enhance

# Seconds per unit card: (gaming graphics card, built-in graphics). Measured: UltraMix on an
# Intel UHD 770 (integrated) ~44 s/card; dedicated GPUs are roughly 10-15x faster.
SECONDS_PER_CARD = {"ultramix": (3.0, 45.0), "official": (1.0, 12.0)}


# ---- before/after compare -----------------------------------------------------------
class CompareView(QWidget):
    """Original left of the divider, enhanced right. Left-drag moves the divider, the mouse wheel
    zooms in on the spot under the cursor, right-drag pans, double-click fits the card again."""

    MAX_ZOOM = 10.0

    def __init__(self, original: Path, enhanced: Path, parent=None):
        super().__init__(parent)
        self.orig = QPixmap(str(original))
        self.enh = QPixmap(str(enhanced))
        self.split = 0.5          # divider position, fraction of the widget width
        self._view: QRectF | None = None   # where the card is drawn; None = fit to window
        self._pan_from = None
        self.setMinimumSize(640, 460)
        self.setCursor(Qt.SplitHCursor)

    def set_split(self, v: float):
        self.split = min(max(v, 0.0), 1.0)
        self.update()

    def _fit(self) -> QRectF:
        w, h = self.width(), self.height()
        ratio = self.enh.width() / max(self.enh.height(), 1) if not self.enh.isNull() else w / max(h, 1)
        tw, th = (w, w / ratio) if w / h < ratio else (h * ratio, h)
        return QRectF((w - tw) / 2, (h - th) / 2, tw, th)

    def _target(self) -> QRectF:
        return self._view if self._view is not None else self._fit()

    @property
    def zoom(self) -> float:
        return self._target().width() / max(self._fit().width(), 1)

    def zoom_at(self, pos: QPointF, factor: float):
        t, fit = self._target(), self._fit()
        z = min(max(self.zoom * factor, 1.0), self.MAX_ZOOM)
        if z <= 1.0001:
            self._view = None
        else:
            f = z / self.zoom
            self._view = QRectF(pos.x() - (pos.x() - t.left()) * f, pos.y() - (pos.y() - t.top()) * f,
                                fit.width() * z, fit.height() * z)
        self.update()

    def resizeEvent(self, e):  # noqa: N802 - Qt API
        self._view = None
        super().resizeEvent(e)

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#111"))
        t = self._target()
        x = self.width() * self.split
        for pix, clip in ((self.orig, QRectF(0, 0, x, self.height())),
                          (self.enh, QRectF(x, 0, self.width() - x, self.height()))):
            p.save()
            p.setClipRect(clip)
            p.drawPixmap(t, pix, QRectF(pix.rect()))
            p.restore()
        p.setPen(QPen(QColor("#e04a52"), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.fillRect(QRectF(0, 0, self.width(), 24), QColor(0, 0, 0, 140))
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(8, 3, 300, 18), Qt.AlignLeft, "◀ Original")
        p.drawText(QRectF(self.width() - 308, 3, 300, 18), Qt.AlignRight, "AI-enhanced ▶")
        p.drawText(QRectF(0, 3, self.width(), 18), Qt.AlignHCenter,
                   f"{self.zoom:.1f}×  ·  scroll to zoom, right-drag to pan, double-click to fit"
                   if self._view is not None else "Scroll to zoom in")
        p.end()

    def wheelEvent(self, e):  # noqa: N802
        steps = e.angleDelta().y() / 120
        if steps:
            self.zoom_at(e.position(), 1.25 ** steps)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._drag(e)
        elif e.button() in (Qt.RightButton, Qt.MiddleButton) and self._view is not None:
            self._pan_from = (e.position(), QRectF(self._view))
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._pan_from is not None:
            start, rect = self._pan_from
            d = e.position() - start
            self._view = rect.translated(d.x(), d.y())
            self.update()
        elif e.buttons() & Qt.LeftButton:
            self._drag(e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._pan_from is not None:
            self._pan_from = None
            self.setCursor(Qt.SplitHCursor)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self._view = None
        self.update()

    def _drag(self, e):
        if self.width() > 0:
            self.set_split(e.position().x() / self.width())
            if hasattr(self, "on_drag"):
                self.on_drag(self.split)


class CompareDialog(QDialog):
    def __init__(self, parent, card, original: Path, enhanced: Path, info: str):
        super().__init__(parent)
        self.setWindowTitle(f"Compare – {card.label} {card.name}")
        self.resize(1100, 820)
        v = QVBoxLayout(self)
        self.view = CompareView(original, enhanced)
        v.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Original"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.valueChanged.connect(lambda val: self.view.set_split(val / 1000))
        self.view.on_drag = lambda s: (self.slider.blockSignals(True), self.slider.setValue(round(s * 1000)),
                                       self.slider.blockSignals(False))
        row.addWidget(self.slider, 1)
        row.addWidget(QLabel("Enhanced"))
        v.addLayout(row)
        note = QLabel(info)
        note.setObjectName("muted")
        v.addWidget(note)


# ---- main-window mixin ------------------------------------------------------------
class AiMixin:
    """Enhance menu + actions. Expects MainWindow attributes: cache, prefs, card_tabs, images,
    store, tools, task, status, _start(), _save_prefs()."""

    # -- menu --
    def _build_ai_menu(self, mb):
        m = self.menu_ai = mb.addMenu("&Enhance")
        self.act_ai = m.addAction("Enable AI enhancement…")
        self.act_ai.setCheckable(True)
        self.act_ai.triggered.connect(self._ai_toggle_enabled)

        pm = m.addMenu("Quality preset")
        self._preset_group = QActionGroup(self)
        self.act_presets = {}
        for key, p in enhance.PRESETS.items():
            a = pm.addAction(p.label)
            a.setCheckable(True)
            a.setData(key)
            self._preset_group.addAction(a)
            a.triggered.connect(lambda _=False, k=key: self._ai_set_preset(k))
            self.act_presets[key] = a

        tm = m.addMenu("Card types to enhance")
        self.act_types = {}
        for t, ct in config.CARD_TYPES.items():
            a = tm.addAction(ct.label + ("  (already ~300 DPI)" if t == "tactics" else ""))
            a.setCheckable(True)
            a.toggled.connect(lambda on, k=t: self._ai_set_type(k, on))
            self.act_types[t] = a

        m.addSeparator()
        self.act_enhance_all = m.addAction("Enhance all now", lambda: self.enhance_all())
        self.act_ai_auto = m.addAction("Enhance new cards after Update")
        self.act_ai_auto.setCheckable(True)
        self.act_ai_auto.toggled.connect(lambda on: self._set_pref("ai_auto", on))
        self.act_space = m.addAction("Space-saving mode…")
        self.act_space.setCheckable(True)
        self.act_space.triggered.connect(self._ai_toggle_space_saving)
        m.addSeparator()
        m.addAction("Open enhanced folder", self._open_enhanced)
        self.act_rm_enh = m.addAction("Remove enhanced images…", self._ai_remove_enhanced)
        self.act_rm_tools = m.addAction("Remove AI tools…", self._ai_remove_tools)
        self._ai_sync_menu()

    def _set_pref(self, key, value):
        self.prefs[key] = value
        self._save_prefs()

    def _ai_sync_menu(self):
        enabled = bool(self.prefs.get("ai_enabled")) and self.tools.installed()
        ok_platform = enhance.supported()
        self.act_ai.setChecked(enabled)
        self.act_ai.setText("AI enhancement enabled" if enabled else "Enable AI enhancement…")
        self.act_ai.setEnabled(ok_platform or enabled)
        if not ok_platform:
            self.act_ai.setToolTip(enhance.UNSUPPORTED_MSG)
        preset = self.prefs.get("ai_preset", enhance.DEFAULT_PRESET)
        self.act_presets[preset].setChecked(True)
        types = set(self.prefs.get("ai_types", list(enhance.DEFAULT_TYPES)))
        for t, a in self.act_types.items():
            a.blockSignals(True)
            a.setChecked(t in types)
            a.blockSignals(False)
        self.act_ai_auto.blockSignals(True)
        self.act_ai_auto.setChecked(bool(self.prefs.get("ai_auto", True)))
        self.act_ai_auto.blockSignals(False)
        self.act_space.setChecked(bool(self.prefs.get("ai_space_saving")))
        self.act_space.setEnabled(enabled)
        self.act_space.setToolTip("" if enabled else
                                  "Only affects AI-enhanced images – enable AI enhancement first")
        for a in (self.act_enhance_all, self.act_ai_auto):  # preset/types can be chosen beforehand
            a.setEnabled(enabled)
        has_enh = self.store.count() > 0
        self.act_rm_enh.setEnabled(has_enh)
        self.act_rm_tools.setEnabled(self.tools.installed())

    # -- helpers --
    def _ai_items(self, only_codes: set[str] | None = None) -> list[tuple]:
        types = set(self.prefs.get("ai_types", list(enhance.DEFAULT_TYPES)))
        items = []
        for t, tab in self.card_tabs.items():
            if t not in types and not only_codes:
                continue
            for c in tab.cards:
                src = self.images.get(c.code)
                if src is not None and (only_codes is None or c.code in only_codes):
                    items.append((c, src))
        return items

    def _estimate(self, n: int) -> str:
        """Honest range, since speed depends hugely on the graphics chip."""
        fast, slow = SECONDS_PER_CARD.get(self.prefs.get("ai_preset", "ultramix"), (3.0, 45.0))

        def say(secs: float) -> str:
            if secs < 60:
                return f"{max(5, round(secs / 5) * 5)} seconds"
            mins = round(secs / 60)
            return f"{mins} minute{'s' * (mins != 1)}" if mins < 90 else f"{secs / 3600:.1f} hours"
        return (f"about {say(n * fast)} with a gaming graphics card, up to {say(n * slow)} on built-in "
                "graphics (you can cancel any time; finished cards are kept)")

    def _open_enhanced(self):
        from .gui import open_in_file_manager
        self.store.root.mkdir(parents=True, exist_ok=True)
        open_in_file_manager(self.store.root)

    # -- enable / install --
    def _ai_toggle_enabled(self, checked: bool):
        if not checked:  # turning off keeps tools and images; stops printing/creating enhanced copies
            self._set_pref("ai_enabled", False)
            self._ai_sync_menu()
            self.refresh_enhanced()
            self.status.setText("AI enhancement off – printing uses the original images. Enhanced copies are "
                                "kept for when you turn it back on.")
            return
        self.act_ai.setChecked(False)  # only becomes checked once installed
        preset = self.prefs.get("ai_preset", enhance.DEFAULT_PRESET)
        if self.tools.installed(preset):  # re-enabling: tools are still there
            self._set_pref("ai_enabled", True)
            self._ai_sync_menu()
            return
        if self._ai_download_prompt(preset):
            self._ai_install(preset, then_enable=True)

    def _ai_download_prompt(self, preset: str) -> bool:
        p = enhance.PRESETS[preset]
        need_engine = not self.tools.installed()
        engine_mb = self.tools.engine.mb
        size = (engine_mb if need_engine else 0) + p.download_mb
        gpu_note = ("a graphics card with Vulkan support is needed"
                    + ("; on Linux also the Vulkan driver packages, e.g. <code>mesa-vulkan-drivers</code>"
                       if enhance.platform_key() == "linux" else ""))
        e = html.escape
        text = (f"<b>Enable AI enhancement?</b><br><br>"
                f"This downloads about <b>{size} MB</b> of free, third-party tools and runs them on "
                f"<b>your own computer</b> ({gpu_note}). "
                f"Card images are never uploaded anywhere.<br><br>"
                f"<b>What it does:</b> makes sharper copies of your cards in a separate "
                f"<code>cards-enhanced</code> folder. Your original images are never changed.<br><br>"
                f"<b>Downloads, each checked against a fixed checksum before use:</b><ul>"
                + (f"<li>Real-ESRGAN ncnn-vulkan (official release by Xintao Wang, BSD-3) – "
                   f"{engine_mb} MB</li>" if need_engine else "")
                + (f"<li>{e(p.label)} – {e(p.credit)} – {p.download_mb} MB</li>" if p.files else "")
                + "</ul>Credits and licenses are listed in Help → About and saved with the tools.")
        r = QMessageBox.question(self, "AI enhancement", text, QMessageBox.Ok | QMessageBox.Cancel)
        return r == QMessageBox.Ok

    def _ai_install(self, preset: str, then_enable: bool):
        tools = self.tools

        def work(task):
            tools.install([preset], on_progress=lambda label, done, total:
                          task.step(label, f"{done / 1e6:.0f}/{total / 1e6:.0f} MB" if total else
                                    f"{done / 1e6:.0f} MB", min(done, total or done), total or done or 1))
            return True

        def done(_):
            self.prefs["ai_preset"] = preset
            if then_enable:
                self.prefs["ai_enabled"] = True
            self._save_prefs()
            self._ai_sync_menu()
            items = self._ai_items()
            if then_enable and items:
                r = QMessageBox.question(self, "AI enhancement",
                                         f"AI tools are ready.\n\nEnhance your {len(items)} cards now? "
                                         f"This takes {self._estimate(len(items))}.")
                if r == QMessageBox.Yes:
                    self._after_task = self.enhance_all

        self._start(work, done, "Downloading AI tools…", cancellable=False)

    def _ai_set_preset(self, key: str):
        if key == self.prefs.get("ai_preset"):
            return
        if not self.prefs.get("ai_enabled"):  # downloaded when AI is enabled
            self._set_pref("ai_preset", key)
            return
        if not self.tools.installed(key):
            if not self._ai_download_prompt(key):
                self._ai_sync_menu()
                return
            self._ai_install(key, then_enable=False)
        else:
            self._set_pref("ai_preset", key)
        self.status.setText(f"Preset: {enhance.PRESETS[key].label}. Use Enhance all now to re-enhance "
                            "existing cards with it.")

    def _ai_set_type(self, t: str, on: bool):
        types = set(self.prefs.get("ai_types", list(enhance.DEFAULT_TYPES)))
        (types.add if on else types.discard)(t)
        self._set_pref("ai_types", sorted(types))

    # -- enhancing --
    def enhance_all(self, only_codes: set[str] | None = None, label: str = "Enhancing cards…"):
        if not (self.prefs.get("ai_enabled") and self.tools.installed()):
            return
        items = self._ai_items(only_codes)
        if not items:
            self.status.setText("Nothing to enhance")
            return
        store, tools = self.store, self.tools
        preset = self.prefs.get("ai_preset", enhance.DEFAULT_PRESET)
        compressed = bool(self.prefs.get("ai_space_saving"))

        def work(task):
            return enhance.enhance_many(store, tools, items, preset, compressed, on_step=task.step,
                                        should_stop=lambda: task.cancelled)

        def done(result):
            n, skipped, failed = result
            msg = f"{n} enhanced, {skipped} already up to date"
            if failed:
                msg += f", {len(failed)} failed ({', '.join(failed)})"
            self.status.setText(msg)

        self._ai_task = True
        self._start(work, done, label, cancellable=True)

    def auto_enhance_new(self):
        """After Update: enhance cards with no enhanced copy yet, or whose image changed (errata)."""
        if not (self.prefs.get("ai_enabled") and self.prefs.get("ai_auto", True) and self.tools.installed()):
            return
        preset = self.prefs.get("ai_preset", enhance.DEFAULT_PRESET)
        compressed = bool(self.prefs.get("ai_space_saving"))
        missing = {c.code for c, src in self._ai_items()
                   if not self.store.is_current(c, src, preset, compressed)}
        if missing:
            self.enhance_all(missing, label="Enhancing new and updated cards…")

    # -- space saving --
    def _ai_toggle_space_saving(self, checked: bool):
        self.act_space.setChecked(not checked)  # stays as-is unless confirmed
        if checked:
            n = self.store.count()
            r = QMessageBox.question(
                self, "Space-saving mode",
                "<b>Turn on space-saving mode?</b><br><br>"
                "AI-enhanced images will be stored as high-quality JPEG (95%, full colour) at the "
                "400 DPI print size – about 1 MB each instead of about 5 MB – and PDFs made from them "
                "will be about 3× smaller.<br><br>"
                f"Your {n} existing enhanced image{'s' * (n != 1)} will be converted now, and new ones "
                "will be saved this way too. <b>Your original card images are not affected.</b><br><br>"
                "Turning it off later recreates the full-quality versions by running the AI again.",
                QMessageBox.Ok | QMessageBox.Cancel)
            if r != QMessageBox.Ok:
                return
            self._set_pref("ai_space_saving", True)
            self.act_space.setChecked(True)
            store = self.store
            cards = [c for tab in self.card_tabs.values() for c in tab.cards if store.entry(c)]

            def work(task):
                for i, c in enumerate(cards, 1):
                    store.compress(c)
                    store.save()
                    task.step(c.label, "compressed", i, len(cards))
                return len(cards)

            self._ai_task = True
            self._start(work, lambda n: self.status.setText(f"Space-saving mode on – {n} images converted"),
                        "Converting enhanced images…")
        else:
            codes = set(self.store.compressed_codes())
            r = QMessageBox.question(
                self, "Space-saving mode",
                "<b>Turn off space-saving mode?</b><br><br>"
                "Compressed images can't be turned back into full quality, so the AI will recreate "
                f"your {len(codes)} enhanced image{'s' * (len(codes) != 1)} from the original cards "
                f"({self._estimate(len(codes))}). Nothing needs to be downloaded again.<br><br>"
                "The compressed copies stay in place until each full-quality one is ready, so printing "
                "keeps working, and if you cancel nothing is lost.",
                QMessageBox.Ok | QMessageBox.Cancel)
            if r != QMessageBox.Ok:
                return
            self._set_pref("ai_space_saving", False)
            self.act_space.setChecked(False)
            if codes:
                self.enhance_all(codes, label="Recreating full-quality enhanced images…")

    # -- removal --
    def _ai_remove_enhanced(self):
        n = self.store.count()
        r = QMessageBox.question(self, "Remove enhanced images",
                                 f"Delete all {n} AI-enhanced images? Your original cards are kept, and "
                                 "you can enhance again any time.", QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.store.remove_all()
            self.prefs["show_enhanced"] = False
            self.refresh_enhanced()
            self.status.setText("Enhanced images removed")

    def _ai_remove_tools(self):
        r = QMessageBox.question(self, "Remove AI tools",
                                 "Remove the downloaded AI tools? This also turns AI enhancement off, so "
                                 "printing goes back to the original images.<br><br>Enhanced images you "
                                 "already have are kept and are used again if you re-enable AI later.",
                                 QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.tools.remove()
            self._set_pref("ai_enabled", False)
            self._ai_sync_menu()
            self.status.setText("AI tools removed")

    # -- compare --
    def compare_card(self, card):
        orig, enh = self.images.get(card.code), self.store.path(card)
        if not (orig and enh):
            return
        e = self.store.entry(card) or {}
        preset = enhance.PRESETS.get(e.get("preset"), None)
        info = (f"Enhanced with {preset.label if preset else e.get('preset')} · {e.get('width')}×{e.get('height')} px"
                f"{' · space-saving JPEG' if e.get('compressed') else ' · lossless'} · drag the image or the "
                "slider to compare")
        CompareDialog(self, card, orig, enh, info).exec()


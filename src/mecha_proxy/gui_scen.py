"""Scenarios tab: Bandai's scenario PDFs, listed with a preview, saved as US Letter PDFs."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from . import scenarios

THUMB_W = 150


class ScenarioRow(QFrame):
    toggled = Signal()
    open_requested = Signal(object)  # Path of the original PDF

    def __init__(self, sc: scenarios.Scenario, pdf: Path | None, thumb: Path | None):
        super().__init__()
        self.sc, self.pdf = sc, pdf
        self.setObjectName("tile")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 8, 8, 8)
        self.check = QCheckBox()
        self.check.setEnabled(pdf is not None)
        self.check.toggled.connect(self.toggled)
        h.addWidget(self.check, 0, Qt.AlignVCenter)
        pic = QLabel()
        pic.setFixedWidth(THUMB_W)
        pix = QPixmap(str(thumb)) if thumb else QPixmap()
        if not pix.isNull():
            pic.setPixmap(pix.scaledToWidth(THUMB_W, Qt.SmoothTransformation))
        else:
            pic.setText("Coming soon" if not sc.available else "No preview")
            pic.setAlignment(Qt.AlignCenter)
            pic.setObjectName("muted")
            pic.setFixedHeight(int(THUMB_W * 1.41))
        h.addWidget(pic)
        info = QVBoxLayout()
        title = QLabel(f"<b style='font-size:15px'>{html.escape(sc.title.title())}</b>")
        sub = QLabel(html.escape(sc.set_name.title()))
        sub.setObjectName("muted")
        if not sc.available:
            status = "Coming soon – it appears here once Bandai posts it"
        elif pdf is None:
            status = "Not downloaded yet – use Cards → Update cards"
        else:
            status = f"Updated {sc.updated or '?'}" + (f"  ·  {sc.size}" if sc.size else "")
        st = QLabel(status)
        st.setObjectName("muted")
        info.addWidget(title)
        info.addWidget(sub)
        info.addWidget(st)
        info.addStretch()
        btn = QPushButton("Open original")
        btn.setToolTip("Open Bandai's PDF as published (two-page spread)")
        btn.setEnabled(pdf is not None)
        btn.clicked.connect(lambda: self.open_requested.emit(self.pdf))
        row = QHBoxLayout()
        row.addWidget(btn)
        row.addStretch()
        info.addLayout(row)
        h.addLayout(info, 1)
        if not sc.available:  # coming soon: shown greyed out
            for w in (pic, title, sub, st):
                w.setEnabled(False)
        if pdf is not None:
            self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):  # noqa: N802 - click anywhere on the row toggles it
        if self.check.isEnabled() and e.button() == Qt.LeftButton:
            self.check.toggle()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if self.pdf is not None:
            self.open_requested.emit(self.pdf)


class ScenarioTab(QWidget):
    selection_changed = Signal()
    open_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[ScenarioRow] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        intro = QLabel("Bandai's official scenarios. Each prints as two Letter pages (the rulebook spread split "
                       "down the middle), optionally with a page of goal cards. Click a scenario to select it.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        v.addWidget(intro)
        self.empty = QLabel("No scenarios yet – use Cards → Update cards to download the list.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("muted")
        v.addWidget(self.empty)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self.list = QVBoxLayout(self.host)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.addStretch()
        self.scroll.setWidget(self.host)
        v.addWidget(self.scroll, 1)

    def set_items(self, items: list[tuple[scenarios.Scenario, Path | None, Path | None]]):
        chosen = {r.sc.key for r in self.rows if r.check.isChecked()}
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for sc, pdf, thumb in items:
            row = ScenarioRow(sc, pdf, thumb)
            row.setParent(self.host)  # never a stray top-level window
            row.check.setChecked(sc.key in chosen and pdf is not None)
            row.toggled.connect(self.selection_changed)
            row.open_requested.connect(self.open_requested)
            self.list.insertWidget(self.list.count() - 1, row)
            self.rows.append(row)
        self.empty.setVisible(not self.rows)
        self.scroll.setVisible(bool(self.rows))
        self.selection_changed.emit()

    def selected(self) -> list[tuple[scenarios.Scenario, Path]]:
        return [(r.sc, r.pdf) for r in self.rows if r.check.isChecked() and r.pdf is not None]

    def available_count(self) -> int:
        return sum(1 for r in self.rows if r.pdf is not None)


class ScenarioPanel(QWidget):
    """Sidebar for the Scenarios tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        head = QLabel("<b>Scenarios</b>")
        v.addWidget(head)
        self.goal_cards = QCheckBox("Add goal cards (2 per scenario)")
        self.goal_cards.setToolTip("Adds a page with two copies of the scenario goal card – one for each player – "
                                   "at 88 × 63 mm (tactics card size) with crop marks.\nThe card is cut out of "
                                   "Bandai's own PDF, nothing is redrawn.")
        v.addWidget(self.goal_cards)
        note = QLabel("Pages are scaled to fit Letter paper with a small margin. Print goal cards at "
                      "100% / Actual size.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        v.addStretch()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        self.btn_save = QPushButton("Save as Letter PDF…")
        self.btn_save.setObjectName("accent")
        v.addWidget(self.btn_save)

    def update_summary(self, n_selected: int):
        pages = n_selected * (3 if self.goal_cards.isChecked() else 2)
        self.summary.setText(f"{n_selected} scenario{'s' * (n_selected != 1)} selected → {pages} Letter page"
                             f"{'s' * (pages != 1)}" if n_selected else "Select scenarios on the left.")
        self.btn_save.setEnabled(n_selected > 0)


# ---- main-window mixin ------------------------------------------------------------------------
class ScenarioMixin:
    """Expects MainWindow attributes: cache, prefs, tabs, filter_stack, card_controls, status, _start()."""

    def _build_scenarios(self):
        self.scen_tab = ScenarioTab()
        self.scen_panel = ScenarioPanel()
        self.scen_panel.goal_cards.setChecked(bool(self.prefs.get("scen_goal_cards", True)))
        self.scen_panel.goal_cards.toggled.connect(self._scen_goal_toggled)
        self.scen_panel.btn_save.clicked.connect(self.save_scenarios)
        self.scen_tab.selection_changed.connect(self._scen_summary)
        self.scen_tab.open_requested.connect(self._open_scenario)
        self.scen_index = self.tabs.addTab(self.scen_tab, "Scenarios")
        self.filter_stack.addWidget(self.scen_panel)
        self._scen_summary()

    def _scen_goal_toggled(self, on: bool):
        self.prefs["scen_goal_cards"] = on
        self._save_prefs()
        self._scen_summary()

    def _scen_summary(self):
        self.scen_panel.update_summary(len(self.scen_tab.selected()))

    def reload_scenarios(self):
        thumbs = Path(self.cache.meta_dir) / "scenario-thumbs"
        items = []
        for sc in scenarios.saved_list(self.cache):
            pdf = scenarios.local_pdf(self.cache, sc)
            thumb = None
            if pdf is not None:
                thumb = thumbs / (pdf.stem + ".png")
                if not thumb.is_file() or thumb.stat().st_mtime < pdf.stat().st_mtime:
                    thumb = scenarios.thumbnail(pdf, thumb)
            items.append((sc, pdf, thumb))
        self.scen_tab.set_items(items)
        n = self.scen_tab.available_count()
        self.tabs.setTabText(self.scen_index, f"Scenarios ({n})" if n else "Scenarios")

    def _open_scenario(self, pdf: Path):
        from .gui import open_in_file_manager
        open_in_file_manager(pdf)

    def save_scenarios(self):
        from . import service
        chosen = self.scen_tab.selected()
        if not chosen:
            return
        default = service.default_output(self.cache.root / "output", ["scenarios"], suffix="scenarios")
        out = self._ask_output("Save scenarios PDF", default)
        if not out:
            return
        goal = self.scen_panel.goal_cards.isChecked()
        paths = [p for _, p in chosen]

        def work(task):
            task.log(f"Laying out {len(paths)} scenario(s)…")
            return scenarios.build_letter_pdf(paths, out, goal_cards=goal, log=task.log)

        def done(result):
            path, notes = result
            self.status.setText(f"Wrote {path.name}")
            lines = [f"Saved:\n  {path}"]
            if notes:
                lines.append("\n".join(notes))
            lines.append("Pages are already sized for Letter paper."
                         + (" Print at 100% / Actual size so the goal cards come out 88 × 63 mm." if goal else ""))
            from PySide6.QtWidgets import QMessageBox

            from .gui import APP_TITLE, open_in_file_manager
            box = QMessageBox(self)
            box.setWindowTitle(APP_TITLE)
            box.setText("\n\n".join(lines))
            open_btn = box.addButton("Open PDF", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Ok)
            box.exec()
            if box.clickedButton() is open_btn:
                open_in_file_manager(path)

        self._start(work, done, "Building scenarios PDF…")

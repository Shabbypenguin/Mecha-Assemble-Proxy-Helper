"""Rules menu: Bandai's rule documents, downloaded when picked and laid out for printing."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QRadioButton, QScrollArea, QVBoxLayout)

from . import config, rulesdocs

LAYOUT_HELP = {
    "pages": "One page per sheet side, as large as fits (100% where the page fits). Print single- or "
             "double-sided.",
    "booklet": "Two pages per sheet side, folded and stapled into a booklet half the paper size, with smaller "
               "text. See How to assemble… for the steps.",
}


def nice_title(title: str) -> str:
    return title.title() if title.isupper() else title


def show_guide(parent, paper: str, mode: str, reverse: bool, sheets: int | None = None,
               files: tuple[str, str] | None = None) -> None:
    """The booklet assembly guide (the same page that goes into the PDF) in a window."""
    img = rulesdocs.guide_image(paper, mode, reverse, sheets, width=1000, files=files)
    qimg = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()
    dlg = QDialog(parent)
    dlg.setWindowTitle("How to make the booklet")
    v = QVBoxLayout(dlg)
    pic = QLabel()
    pic.setPixmap(QPixmap.fromImage(qimg))
    pic.setAlignment(Qt.AlignCenter)
    scroll = QScrollArea()
    scroll.setWidget(pic)
    scroll.setWidgetResizable(True)
    v.addWidget(scroll, 1)
    close = QPushButton("Close")
    close.clicked.connect(dlg.accept)
    row = QHBoxLayout()
    row.addStretch()
    row.addWidget(close)
    v.addLayout(row)
    dlg.resize(min(img.width + 40, 1060), min(img.height + 80, 820))
    dlg.exec()


class RulesDialog(QDialog):
    """Paper + layout (+ booklet printing options) for one document, then Save PDF… (or Open original)."""

    OPEN_ORIGINAL = 2

    def __init__(self, parent, title: str, prefs: dict, sheets: int | None = None):
        super().__init__(parent)
        self.sheets = sheets
        self.setWindowTitle(nice_title(title))
        v = QVBoxLayout(self)
        intro = QLabel(f"<b>{nice_title(title)}</b><br>The latest version is downloaded from Bandai's site "
                       "when you save or open it.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        form = QFormLayout()
        self.paper = QComboBox()
        for k, (label, _) in config.PAPER_SIZES.items():
            self.paper.addItem(label, k)
        self.paper.setCurrentIndex(max(self.paper.findData(prefs.get("paper", config.DEFAULT_PAPER)), 0))
        self.paper.setToolTip("Same setting as Paper in the print dialog")
        form.addRow("Paper", self.paper)
        lay = QVBoxLayout()
        self.radios = {}
        for key, label in rulesdocs.LAYOUTS.items():
            r = QRadioButton(label)
            self.radios[key] = r
            lay.addWidget(r)
        form.addRow("Layout", lay)
        v.addLayout(form)

        # booklet options (only shown for the booklet layout)
        self.booklet_box = QGroupBox("Booklet")
        bf = QFormLayout(self.booklet_box)
        self.mode = QComboBox()
        for k, label in rulesdocs.MODES.items():
            self.mode.addItem(label, k)
        self.mode.setCurrentIndex(max(self.mode.findData(prefs.get("rules_mode", "duplex")), 0))
        self.mode.setToolTip("Manual: two files – print the fronts, put the stack back in the tray, print the backs")
        bf.addRow("Printing", self.mode)
        self.reverse = QCheckBox("Reverse back page order")
        self.reverse.setChecked(bool(prefs.get("rules_reverse", False)))
        self.reverse.setToolTip("Manual printing: tick this if the backs came out in the wrong order")
        self.guide = QCheckBox("Add an assembly guide page")
        self.guide.setChecked(bool(prefs.get("rules_guide", True)))
        self.guide.setToolTip("A first sheet with these steps (its back stays blank so the pages still pair up)")
        bf.addRow("", self.reverse)
        bf.addRow("", self.guide)
        self.btn_guide = QPushButton("How to assemble…")
        self.btn_guide.clicked.connect(self._show_guide)
        bf.addRow("", self.btn_guide)
        v.addWidget(self.booklet_box)

        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setObjectName("muted")
        self.help.setMinimumHeight(40)
        v.addWidget(self.help)
        btns = QHBoxLayout()
        orig = QPushButton("Open original")
        orig.setToolTip("Open Bandai's PDF as published")
        orig.clicked.connect(lambda: self.done(self.OPEN_ORIGINAL))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.save = QPushButton("Save PDF…")
        self.save.setObjectName("accent")
        self.save.setDefault(True)
        self.save.clicked.connect(self.accept)
        btns.addWidget(orig)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(self.save)
        v.addLayout(btns)

        for r in self.radios.values():
            r.toggled.connect(self._update)
        self.mode.currentIndexChanged.connect(self._update)
        self.radios.get(prefs.get("rules_layout"), self.radios["pages"]).setChecked(True)
        self._update()
        self.resize(480, self.sizeHint().height())

    @property
    def layout_key(self) -> str:
        return next((k for k, r in self.radios.items() if r.isChecked()), "pages")

    def options(self) -> dict:
        """Booklet print options, as passed to rulesdocs.build_pdf."""
        return {"mode": self.mode.currentData(),
                "reverse": self.reverse.isChecked(), "guide": self.guide.isChecked()}

    def _update(self, *_):
        booklet = self.layout_key == "booklet"
        self.booklet_box.setVisible(booklet)
        self.reverse.setVisible(self.mode.currentData() == "manual")
        self.help.setText(LAYOUT_HELP[self.layout_key])
        self.adjustSize()

    def _show_guide(self):
        o = self.options()
        show_guide(self, self.paper.currentData(), o["mode"], o["reverse"], self.sheets)


class RulesMixin:
    """Expects MainWindow attributes: cache, prefs, status, _start(), _ask_output(), _save_prefs()."""

    def _build_rules_menu(self, mb):
        self.menu_rules = mb.addMenu("&Rules")
        self.menu_rules.aboutToShow.connect(self._fill_rules_menu)
        self._fill_rules_menu()

    def _fill_rules_menu(self):
        m = self.menu_rules
        m.clear()
        docs = [d for d in rulesdocs.saved_list(self.cache) if d.available]
        for d in docs:
            act = m.addAction(nice_title(d.title) + "…", lambda t=d.title: self.open_rules_doc(t))
            act.setStatusTip(f"Last updated {d.updated or '?'}" + (f" · {d.size}" if d.size else ""))
        if not docs:
            act = m.addAction("No documents listed yet – use Refresh list")
            act.setEnabled(False)
        m.addSeparator()
        m.addAction("Refresh list", self.refresh_rules_list).setStatusTip(
            "Read Bandai's Rules page again for new documents (Update cards does this too)")
        m.addAction("Open rules folder", self._open_rules_folder)

    def _open_rules_folder(self):
        from .gui import open_in_file_manager
        rulesdocs.folder(self.cache).mkdir(parents=True, exist_ok=True)
        open_in_file_manager(rulesdocs.folder(self.cache))

    def refresh_rules_list(self):
        def done(docs):
            self.status.setText(f"Rules: {len(docs)} document{'s' * (len(docs) != 1)} on Bandai's site")
        self._start(lambda task: rulesdocs.refresh_list(self.cache), done, "Reading Bandai's Rules page…")

    def open_rules_doc(self, title: str):
        have = rulesdocs.local_copy(self.cache, title)
        dlg = RulesDialog(self, title, self.prefs, sheets=rulesdocs.sheets_for(have[0] if have else None))
        result = dlg.exec()
        if result == QDialog.Rejected:
            return
        if dlg.paper.currentData() != self.prefs.get("paper"):
            self.prefs["paper"] = dlg.paper.currentData()
            if hasattr(self, "_scen_sync_paper"):
                self._scen_sync_paper()
            if hasattr(self, "_update_summary"):
                self._update_summary()
        self.prefs["rules_layout"] = dlg.layout_key
        opts = dlg.options()
        self.prefs.update({"rules_mode": opts["mode"],
                           "rules_reverse": opts["reverse"], "rules_guide": opts["guide"]})
        self._save_prefs()
        if result == RulesDialog.OPEN_ORIGINAL:
            self._open_rules_original(title)
        else:
            self._save_rules_pdf(title, dlg.paper.currentData(), dlg.layout_key, opts)

    def _open_rules_original(self, title: str):
        from .gui import open_in_file_manager

        def done(result):
            path, note = result
            self.status.setText(note or f"Opened {path.name}")
            open_in_file_manager(path)
        self._start(lambda task: rulesdocs.get_latest(self.cache, title, log=task.log), done,
                    f"Getting the latest {nice_title(title)}…")

    def _save_rules_pdf(self, title: str, paper: str, layout: str, opts: dict | None = None):
        from . import service
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "rules"
        default = service.default_output(self.cache.root / "output", [], suffix=slug + ("-booklet" if layout ==
                                                                                       "booklet" else ""))
        out = self._ask_output(f"Save {nice_title(title)}", default)
        if not out:
            return

        def work(task):
            src, note = rulesdocs.get_latest(self.cache, title, log=task.log)
            task.log("Laying out pages…")
            path, info = rulesdocs.build_pdf(src, out, paper=paper, layout=layout, log=task.log, **(opts or {}))
            return path, info, note

        def done(result):
            from .gui import APP_TITLE, open_in_file_manager
            path, info, note = result
            self.status.setText(f"Wrote {path.name}")
            saved = "\n".join(f"  {f}" for f in info["files"])
            text = "\n\n".join(x for x in (f"Saved:\n{saved}", note, rulesdocs.how_to_print(info)) if x)
            box = QMessageBox(self)
            box.setWindowTitle(APP_TITLE)
            box.setText(text)
            guide_btn = (box.addButton("How to assemble", QMessageBox.ActionRole)
                         if info["layout"] == "booklet" else None)
            open_btn = box.addButton("Open PDF" if len(info["files"]) == 1 else "Open fronts PDF",
                                     QMessageBox.ActionRole)
            box.addButton(QMessageBox.Ok)
            box.exec()
            if guide_btn is not None and box.clickedButton() is guide_btn:
                show_guide(self, info["paper"], info["mode"], info["reverse"], info["sheets"],
                           tuple(f.name for f in info["files"]) if info["mode"] == "manual" else None)
            elif box.clickedButton() is open_btn:
                open_in_file_manager(path)

        self._start(work, done, f"Preparing {nice_title(title)}…")

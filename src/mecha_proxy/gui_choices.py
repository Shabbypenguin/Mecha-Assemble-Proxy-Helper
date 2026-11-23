"""The "What do you want to download?" window: first launch and every Cards → Update cards."""
from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel,
                               QRadioButton, QVBoxLayout)

from . import config

# what can be downloaded, in the order shown; card types first
KINDS = ["unit", "tactics", "other", "scenarios", "maps"]
KIND_LABELS = {"unit": "Unit cards", "tactics": "Tactics cards", "other": "Tokens",
               "scenarios": "Scenarios", "maps": "Maps"}


def chosen_types(prefs: dict) -> list[str]:
    """The kinds the user wants (everything until they choose otherwise)."""
    got = prefs.get("download_types")
    if not isinstance(got, list):
        return list(KINDS)
    return [k for k in KINDS if k in got]


class DownloadChoicesDialog(QDialog):
    """Paper size + what to download. hints: {kind: "15 cards" / "about 42 MB"} shown next to each box."""

    def __init__(self, parent, prefs: dict, hints: dict[str, str] | None = None,
                 map_sizes: dict[str, str] | None = None, first_run: bool = False):
        super().__init__(parent)
        self.setWindowTitle("What do you want to download?")
        self.map_sizes = map_sizes or {}
        v = QVBoxLayout(self)
        intro = QLabel(("Welcome! " if first_run else "")
                       + "Pick your paper size and what to download from Bandai's site. Anything you leave out "
                         "gets no tab; you can change this every time you update.")
        intro.setWordWrap(True)
        v.addWidget(intro)

        paper_box = QGroupBox("Paper")
        ph = QHBoxLayout(paper_box)
        self.paper_group = QButtonGroup(self)
        self.paper_radios = {}
        for key, (label, _) in config.PAPER_SIZES.items():
            r = QRadioButton(label)
            self.paper_group.addButton(r)
            self.paper_radios[key] = r
            ph.addWidget(r)
            r.toggled.connect(self._update)
        ph.addStretch()
        self.paper_radios.get(prefs.get("paper"), self.paper_radios[config.DEFAULT_PAPER]).setChecked(True)
        v.addWidget(paper_box)

        what = QGroupBox("Download")
        wv = QVBoxLayout(what)
        self.boxes: dict[str, QCheckBox] = {}
        chosen = chosen_types(prefs)
        hints = hints or {}
        for kind in KINDS:
            cb = QCheckBox(KIND_LABELS[kind])
            cb.setChecked(kind in chosen)
            cb.toggled.connect(self._update)
            self.boxes[kind] = cb
            wv.addWidget(cb)
        self.hints = hints
        v.addWidget(what)
        note = QLabel("Card lists and details are always kept up to date (they're tiny and the filters need "
                      "them); this decides which images, PDFs and tabs you get. Unticking hides a tab but keeps "
                      "what's already downloaded.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setText("Download")
        ok.setObjectName("accent")  # the app's red main-action button
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)
        self._update()
        self.resize(460, self.sizeHint().height())

    @property
    def paper(self) -> str:
        return next(k for k, r in self.paper_radios.items() if r.isChecked())

    @property
    def types(self) -> list[str]:
        return [k for k in KINDS if self.boxes[k].isChecked()]

    def _update(self, *_):
        if not hasattr(self, "boxes") or len(self.boxes) < len(KINDS):
            return
        for kind, cb in self.boxes.items():
            hint = self.map_sizes.get(self.paper) if kind == "maps" else self.hints.get(kind)
            cb.setText(KIND_LABELS[kind] + (f"  ({hint})" if hint else ""))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.types))

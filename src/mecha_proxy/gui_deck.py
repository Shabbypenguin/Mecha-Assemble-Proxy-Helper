"""Paste-a-deck-code dialog."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from . import deckcode

EXAMPLE = "1x ST01-U02\n1x ST01-T02\n2x EX01-T05"


class PasteDeckDialog(QDialog):
    """Text box (pre-filled from the clipboard when it holds a deck) with a live summary.

    "Select these cards" replaces the current selection with the deck.
    """

    def __init__(self, parent, known: set[str], clipboard: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Paste deck code")
        self.resize(460, 420)
        self.known = known
        self.result_cards: dict[str, int] = {}
        v = QVBoxLayout(self)
        intro = QLabel("Paste a deck code – one card per line, like <code>1x ST01-U02</code>. "
                       "Card names, extra text and missing quantities are fine.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(EXAMPLE)
        if clipboard:  # pre-fill only when the clipboard actually holds card codes
            found = deckcode.parse(clipboard, known)
            if found.cards or found.unknown:
                self.text.setPlainText(clipboard[:deckcode.MAX_CHARS])
        v.addWidget(self.text, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        v.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("Select these cards")
        self.ok.setToolTip("Replaces the cards selected now on every tab")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)
        self.text.textChanged.connect(self._update)
        self._update()

    def _update(self):
        res = deckcode.parse(self.text.toPlainText(), self.known)
        self.result_cards = res.cards
        e = html.escape
        if not res.cards and not res.unknown:
            msg = "No card codes found yet." if self.text.toPlainText().strip() else ""
        else:
            n = len(res.cards)
            msg = (f"<b>{res.total} card{'s' * (res.total != 1)}</b> "
                   f"({n} different) will be selected." if res.cards else "No known cards found.")
            if res.unknown:
                msg += (f"<br><span style='color:#e8a33d'>Not in your card list: {e(', '.join(res.unknown))}</span>"
                        " – try Cards → Update cards first.")
        if res.truncated:
            msg += "<br>Only the first part of a very long text was read."
        self.summary.setText(msg)
        self.ok.setEnabled(bool(res.cards))

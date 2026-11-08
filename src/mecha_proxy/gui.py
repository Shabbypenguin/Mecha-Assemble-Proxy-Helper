"""Desktop GUI (PySide6). Same engine as the CLI, via mecha_proxy.service.

Launch with `mecha-proxy-gui` or `mecha-proxy gui`.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QImageReader, QKeySequence, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
                               QTabWidget, QVBoxLayout, QWidget)

from . import __version__, config, deckcode, enhance, filters, scraper, service
from .cache import atomic_write
from .gui_ai import AiMixin
from .gui_deck import PasteDeckDialog
from .print_dialog import PrintDialog, settings_from_prefs
from .widgets import ChipBox, HoverPreview, RangeBox

APP_TITLE = "Mecha-Assemble-Proxy-Helper"
ACCENT = "#c62832"
TILE_IMG_W = {"unit": 200, "tactics": 150, "other": 200}
TILE_PAD = 20      # tile margins + border around the image
GRID_SPACING = 10
HOVER_DELAY_MS = 400
HOVER_SCALE = 2.8   # hover preview: at most this many times the thumbnail width (≈ real printed size)
CHIP_COLORS = {"set": "#4a5a7a", "signature": "#7a4a6e", "trait": "#3f6e5a", "kind": "#7a6340",
               "usable": "#2f6378", "who": "#7a4a6e"}
DEFAULT_PREFS = {"mode": "duplex", "flip": "long", "bleed": 0.0, "gap": 0.0, "off_x": 0.0, "off_y": 0.0,
                 "crop": True, "reverse": False, "custom_back": None, "use_custom_back": False,
                 "auto_check": True, "last_check": 0.0,
                 # AI enhancement (all off until the user enables it from the Enhance menu)
                 "ai_enabled": False, "ai_preset": enhance.DEFAULT_PRESET, "ai_types": list(enhance.DEFAULT_TYPES),
                 "ai_auto": True, "ai_space_saving": False, "show_enhanced": False,
                 "small_pdf": False,
                 "changes_ack": "",
                 "skip_app_version": ""}  # app update the user already opened (don't nag again)  # newest Bandai card change the user has already looked at
CHECK_INTERVAL_S = 24 * 3600   # look for new cards on the site at most once a day
BANNER_BG, BANNER_FG = "#2C52B3", "#FFFFFF"

STYLE = f"""
QPushButton {{ padding: 7px 12px; border-radius: 6px; background: #3a3b3f; border: 1px solid #4a4b50; }}
QPushButton:hover {{ background: #45464b; }}
QPushButton:disabled {{ color: #7a7b80; }}
QPushButton#accent {{ background: {ACCENT}; border: none; font-weight: bold; color: white; }}
QPushButton#accent:hover {{ background: #d93a44; }}
QPushButton#accent:disabled {{ background: #5a2a2e; color: #b0a0a0; }}
QPushButton:checked {{ background: #4a2226; border: 1px solid #e04a52; }}
QLabel#updated {{ background: rgba(20, 20, 24, 190); color: #7fd1ff; border-radius: 9px; font-weight: bold;
                 padding: 1px 6px; }}
QLabel#badge {{ background: rgba(20, 20, 24, 190); color: #ffd36b; border-radius: 9px; font-weight: bold;
               padding: 1px 6px; }}
QPushButton#reset {{ background: #2f3a4f; border: 1px solid #5a7bb5; color: #dbe6ff; font-weight: bold; }}
QPushButton#reset:hover {{ background: #3a4964; }}
QGroupBox {{ border: 1px solid #3c3d42; border-radius: 8px; margin-top: 14px; padding: 10px 8px 8px 8px;
            font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; }}
QFrame#tile {{ background: #2b2c30; border-radius: 8px; border: 2px solid transparent; }}
QFrame#tile[selected="true"] {{ border: 2px solid #e04a52; }}
QFrame#panel {{ background: #26272b; border-radius: 8px; }}
QTabWidget::pane {{ border: 1px solid #3c3d42; border-radius: 8px; }}
QLabel#muted {{ color: #9aa0a6; }}
QLabel#title {{ font-weight: bold; font-size: 14px; }}
QLabel#empty {{ color: #9aa0a6; font-size: 15px; }}
QPushButton#banner {{ background: {BANNER_BG}; color: {BANNER_FG}; border: none; border-radius: 0;
                     padding: 9px; font-weight: bold; text-align: center; }}
QPushButton#banner:hover {{ background: #3a63c9; }}
QToolTip {{ background: #303134; color: #e8eaed; border: 1px solid #4a4b50; padding: 6px; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in [(QPalette.Window, "#202124"), (QPalette.WindowText, "#e8eaed"),
                    (QPalette.Base, "#2b2c30"), (QPalette.AlternateBase, "#303134"),
                    (QPalette.Text, "#e8eaed"), (QPalette.Button, "#303134"),
                    (QPalette.ButtonText, "#e8eaed"), (QPalette.Highlight, ACCENT),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#303134"),
                    (QPalette.ToolTipText, "#e8eaed"), (QPalette.PlaceholderText, "#80868b")]:
        pal.setColor(role, QColor(c))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#6b6c70"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6b6c70"))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)


def app_icon() -> QIcon:
    """The window/taskbar icon: bundled next to the code in builds, packaging/ in a checkout."""
    base = Path(getattr(sys, "_MEIPASS", ""))
    for cand in (base / "icon.ico", Path(__file__).resolve().parents[2] / "packaging" / "icon.ico"):
        if cand.is_file():
            return QIcon(str(cand))
    return QIcon()


def open_in_file_manager(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 - our own folder
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(path)], check=False)


def details_html(card: scraper.Card) -> str:
    """Readable text summary of a card for tooltips."""
    d = card.details or {}
    e = html.escape
    out = [f"<b>{e(card.label)}  {e(card.name)}</b>"]
    if d.get("model_no"):
        out[0] += f"  <span style='color:#9aa0a6'>{e(d['model_no'])}</span>"
    stats = "  ".join(f"<b>{k.upper()}</b> {d[k]}" for k in ("hp", "vp", "tl") if k in d)
    if stats:
        out.append(stats)
    if d.get("signature"):
        out.append(f"Signature: {e(d['signature'])}")
    if d.get("traits"):
        out.append("Traits: " + e(", ".join(d["traits"])))
    if d.get("effect_type"):
        out.append(f"Effect type: {e(d['effect_type'].capitalize())}")
    if d.get("effect"):
        out.append(e(d["effect"]))
    for w in d.get("weapons", []):
        line = f"<b>⚔ {e(w['name'])}</b>"
        if w.get("effect"):
            line += f" – {e(w['effect'])}"
        if w.get("critical"):
            line += f"<br>&nbsp;&nbsp;Critical: {e(w['critical'])}"
        out.append(line)
    for a in d.get("abilities", []):
        kinds = "/".join(k.capitalize() for k in a.get("kinds", []))
        out.append(f"<b>{e(a['name'])}</b>" + (f" [{e(kinds)}]" if kinds else "")
                   + (f" – {e(a['effect'])}" if a.get("effect") else ""))
    if not d:
        out.append("<i>No details yet – run Update cards.</i>")
    return "<div style='max-width:420px'>" + "<br>".join(out) + "</div>"


# ---- background work ------------------------------------------------------------
class Task(QThread):
    """Run fn(task) off the UI thread. fn may call task.step(...) / task.log(...)."""

    stepped = Signal(str, str, int, int)
    logged = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._stop = False

    def step(self, label, status, done, total):
        self.stepped.emit(label, status, done, total)

    def log(self, msg):
        self.logged.emit(str(msg))

    def cancel(self):
        self._stop = True

    @property
    def cancelled(self) -> bool:
        return self._stop

    def run(self):
        try:
            self.finished_ok.emit(self._fn(self))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user in a dialog
            self.failed.emit(str(exc) or exc.__class__.__name__)


# ---- card tiles -----------------------------------------------------------------
class ClickLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, e):  # noqa: N802 - Qt API
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class CardTile(QFrame):
    changed = Signal()
    compare_requested = Signal(object)

    def __init__(self, card: scraper.Card, img_w: int, parent=None):
        super().__init__(parent)
        self.card = card
        self.image_path: Path | None = None   # what the hover preview shows
        self.has_enhanced = False
        self.haystack = scraper.search_text(card)
        self.setObjectName("tile")
        self.setProperty("selected", False)
        self.setFixedWidth(img_w + TILE_PAD)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        self.img = ClickLabel()
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setCursor(Qt.PointingHandCursor)
        self.img_w = img_w
        self.img.setFixedWidth(img_w)
        self.img.installEventFilter(self)
        self.img.setContextMenuPolicy(Qt.CustomContextMenu)
        self.img.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self.img, alignment=Qt.AlignHCenter)
        self.badge = QLabel("✦", self.img)
        self.badge.setObjectName("badge")
        self.badge.setToolTip("Has an AI-enhanced copy – right-click to compare")
        self.badge.adjustSize()
        self.badge.move(img_w - self.badge.width() - 4, 4)
        self.badge.hide()
        self.updated = QLabel("↻ Updated", self.img)  # Bandai changed this card (errata)
        self.updated.setObjectName("updated")
        self.updated.adjustSize()
        self.updated.move(4, 4)
        self.updated.hide()
        name = QLabel(name_text := card.name)
        name.setObjectName("muted")
        name.setFixedWidth(img_w)
        name.setText(name.fontMetrics().elidedText(name_text, Qt.ElideRight, img_w))
        name.setToolTip(details_html(card))
        lay.addWidget(name)
        row = QHBoxLayout()
        self.check = QCheckBox(card.label)
        self.check.setToolTip(details_html(card))
        self.qty = QSpinBox()
        self.qty.setRange(1, 99)
        self.qty.setToolTip("Copies to print")
        row.addWidget(self.check)
        row.addStretch()
        row.addWidget(self.qty)
        lay.addLayout(row)
        self.img.clicked.connect(self.check.toggle)
        self.check.toggled.connect(self._on_toggle)
        self.qty.valueChanged.connect(self._on_qty)
        self._hover = QTimer(self)
        self._hover.setSingleShot(True)
        self._hover.setInterval(HOVER_DELAY_MS)
        self._hover.timeout.connect(self._show_preview)
        self.set_image(None)

    # hover-to-enlarge
    def eventFilter(self, obj, ev):  # noqa: N802 - Qt API
        if obj is self.img:
            if ev.type() == QEvent.Enter and self.image_path:
                self._hover.start()
            elif ev.type() in (QEvent.Leave, QEvent.MouseButtonPress, QEvent.Hide):
                self._hover.stop()
                HoverPreview.get().hide()
            elif ev.type() == QEvent.MouseMove and HoverPreview.get().isVisible() and self.image_path:
                HoverPreview.get().show_for(self.image_path, QCursor.pos(), round(self.img_w * HOVER_SCALE))
        return False

    def _show_preview(self):
        if self.image_path and self.img.underMouse():
            HoverPreview.get().show_for(self.image_path, QCursor.pos(), round(self.img_w * HOVER_SCALE))

    def _context_menu(self, pos):
        m = QMenu(self)
        a = m.addAction("Compare original / enhanced…", lambda: self.compare_requested.emit(self.card))
        a.setEnabled(self.has_enhanced)
        if not self.has_enhanced:
            a.setToolTip("No AI-enhanced copy yet")
        m.exec(self.img.mapToGlobal(pos))

    def set_image(self, path: Path | None, preview: Path | None = None, enhanced: bool = False):
        """path: thumbnail source; preview: full image for hover (defaults to path)."""
        self.has_enhanced = enhanced and path is not None
        self.badge.setVisible(self.has_enhanced)
        self.badge.raise_()
        self.image_path = None
        if path:
            reader = QImageReader(str(path))
            size = reader.size()
            if size.isValid() and size.width() > 0:
                reader.setScaledSize(QSize(self.img_w, round(size.height() * self.img_w / size.width())))
            img = reader.read()
            if not img.isNull():
                self.image_path = Path(preview or path)
                self.img.setStyleSheet("")
                self.img.setPixmap(QPixmap.fromImage(img))
                self.img.setFixedHeight(img.height())
                self.img.setMouseTracking(True)
                self.img.setToolTip("")
                return
        w, h = config.CARD_TYPES[self.card.type].size_mm
        self.img.setFixedHeight(round(self.img_w * h / w))
        self.img.setText("not downloaded\nyet")
        self.img.setToolTip("Downloads when you print it, or click Update cards")
        self.img.setStyleSheet("color:#80868b; border:1px dashed #4a4b50; border-radius:6px;")

    def _on_toggle(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)
        self.changed.emit()

    def _on_qty(self, _):
        if not self.check.isChecked():
            self.check.setChecked(True)  # editing a quantity implies you want the card
        else:
            self.changed.emit()

    def set_selected(self, on: bool):
        self.check.setChecked(on)

    def set_changed(self, change: dict | None):
        """Show the "Updated" marker for a card Bandai changed ({"date", "fields"}), or hide it."""
        self.updated.setVisible(bool(change))
        if change:
            fields = ", ".join(scraper.FIELD_LABELS.get(f, f) for f in change.get("fields", []))
            self.updated.setToolTip(f"Updated by Bandai on {change.get('date', '')[:10]}: {fields}.\n"
                                    "The card details shown are the new ones.")
            self.updated.raise_()

    @property
    def selected(self) -> bool:
        return self.check.isChecked()


class CardGrid(QScrollArea):
    """Thumbnail grid that reflows to the window width."""

    selection_changed = Signal()
    compare_requested = Signal(object)

    def __init__(self, card_type: str, parent=None):
        super().__init__(parent)
        self.card_type = card_type
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._grid.setSpacing(GRID_SPACING)
        self.setWidget(self._host)
        self.tiles: list[CardTile] = []
        self._shown: list[CardTile] = []
        self._cols = 0
        self._predicate = lambda tile: True

    def set_cards(self, cards: list[scraper.Card], keep: dict[str, int] | None = None):
        keep = keep or {}
        for t in self.tiles:
            t.setParent(None)
            t.deleteLater()
        self.tiles = []
        for c in cards:
            tile = CardTile(c, TILE_IMG_W[self.card_type])
            tile.setParent(self._host)  # a parentless widget made visible pops up as its own window
            tile.hide()
            if c.code in keep:
                tile.qty.blockSignals(True)
                tile.qty.setValue(keep[c.code])
                tile.qty.blockSignals(False)
                tile.set_selected(True)
            tile.changed.connect(self.selection_changed)
            tile.compare_requested.connect(self.compare_requested)
            self.tiles.append(tile)
        self.refilter()

    def set_images(self, images: dict[str, Path | None], enhanced: dict[str, Path] | None = None,
                   thumbs: dict[str, Path | None] | None = None, show_enhanced: bool = False):
        enhanced, thumbs = enhanced or {}, thumbs or {}
        for t in self.tiles:
            code = t.card.code
            orig, enh = images.get(code), enhanced.get(code)
            if show_enhanced and enh and orig:
                t.set_image(thumbs.get(code) or enh, preview=enh, enhanced=True)
            else:
                t.set_image(orig, enhanced=bool(enh))

    def set_predicate(self, fn):
        self._predicate = fn
        self.refilter()

    def refilter(self):
        self._shown = [t for t in self.tiles if self._predicate(t)]
        self._cols = 0
        self._relayout()

    def visible_tiles(self) -> list[CardTile]:
        return list(self._shown)

    def _relayout(self):
        tile_w = TILE_IMG_W[self.card_type] + TILE_PAD + GRID_SPACING
        cols = max(1, (self.viewport().width() - GRID_SPACING) // tile_w)
        if cols == self._cols:
            return
        self._cols = cols
        while self._grid.count():
            self._grid.takeAt(0)
        shown = set(map(id, self._shown))
        for i, t in enumerate(self._shown):
            self._grid.addWidget(t, i // cols, i % cols)
        for t in self.tiles:
            t.setVisible(id(t) in shown)

    def resizeEvent(self, e):  # noqa: N802 - Qt API
        super().resizeEvent(e)
        self._relayout()

    def selection(self) -> dict[str, int]:
        return {t.card.code: t.qty.value() for t in self.tiles if t.selected}


# ---- filters --------------------------------------------------------------------
class FilterPanel(QFrame):
    """Left-hand filters for one card type."""

    changed = Signal()
    reset_requested = Signal()

    def __init__(self, card_type: str, parent=None):
        super().__init__(parent)
        self.card_type = card_type
        self.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        scroll.setWidget(host)
        outer.addWidget(scroll)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("Filters")
        t.setObjectName("title")
        head.addWidget(t)
        head.addStretch()
        self.count = QLabel()
        self.count.setObjectName("muted")
        head.addWidget(self.count)
        lay.addLayout(head)
        self.reset_btn = QPushButton("↺  Reset filters")
        self.reset_btn.setObjectName("reset")
        self.reset_btn.setToolTip("Clear every filter and the search box")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)

        kind_label = "Effect type" if card_type == "tactics" else "Ability type"
        if card_type == "tactics":
            # deck rules instead of raw tags: a tactics card has at most one signature or trait
            specs = [("usable", "Usable by", "any of these units"), ("who", "Who can use it", "any of"),
                     ("set", "Set", "any of"), ("kind", kind_label, "must have all")]
        else:  # no signature box: each unit's signature is unique, so search covers it
            specs = [("set", "Set", "any of"), ("trait", "Traits", "must have all"),
                     ("kind", kind_label, "must have all")]
        self._units: dict[str, scraper.Card] = {}
        self.boxes: dict[str, ChipBox] = {}
        for key, title, hint in specs:
            box = ChipBox(title, hint, CHIP_COLORS[key])
            box.changed.connect(self.changed)
            if key == "usable":
                box.setToolTip("Pick units (e.g. the ones in your deck) to see every tactics card at least one "
                               "of them can use: cards for any unit, each unit's signature card, and cards for "
                               "traits it has. The number is how many tactics that unit can use.")
            elif key == "who":
                box.pinned = (filters.WHO_ANY, filters.WHO_SIGNATURE)
                box.setToolTip("Deck rules for tactics cards:\n"
                               f"• {filters.WHO_ANY} – no restriction\n"
                               f"• {filters.WHO_SIGNATURE} – needs the one unit with the same signature tag\n"
                               f"• {filters.WHO_TRAIT.format('<trait>')} – needs a unit with that trait in "
                               "your deck (e.g. a Londo Bell unit for Londo Bell cards)")
            self.boxes[key] = box
            lay.addWidget(box)

        self.ranges: dict[str, RangeBox] = {}
        for stat in config.CARD_TYPES[card_type].stats:
            rb = RangeBox(filters.STAT_LABELS[stat])
            rb.changed.connect(self.changed)
            self.ranges[stat] = rb
            lay.addWidget(rb)

        self.selected_only = QCheckBox("Show currently selected only")
        self.selected_only.toggled.connect(self.changed)
        lay.addWidget(self.selected_only)
        lay.addStretch()

    def set_units(self, units: list[scraper.Card]):
        """Tactics tab: the unit cards offered in "Usable by" (call before set_cards)."""
        self._units = filters.unit_labels(units)

    def set_cards(self, cards: list[scraper.Card]):
        for key, box in self.boxes.items():
            if key == "usable":
                opts = Counter({label: sum(filters.usable_by(c, u) for c in cards)
                                for label, u in self._units.items()})
                box.set_options(opts)
                box.setVisible(bool(opts))
                continue
            opts = filters.options(cards, key)
            box.set_options(opts)
            box.setVisible(bool(opts))
        for stat, rb in self.ranges.items():
            rb.set_bounds(filters.stat_bounds(cards, stat))

    def state(self, text: str) -> filters.FilterState:
        return filters.FilterState(
            chosen={k: b.values() for k, b in self.boxes.items() if b.values() and k != "usable"},
            usable_by=[self._units[v] for v in (self.boxes["usable"].values() if "usable" in self.boxes else ())
                       if v in self._units],
            ranges={s: v for s, rb in self.ranges.items() if not rb.isHidden() and (v := rb.value())},
            text=text)

    def reset(self):
        widgets = [*self.boxes.values(), *self.ranges.values(), self.selected_only]
        for w in widgets:
            w.blockSignals(True)
        for b in self.boxes.values():
            b.clear()
        for rb in self.ranges.values():
            rb.reset()
        self.selected_only.setChecked(False)
        for w in widgets:
            w.blockSignals(False)
        self.reset_requested.emit()  # lets the tab clear its search box too
        self.changed.emit()


class CardTab(QWidget):
    selection_changed = Signal()
    show_enhanced_toggled = Signal(bool)

    def __init__(self, card_type: str, parent=None):
        super().__init__(parent)
        self.card_type = card_type
        self.changes: dict[str, dict] = {}  # code -> Bandai change info, for the ↻ Updated markers
        self.cards: list[scraper.Card] = []
        self.filters = FilterPanel(card_type)  # placed in the main window's left column
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search anything: name, code, trait, weapon, ability text…")
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 1)
        self.btn_sel = QPushButton("Select shown")
        self.btn_unsel = QPushButton("Unselect shown")
        self.btn_clear = QPushButton("Clear all")
        for b in (self.btn_sel, self.btn_unsel, self.btn_clear):
            bar.addWidget(b)
        self.btn_enh = QPushButton("✦ Show enhanced")
        self.btn_enh.setCheckable(True)
        self.btn_enh.toggled.connect(self.show_enhanced_toggled)
        bar.addWidget(self.btn_enh)
        lay.addLayout(bar)
        self.stack = QStackedWidget()
        self.empty = QLabel("No card list yet.\nClick “Update cards” to download it.")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.no_match = QLabel("No cards match these filters.")
        self.no_match.setObjectName("empty")
        self.no_match.setAlignment(Qt.AlignCenter)
        self.grid = CardGrid(card_type)
        for w in (self.empty, self.grid, self.no_match):
            self.stack.addWidget(w)
        lay.addWidget(self.stack, 1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self.apply_filter)
        self.search.textChanged.connect(self._debounce.start)
        self.filters.changed.connect(self.apply_filter)
        self.filters.reset_requested.connect(self._clear_search)
        self.btn_sel.clicked.connect(lambda: self._bulk(True))
        self.btn_unsel.clicked.connect(lambda: self._bulk(False))
        self.btn_clear.clicked.connect(self._clear)
        self.grid.selection_changed.connect(self._on_selection)

    def set_cards(self, cards: list[scraper.Card]):
        keep = self.grid.selection()
        self.cards = cards
        self.grid.set_cards(cards, keep)
        self.filters.set_cards(cards)
        self.apply_filter()

    def apply_filter(self):
        state = self.filters.state(self.search.text())
        sel_only = self.filters.selected_only.isChecked()

        def pred(tile: CardTile) -> bool:
            if sel_only and not tile.selected:
                return False
            return filters.matches(tile.card, state, tile.haystack)

        self.grid.set_predicate(pred)
        shown = len(self.grid.visible_tiles())
        self.filters.count.setText(f"Showing {shown} of {len(self.cards)}")
        if not self.cards:
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(self.grid if shown else self.no_match)

    def _clear_search(self):
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)

    def _on_selection(self):
        if self.filters.selected_only.isChecked():
            self.apply_filter()
        self.selection_changed.emit()

    def _bulk(self, on: bool):
        for t in self.grid.visible_tiles():
            t.set_selected(on)

    def _clear(self):
        for t in self.grid.tiles:
            t.set_selected(False)


# ---- main window ----------------------------------------------------------------
class MainWindow(AiMixin, QMainWindow):
    def __init__(self, home: str | None = None):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} {__version__}")
        self.resize(1320, 820)
        self.cache = service.open_cache(home, log=lambda m: None)
        self.task: Task | None = None
        self.prefs_path = self.cache.meta_dir / "gui.json"
        self.prefs = self._load_prefs()
        self.images: dict[str, Path | None] = {}
        self.store = service.open_store(self.cache)
        self.tools = enhance.Tools(self.cache.meta_dir)
        self.enhanced: dict[str, Path] = {}          # code -> enhanced master
        self.enh_thumbs: dict[str, Path | None] = {}
        self._after_task = None                      # run once the current task ends
        self._ai_task = False                        # current task changes enhanced images

        self.card_tabs = {t: CardTab(t) for t in config.CARD_TYPES}
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body_host = QWidget()
        body = QHBoxLayout(body_host)
        outer.addWidget(body_host, 1)
        self.banner = QPushButton()
        self.banner.setObjectName("banner")
        self.banner.setCursor(Qt.PointingHandCursor)
        self.banner.hide()
        self.banner.clicked.connect(self._banner_clicked)
        outer.addWidget(self.banner)
        self._new_on_site: list[str] = []
        self._app_update: tuple[str, str] | None = None  # (version, release page) if newer exists
        self._checker: Task | None = None
        body.addWidget(self._build_sidebar(), 0)
        self.tabs = QTabWidget()
        for t, tab in self.card_tabs.items():
            self.tabs.addTab(tab, config.CARD_TYPES[t].label)
            tab.selection_changed.connect(self._update_summary)
            tab.show_enhanced_toggled.connect(self._set_show_enhanced)
            tab.grid.compare_requested.connect(self.compare_card)
        self.tabs.currentChanged.connect(self._tab_changed)
        body.addWidget(self.tabs, 1)
        self._build_menus()

        sb = self.statusBar()
        self.status = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(260)
        self.progress.hide()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.hide()
        self.btn_cancel.clicked.connect(lambda: self.task and self.task.cancel())
        sb.addWidget(self.status, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.btn_cancel)

        self.reload_cards()
        QTimer.singleShot(0, self._first_run_prompt)

    # -- layout --
    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(330)
        v = QVBoxLayout(side)
        v.setContentsMargins(4, 4, 8, 4)
        self.filter_stack = QStackedWidget()
        for tab in self.card_tabs.values():
            self.filter_stack.addWidget(tab.filters)
        v.addWidget(self.filter_stack, 1)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        lists = QHBoxLayout()  # deck lists: shared by every tab, like the selection summary
        self.btn_import = QPushButton("Import list…")
        self.btn_import.setToolTip("Select the cards from a deck list someone shared  (Ctrl+Shift+V)")
        self.btn_import.clicked.connect(self.paste_deck_code)
        self.btn_export = QPushButton("Export list")
        self.btn_export.setToolTip("Copy the selected cards (all tabs) to the clipboard as a deck list "
                                   "to share  (Ctrl+Shift+C)")
        self.btn_export.clicked.connect(self.copy_deck_code)
        lists.addWidget(self.btn_import)
        lists.addWidget(self.btn_export)
        v.addLayout(lists)
        self.btn_print = QPushButton("Start printing…")
        self.btn_print.setObjectName("accent")
        self.btn_print.setToolTip("Choose print settings with a preview, then save the PDF(s)  (Ctrl+P)")
        self.btn_print.clicked.connect(self.start_printing)
        v.addWidget(self.btn_print)
        return side

    def _build_menus(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        self.act_print = file_menu.addAction("Start &printing…", self.start_printing)
        self.act_print.setShortcut(QKeySequence.Print)
        file_menu.addAction("Open &output folder", self._open_output)
        file_menu.addSeparator()
        act_quit = file_menu.addAction("&Quit", self.close)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.setMenuRole(QAction.QuitRole)

        cards = mb.addMenu("&Cards")
        self.act_update = cards.addAction("⟳  &Update cards", self.run_update)
        self.act_update.setShortcut(QKeySequence.Refresh)
        self.act_update.setStatusTip("Full update: card lists, every card's details (finds cards Bandai "
                                     "changed) and any new images")
        self.act_cal = cards.addAction("&Calibration sheet…", lambda: self.run_calibration())
        self.act_cal.setStatusTip("A 2-page front/back alignment test for the current tab's card size")
        cards.addSeparator()
        act = cards.addAction("&Import deck list…", self.paste_deck_code)
        act.setShortcut(QKeySequence("Ctrl+Shift+V"))
        act.setStatusTip("Select the cards from a deck list someone shared")
        self.act_export = cards.addAction("&Export deck list", self.copy_deck_code)
        self.act_export.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.act_export.setStatusTip("Copy the selected cards as text (e.g. \"1x ST01-U02\") to share")
        cards.addSeparator()
        cards.addAction("Check for &new cards now", lambda: self.check_site(manual=True))
        self.act_auto = cards.addAction("Check for new cards and app updates at &startup")
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(bool(self.prefs.get("auto_check", True)))
        self.act_auto.setStatusTip("Once a day, look at the card lists on the site for new cards")
        self.act_auto.toggled.connect(self._toggle_auto)
        cards.addSeparator()
        cards.addAction("Open cards &folder", lambda: open_in_file_manager(self.cache.images_dir))

        self._build_ai_menu(mb)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction("Check for app &updates", lambda: self.check_site(manual=True))
        help_menu.addAction("Open &project page", lambda: __import__("webbrowser").open(config.REPO_URL))
        help_menu.addSeparator()
        help_menu.addAction("&About", self._about)

    # -- "cards waiting" banner --
    def _toggle_auto(self, on: bool):
        self.prefs["auto_check"] = on
        self._save_prefs()

    def _changed_printed(self) -> list[str]:
        """Printed cards Bandai changed since they were printed, not yet looked at."""
        manifest = self.cache.load_manifest()
        ack = self.prefs.get("changes_ack", "")
        changes = (manifest or {}).get("changes", {})
        return [c for c in service.changed_since_printed(manifest, service.load_printed(self.cache))
                if changes[c].get("date", "") > ack]

    def _banner_clicked(self):
        if self._changed_printed():
            self.show_changed()
        elif self._pending_app_update():
            self.open_app_update()
        else:
            self.run_update()

    def _pending_app_update(self) -> tuple[str, str] | None:
        u = self._app_update
        return u if u and u[0] != self.prefs.get("skip_app_version") else None

    def open_app_update(self):
        """Open the release page; the banner then stops mentioning this version."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        version, url = self._app_update
        QDesktopServices.openUrl(QUrl(url))
        self.prefs["skip_app_version"] = version
        self._save_prefs()
        self.status.setText(f"Opened the download page for version {version}. Download the zip and "
                            "unzip it over this folder (your cards and settings are kept).")
        self.refresh_banner()

    def show_changed(self):
        """Banner click: select the printed cards Bandai changed, ready for reprinting."""
        printed_changed = set(self._changed_printed())
        manifest = self.cache.load_manifest() or {}
        self.prefs["changes_ack"] = max((ch.get("date", "") for ch in manifest.get("changes", {}).values()),
                                        default="")
        self._save_prefs()
        for tab in self.card_tabs.values():
            for t in tab.grid.tiles:
                if t.card.code in printed_changed:
                    t.set_selected(True)
        for i, (t, tab) in enumerate(self.card_tabs.items()):
            first = next((tile for tile in tab.grid.tiles if tile.card.code in printed_changed), None)
            if first is not None:
                self.tabs.setCurrentIndex(i)
                if not first.isVisible():  # hidden by a filter: show everything so it can be seen
                    tab.filters.reset()
                tab.grid.ensureWidgetVisible(first)
                break
        self.status.setText(f"{len(printed_changed)} updated card{'s' * (len(printed_changed) != 1)} you'd "
                            "printed before are selected – Start printing to make new copies")
        self.refresh_banner()

    def refresh_banner(self):
        changed = self._changed_printed()
        if changed:
            n = len(changed)
            labels = [c.upper() for c in changed[:3]]
            self.banner.setText(f"Bandai updated {n} card{'s' * (n != 1)} you've printed "
                                f"({', '.join(labels)}{', …' if n > 3 else ''})  –  click to see {'them' if n > 1 else 'it'}")
            self.banner.show()
            return
        update = self._pending_app_update()
        if update:
            self.banner.setText(f"Version {update[0]} of {APP_TITLE} is available (you have {__version__})"
                                "  –  click to open the download page")
            self.banner.show()
            return
        missing = [c for c in self._all_cards() if self.images.get(c.code) is None]
        parts = []
        if self._new_on_site:
            n = len(self._new_on_site)
            sample = ", ".join(self._new_on_site[:3]) + (", …" if n > 3 else "")
            parts.append(f"{n} new card{'s' * (n != 1)} on the site ({sample})")
        if missing:
            n = len(missing)
            parts.append(f"{n} card{'s' * (n != 1)} not downloaded yet")
        if parts and self.cache.load_manifest():
            self.banner.setText("  ·  ".join(parts) + "  –  click to update cards")
            self.banner.show()
        else:
            self.banner.hide()

    def _all_cards(self) -> list[scraper.Card]:
        return [c for tab in self.card_tabs.values() for c in tab.cards]

    def _auto_check(self):
        import time
        if not self.prefs.get("auto_check", True) or not self.cache.load_manifest():
            return
        if time.time() - float(self.prefs.get("last_check", 0)) < CHECK_INTERVAL_S:
            return
        self.check_site()

    def check_site(self, manual: bool = False):
        """Look for new cards on the site in the background; the window stays usable."""
        import time
        if self._checker is not None or self.task is not None:
            return
        manifest = self.cache.load_manifest()
        root = self.cache.root

        def work(task: Task):
            from .cache import Cache
            cache = Cache(root, log=lambda m: None)  # own session
            app_update = service.latest_release()   # never raises
            try:
                new = service.new_on_site(cache, manifest)
            except Exception:  # noqa: BLE001 - still report an app update if the card site is down
                if app_update is None:
                    raise
                new = []
            return new, app_update

        def done(result):
            new, self._app_update = result
            self._new_on_site = new
            self.prefs["last_check"] = time.time()
            self._save_prefs()
            self.refresh_banner()
            if manual:
                msg = (f"{len(new)} new card{'s' * (len(new) != 1)} on the site" if new
                       else "You have every card on the site")
                if self._app_update:
                    msg += f" · version {self._app_update[0]} of the app is available"
                self.status.setText(msg)

        def failed(msg):
            if manual:
                self.status.setText(f"Couldn't check for new cards: {msg}")

        def ended():
            self._checker = None

        if manual:
            self.status.setText("Checking the site for new cards…")
        self._checker = Task(work, self)
        self._checker.finished_ok.connect(done)
        self._checker.failed.connect(failed)
        self._checker.finished.connect(ended)
        self._checker.start()

    def _open_output(self):
        out = self.cache.root / "output"
        out.mkdir(parents=True, exist_ok=True)
        open_in_file_manager(out)

    def _about(self):
        QMessageBox.about(self, f"About {APP_TITLE}",
                          f"<b>{APP_TITLE}</b> {__version__}<br><br>"
                          "Lays out replacement cards for miniatures you own onto printable sheets.<br><br>"
                          "Unofficial fan tool, not affiliated with the publisher. Card images are "
                          "downloaded from the publisher's site to your computer for personal use; "
                          "please don't share or sell them.<br><br>License: GPL-3.0-or-later<br>"
                          f"Source and downloads: <a href='{config.REPO_URL}'>{config.REPO}</a><br><br>"
                          "<b>Optional AI enhancement uses</b> (downloaded only if you enable it):<br>"
                          + enhance.CREDITS_HTML)

    @property
    def current_type(self) -> str:
        return list(self.card_tabs)[self.tabs.currentIndex()]

    def _tab_changed(self, idx: int):
        self.filter_stack.setCurrentIndex(idx)
        HoverPreview.get().hide()
        self._update_summary()

    def _selection(self, card_type: str) -> list[tuple[scraper.Card, int]]:
        tab = self.card_tabs[card_type]
        sel = tab.grid.selection()
        return [(c, sel[c.code]) for c in tab.cards if c.code in sel]

    # -- deck codes --
    def copy_deck_code(self):
        sel = [(c.code, q) for t in self.card_tabs for c, q in self._selection(t)]
        if not sel:
            self.status.setText("Nothing selected to export")
            return
        text = deckcode.format_code(sel)
        QApplication.clipboard().setText(text)
        n = sum(q for _, q in sel)
        self.status.setText(f"Deck list copied ({n} card{'s' * (n != 1)})")
        lines = text.splitlines()
        shown = "\n".join(lines[:15]) + (f"\n… and {len(lines) - 15} more" if len(lines) > 15 else "")
        box = QMessageBox(QMessageBox.Information, "Deck list copied",
                          f"<b>Copied {n} card{'s' * (n != 1)} to the clipboard.</b><br><br>"
                          "Paste it anywhere – Discord, a message, a note – to share it. Others can load it "
                          "with <b>Import list…</b>.", QMessageBox.Ok, self)
        box.setInformativeText(f"<pre>{html.escape(shown)}</pre>")
        box.exec()

    def paste_deck_code(self):
        known = {c.code for c in self._all_cards()}
        dlg = PasteDeckDialog(self, known)
        if dlg.exec() != PasteDeckDialog.Accepted or not dlg.result_cards:
            return
        self.apply_deck(dlg.result_cards)

    def apply_deck(self, cards: dict[str, int]):
        """Replace the selection on every tab with {code: copies}."""
        for tab in self.card_tabs.values():
            for t in tab.grid.tiles:
                q = cards.get(t.card.code)
                if q:
                    t.qty.blockSignals(True)
                    t.qty.setValue(q)
                    t.qty.blockSignals(False)
                t.set_selected(bool(q))
        n = sum(cards.values())
        self.status.setText(f"Selected {n} card{'s' * (n != 1)} from the deck list")
        self._update_summary()

    def settings(self, card_type: str | None = None) -> service.PrintSettings:
        s = settings_from_prefs(self.prefs, card_type or self.current_type)
        s.enhanced = bool(self.prefs.get("ai_enabled")) and bool(self.enhanced)
        return s

    def _update_summary(self, *_):
        t = self.current_type
        ct = config.CARD_TYPES[t]
        self.act_cal.setText(f"&Calibration sheet ({ct.label.lower()})…")
        lines, total = [], 0
        for kind in self.card_tabs:
            sel = self._selection(kind)
            copies = sum(q for _, q in sel)
            if not copies:
                continue
            total += copies
            try:
                sheets = service.sheets_needed(self.settings(kind), copies)
                lines.append(f"{config.CARD_TYPES[kind].label}: {copies} "
                             f"({len(sel)} different) → {sheets} sheet{'s' * (sheets != 1)}")
            except ValueError as exc:
                lines.append(f"{kind}: {exc}")
        self.summary.setText("<b>Selected</b><br>" + "<br>".join(lines) if lines
                             else "Nothing selected yet")
        self.btn_print.setEnabled(total > 0 and self.task is None)
        self.act_print.setEnabled(total > 0 and self.task is None)
        self.btn_export.setEnabled(total > 0 and self.task is None)
        self.act_export.setEnabled(total > 0 and self.task is None)
        self.btn_import.setEnabled(self.task is None)

    # -- prefs --
    def _load_prefs(self) -> dict:
        try:
            p = json.loads(self.prefs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            p = {}
        return {**DEFAULT_PREFS, **{k: v for k, v in p.items() if k in DEFAULT_PREFS}}

    def _save_prefs(self):
        try:
            atomic_write(self.prefs_path, json.dumps(self.prefs, indent=1).encode())
        except OSError:
            pass  # preferences are a convenience; never block the user over them

    # -- data --
    def reload_cards(self):
        manifest = self.cache.load_manifest() or {}
        self.images = {}
        self.card_tabs["tactics"].filters.set_units(scraper.cards_from_manifest(manifest, "unit"))
        changes = manifest.get("changes", {})
        for i, (t, tab) in enumerate(self.card_tabs.items()):
            cards = scraper.cards_from_manifest(manifest, t)
            tab.changes = {c.code: changes[c.code] for c in cards if c.code in changes}
            tab.set_cards(cards)
            for tile in tab.grid.tiles:
                tile.set_changed(tab.changes.get(tile.card.code))
            self.images.update({c.code: service.local_image(self.cache, c) for c in cards})
            tab.apply_filter()
            self.tabs.setTabText(i, f"{config.CARD_TYPES[t].label} ({len(cards)})")
        self.cache.save_index()
        self.refresh_enhanced()
        self._update_summary()
        self.refresh_banner()

    # -- enhanced images in the grid --
    def refresh_enhanced(self):
        """Re-read which cards have enhanced copies and redraw the tiles."""
        self.enhanced, self.enh_thumbs = {}, {}
        for c in self._all_cards():
            path = self.store.path(c)
            if path:
                self.enhanced[c.code] = path
                self.enh_thumbs[c.code] = self.store.thumb(c)
        have = bool(self.enhanced)
        show = have and bool(self.prefs.get("show_enhanced"))
        for tab in self.card_tabs.values():
            tab.btn_enh.blockSignals(True)
            tab.btn_enh.setChecked(show)
            tab.btn_enh.blockSignals(False)
            tab.btn_enh.setEnabled(have)
            tab.btn_enh.setToolTip("Show the AI-enhanced copies in the grid and hover preview" if have else
                                   "No AI-enhanced images yet (Enhance menu)")
        self._apply_images()
        if hasattr(self, "act_ai"):
            self._ai_sync_menu()

    def _apply_images(self):
        show = bool(self.prefs.get("show_enhanced")) and bool(self.enhanced)
        for tab in self.card_tabs.values():
            tab.grid.set_images(self.images, self.enhanced, self.enh_thumbs, show)

    def _set_show_enhanced(self, on: bool):
        self.prefs["show_enhanced"] = on
        self._save_prefs()
        for tab in self.card_tabs.values():  # keep every tab's button in step
            tab.btn_enh.blockSignals(True)
            tab.btn_enh.setChecked(on)
            tab.btn_enh.blockSignals(False)
        HoverPreview.get().hide()
        self._apply_images()

    def _first_run_prompt(self):
        if self.cache.load_manifest():
            return
        r = QMessageBox.question(self, APP_TITLE, "No cards downloaded yet.\n\n"
                                 "Download the card list, card details and images now? (about 5 MB)")
        if r == QMessageBox.Yes:
            self.run_update()

    # -- tasks --
    def _start(self, fn, on_done, label: str, cancellable: bool = False):
        if self.task:
            return
        self.task = Task(fn, self)
        self.task.stepped.connect(self._on_step)
        self.task.logged.connect(self.status.setText)
        self.task.finished_ok.connect(on_done)
        self.task.failed.connect(self._on_failed)
        self.task.finished.connect(self._on_task_end)
        for b in (self.act_update, self.act_print, self.act_cal, self.btn_print, self.menu_ai,
                  self.btn_import, self.btn_export, self.act_export):
            b.setEnabled(False)
        self.tabs.setEnabled(False)
        self.filter_stack.setEnabled(False)
        self.status.setText(label)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.btn_cancel.setVisible(cancellable)
        self.task.start()

    def _on_step(self, label, status, done, total):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status.setText(f"[{done}/{total}] {label}  {status}")

    def _on_failed(self, msg):
        QMessageBox.critical(self, APP_TITLE, msg)

    def _on_task_end(self):
        self.task = None
        self.progress.hide()
        self.btn_cancel.hide()
        self.tabs.setEnabled(True)
        self.filter_stack.setEnabled(True)
        self.act_update.setEnabled(True)
        self.act_cal.setEnabled(True)
        self.menu_ai.setEnabled(True)
        if self._ai_task:
            self._ai_task = False
            self.refresh_enhanced()
        self._update_summary()
        nxt, self._after_task = self._after_task, None
        if nxt:
            QTimer.singleShot(0, nxt)

    def run_update(self):
        if self._checker is not None:  # let a background check finish first (it shares nothing, but
            self._checker.wait(15000)  # its result would be stale once the update runs)
        self.banner.hide()
        cache = self.cache

        def work(task: Task):
            task.log("Downloading card lists and every card's details…")
            cache.stats = dict.fromkeys(cache.stats, 0)
            _, missing, changed = service.full_update(cache, log=task.log, on_step=task.step,
                                                      should_stop=lambda: task.cancelled)
            return dict(cache.stats), missing, task.cancelled, changed

        def done(result):
            stats, missing, cancelled, changed = result
            if not cancelled:
                self._new_on_site = []  # the update just pulled the full lists
                self._after_task = self.auto_enhance_new
            self.reload_cards()
            msg = (f"{'Stopped. ' if cancelled else ''}{stats['downloaded']} downloaded, "
                   f"{stats['cached']} already had, {stats['not_modified']} unchanged")
            if missing:
                msg += f", {len(missing)} unavailable ({', '.join(missing)})"
            if changed:
                msg += f" · Bandai updated {len(changed)} card{'s' * (len(changed) != 1)}"
            self.status.setText(msg)

        self._start(work, done, "Updating…", cancellable=True)

    def _ask_output(self, title: str, default: Path, note: str = "") -> Path | None:
        default.parent.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, title + note, str(default), "PDF (*.pdf)")
        if not path:
            return None
        return Path(path) if path.lower().endswith(".pdf") else Path(path + ".pdf")

    def start_printing(self):
        selections = {t: sel for t in self.card_tabs if (sel := self._selection(t))}
        if not selections:
            return
        # enhanced copies print only while AI enhancement is switched on in the Enhance menu
        dlg = PrintDialog(self, selections, self.images, self.prefs,
                          enhanced=self.enhanced if self.prefs.get("ai_enabled") else None)
        dlg.calibration_requested.connect(lambda: self._calibration_from(dlg))
        accepted = dlg.exec()
        self.prefs.update(dlg.result_prefs())  # keep non-print prefs (startup check etc.)
        self._save_prefs()
        self._update_summary()
        if accepted:
            self.run_build(selections)

    def _calibration_from(self, dlg: PrintDialog):
        self.prefs.update(dlg.result_prefs())
        self.run_calibration(card_type=dlg.which.currentData() or self.current_type, parent=dlg)

    def run_build(self, selections: dict[str, list] | None = None):
        selections = selections or {t: sel for t in self.card_tabs if (sel := self._selection(t))}
        if not selections:
            return
        types = list(selections)
        default = service.default_output(self.cache.root / "output", types)
        note = (" (one file per card type: _" + " / _".join(service.TYPE_LABEL.get(t, t) for t in types) + ")"
                if len(types) > 1 else "")
        base = self._ask_output("Save cards PDF", default, note)
        if not base:
            return
        outputs = service.output_paths(base, types)
        settings = {t: self.settings(t) for t in types}
        cache, store = self.cache, self.store

        def work(task: Task):
            cards = [c for sel in selections.values() for c, _ in sel]
            found, missing = service.fetch_images(cache, cards, offline=False, on_step=task.step)
            written = []
            for t in types:
                task.log(f"Laying out {config.CARD_TYPES[t].label.lower()}…")
                if any(c.code in found for c, _ in selections[t]):
                    written += service.make_pdf(cache, selections[t], found, settings[t], outputs[t],
                                                log=task.log, store=store)
            if not written:
                raise RuntimeError("none of the selected cards have an image")
            return written, missing

        def done(result):
            written, missing = result
            self.status.setText("Wrote " + ", ".join(p.name for p in written))
            self.reload_images()
            self._report_written(written, missing, settings[types[0]])

        self._start(work, done, "Building PDF…")

    def reload_images(self):
        for tab in self.card_tabs.values():
            self.images.update({c.code: service.local_image(self.cache, c) for c in tab.cards})
        self._apply_images()
        self.cache.save_index()
        self.refresh_banner()

    def run_calibration(self, card_type: str | None = None, parent=None):
        s = self.settings(card_type)
        default = service.default_output(self.cache.root / "output", [s.card_type],
                                         suffix=f"calibration_{service.TYPE_LABEL.get(s.card_type, s.card_type)}")
        out = self._ask_output("Save calibration sheet", default)
        if not out:
            return
        self._start(lambda task: [service.make_calibration(s, out)],
                    lambda written: self._report_written(written, [], s, calibration=True, parent=parent),
                    "Building calibration sheet…")

    def _report_written(self, written, missing, s, calibration=False, parent=None):
        lines = ["Saved:\n" + "\n".join(f"  {p}" for p in written)]
        if missing:
            lines.append("Skipped (no image): " + ", ".join(missing))
        if calibration:
            lines.append("Print it the same way you'll print cards, then hold it up to a light.")
        elif s.mode == "split":
            lines.append("Print the fronts file, put the stack back in flipped, then print the backs file.\n"
                         "Or print each on its own sheet and glue them back to back.")
        lines.append("Print at 100% / Actual size (not “fit to page”).")
        box = QMessageBox(parent or self)
        box.setWindowTitle(APP_TITLE)
        box.setText("\n\n".join(lines))
        open_btn = box.addButton("Open folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            open_in_file_manager(written[0].parent)

    def closeEvent(self, e):  # noqa: N802 - Qt API
        HoverPreview.get().hide()
        if self.task:
            self.task.cancel()
            self.task.wait(30000 if self._ai_task else 5000)  # an AI pass finishes its current card
        if self._checker:
            self._checker.wait(5000)
        self._save_prefs()
        super().closeEvent(e)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    home = None
    if "--home" in argv:
        i = argv.index("--home")
        home = argv[i + 1] if i + 1 < len(argv) else None
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(app_icon())
    apply_theme(app)
    win = MainWindow(home)
    win.show()
    QTimer.singleShot(1500, win._auto_check)  # background check for new cards (at most daily)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

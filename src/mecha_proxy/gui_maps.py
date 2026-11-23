"""Maps tab: Bandai's printable maps in your paper size, opened as published."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from . import config, maps

THUMB_W = 260


class MapRow(QFrame):
    open_requested = Signal(str)  # map title

    def __init__(self, m: maps.Map, paper: str, pdf: Path | None, thumb: Path | None):
        # thumb None + pdf present: the preview is still being made in the background
        super().__init__()
        self.map = m
        self.setObjectName("tile")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 8, 8, 8)
        pic = QLabel()
        pic.setFixedWidth(THUMB_W)
        pix = QPixmap(str(thumb)) if thumb else QPixmap()
        if not pix.isNull():
            pic.setPixmap(pix.scaledToWidth(THUMB_W, Qt.SmoothTransformation))
        else:
            pic.setText("Making preview…" if pdf is not None else "No preview yet")
            pic.setAlignment(Qt.AlignCenter)
            pic.setObjectName("muted")
            pic.setFixedHeight(70)
        h.addWidget(pic)
        info = QVBoxLayout()
        info.addWidget(QLabel(f"<b style='font-size:15px'>{html.escape(m.display)}</b>"))
        f = m.file(paper)
        if f is None:
            status = f"Not offered in {config.paper_label(paper)} size"
        elif pdf is None:
            status = (f"Not downloaded in {config.paper_label(paper)} size yet – Open downloads it"
                      + (f" ({f.size})" if f.size else ""))
        else:
            status = f"{config.paper_label(paper)} · updated {f.updated or '?'}" + (f" · {f.size}" if f.size else "")
        st = QLabel(status)
        st.setObjectName("muted")
        st.setWordWrap(True)
        info.addWidget(st)
        info.addStretch()
        btn = QPushButton("Open")
        btn.setToolTip("Open the map PDF to print (as published by Bandai)")
        btn.setEnabled(f is not None)
        btn.clicked.connect(lambda: self.open_requested.emit(m.title))
        row = QHBoxLayout()
        row.addWidget(btn)
        row.addStretch()
        info.addLayout(row)
        h.addLayout(info, 1)
        self._openable = f is not None
        if self._openable:
            self.setCursor(Qt.PointingHandCursor)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if self._openable:
            self.open_requested.emit(self.map.title)


class MapsTab(QWidget):
    open_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[MapRow] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        intro = QLabel("Bandai's printable maps. Each one is a set of tiles you print and put together; it opens "
                       "exactly as Bandai published it, in your paper size.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        v.addWidget(intro)
        self.empty = QLabel("No maps yet – use Cards → Update cards (with Maps ticked) to download them.")
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

    def set_items(self, items: list[tuple[maps.Map, str, Path | None, Path | None]]):
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for m, paper, pdf, thumb in items:
            row = MapRow(m, paper, pdf, thumb)
            row.setParent(self.host)  # never a stray top-level window
            row.open_requested.connect(self.open_requested)
            self.list.insertWidget(self.list.count() - 1, row)
            self.rows.append(row)
        self.empty.setVisible(not self.rows)
        self.scroll.setVisible(bool(self.rows))


class MapsPanel(QWidget):
    """Sidebar for the Maps tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addWidget(QLabel("<b>Maps</b>"))
        self.paper = QComboBox()
        for k, (label, _) in config.PAPER_SIZES.items():
            self.paper.addItem(label, k)
        self.paper.setToolTip("Same setting as Paper in the print dialog. Maps come in both sizes; this picks which.")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Paper", self.paper)
        v.addLayout(form)
        note = QLabel("Print every page at <b>100% / Actual size</b> (turn off \"Fit to page\"). The tiles run to "
                      "the edge of the page: use borderless printing if your printer has it, otherwise trim and "
                      "line them up along the printed edges.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        v.addStretch()


class MapsMixin:
    """Expects MainWindow attributes: cache, prefs, tabs, filter_stack, status, _start(), _save_prefs()."""

    def _build_maps(self):
        self.maps_tab = MapsTab()
        self.maps_panel = MapsPanel()
        self.maps_tab.open_requested.connect(self.open_map)
        self.maps_index = self.tabs.addTab(self.maps_tab, "Maps")
        self.filter_stack.addWidget(self.maps_panel)
        self._maps_sync_paper()
        self.maps_panel.paper.currentIndexChanged.connect(self._maps_paper_changed)

    def _maps_sync_paper(self):
        combo = self.maps_panel.paper
        combo.blockSignals(True)
        combo.setCurrentIndex(max(combo.findData(self.prefs.get("paper", config.DEFAULT_PAPER)), 0))
        combo.blockSignals(False)

    def _maps_paper_changed(self, _=None):
        self.prefs["paper"] = self.maps_panel.paper.currentData()
        self._save_prefs()
        if hasattr(self, "_scen_sync_paper"):
            self._scen_sync_paper()      # keeps every paper selector in step
        if hasattr(self, "_update_summary"):
            self._update_summary()
        self.reload_maps()

    def reload_maps(self):
        """Fill the Maps tab. Never renders previews here (a map preview takes seconds): missing
        ones are made by a background task, which reloads the tab when it's done."""
        paper = self.prefs.get("paper", config.DEFAULT_PAPER)
        items, missing = [], False
        for m in maps.saved_list(self.cache):
            pdf = maps.local_pdf(self.cache, m.title, paper)
            thumb = maps.thumb_ready(self.cache, pdf) if pdf is not None else None
            missing |= pdf is not None and thumb is None
            items.append((m, paper, pdf, thumb))
        self.maps_tab.set_items(items)
        n = len(items)
        self.tabs.setTabText(self.maps_index, f"Maps ({n})" if n else "Maps")
        if missing:
            self._make_map_previews(paper)

    def _make_map_previews(self, paper: str):
        from .gui import Task
        if getattr(self, "_map_thumber", None) is not None:
            return
        cache = self.cache

        def finished():
            self._map_thumber = None
            if self.prefs.get("paper", config.DEFAULT_PAPER) == paper:
                self.reload_maps()
        self._map_thumber = Task(lambda task: maps.make_missing_thumbs(cache, paper, lambda: task.cancelled), self)
        self._map_thumber.finished.connect(finished)
        self._map_thumber.start()

    def open_map(self, title: str):
        from .gui import open_in_file_manager
        paper = self.prefs.get("paper", config.DEFAULT_PAPER)
        have = maps.local_pdf(self.cache, title, paper)
        if have is not None:
            open_in_file_manager(have)
            return

        def done(result):
            path, note = result
            self.status.setText(note or f"Opened {path.name}")
            self.reload_maps()
            open_in_file_manager(path)
        self._start(lambda task: maps.get(self.cache, title, paper, log=task.log), done, "Getting the map…")

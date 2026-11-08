"""Reusable Qt widgets: flow layout, type-ahead chip box, range box, hover preview."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImageReader, QPixmap
from PySide6.QtWidgets import (QCompleter, QHBoxLayout, QLabel, QLayout, QLineEdit,
                               QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)


class FlowLayout(QLayout):
    """Lays children out left-to-right, wrapping to new lines (for chips)."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test=False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do_layout(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._spacing, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class Chip(QPushButton):
    def __init__(self, text: str, color: str, parent=None):
        super().__init__(f"{text}  ✕", parent)
        self.value = text
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to remove")
        self.setStyleSheet(f"QPushButton {{ background:{color}; border:none; border-radius:10px;"
                           f" padding:3px 9px; color:white; font-size:12px; }}"
                           f"QPushButton:hover {{ background:#e04a52; }}")


class ChipBox(QWidget):
    """Type to search a category's values; picking one adds a removable chip above the box.

    The ▾ button lists every value so you can scroll instead of typing.
    """

    changed = Signal()

    def __init__(self, title: str, hint: str = "", color: str = "#5a5d66", parent=None):
        super().__init__(parent)
        self.color = color
        self._options: Counter = Counter()
        self._chosen: list[str] = []
        self.pinned: tuple[str, ...] = ()  # values listed first, before the alphabetical rest
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QLabel(title + (f"  <span style='color:#80868b'>{hint}</span>" if hint else ""))
        head.setTextFormat(Qt.RichText)
        lay.addWidget(head)
        self.chip_host = QWidget()
        self.chips = FlowLayout(self.chip_host)
        lay.addWidget(self.chip_host)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Type to search…")
        self.drop = QToolButton()
        self.drop.setText("▾")
        self.drop.setToolTip("Show all")
        row.addWidget(self.edit, 1)
        row.addWidget(self.drop)
        lay.addLayout(row)

        self.model = QStringListModel()
        self.completer = QCompleter(self.model, self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setMaxVisibleItems(12)
        self.edit.setCompleter(self.completer)
        self.completer.activated.connect(self._picked)
        self.edit.returnPressed.connect(self._enter)
        self.drop.clicked.connect(self._show_all)

    # display text "Asticassia  (3)" <-> value "Asticassia"
    @staticmethod
    def _value_of(display: str) -> str:
        return display.rsplit("  (", 1)[0]

    def set_options(self, counts: Counter):
        self._options = Counter(counts)
        self._chosen = [v for v in self._chosen if v in self._options]
        self._refresh()

    def _refresh(self):
        items = [f"{v}  ({n})" for v, n in sorted(self._options.items(),
                                                  key=lambda kv: (kv[0] not in self.pinned, kv[0].lower()))
                 if v not in self._chosen]
        self.model.setStringList(items)
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        for v in self._chosen:
            chip = Chip(v, self.color)
            chip.clicked.connect(lambda _=False, val=v: self.remove(val))
            self.chips.addWidget(chip)
        self.chip_host.setVisible(bool(self._chosen))
        self.setEnabled(bool(self._options))

    def add(self, value: str):
        if value in self._options and value not in self._chosen:
            self._chosen.append(value)
            self._refresh()
            self.changed.emit()

    def remove(self, value: str):
        if value in self._chosen:
            self._chosen.remove(value)
            self._refresh()
            self.changed.emit()

    def clear(self):
        if self._chosen:
            self._chosen = []
            self._refresh()
            self.changed.emit()

    def values(self) -> set[str]:
        return set(self._chosen)

    def _picked(self, display: str):
        self.add(self._value_of(display))
        # the completer writes the picked text into the box after this signal; clear afterwards
        QTimer.singleShot(0, self.edit.clear)

    def _enter(self):
        text = self.edit.text().strip().lower()
        hits = [v for v in self._options if v not in self._chosen and text and text in v.lower()]
        exact = [v for v in hits if v.lower() == text]
        if exact or len(hits) == 1:
            self.add((exact or hits)[0])
            self.edit.clear()

    def _show_all(self):
        self.edit.setFocus()
        self.completer.setCompletionPrefix("")
        self.completer.complete()


class RangeBox(QWidget):
    """Min/max spin boxes for a numeric stat."""

    changed = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(title)
        self.label.setMinimumWidth(90)
        self.lo, self.hi = QSpinBox(), QSpinBox()
        lay.addWidget(self.label)
        lay.addWidget(self.lo)
        lay.addWidget(QLabel("to"))
        lay.addWidget(self.hi)
        lay.addStretch()
        self._bounds = (0, 0)
        self.lo.valueChanged.connect(self._lo_changed)
        self.hi.valueChanged.connect(self._hi_changed)

    def set_bounds(self, bounds: tuple[int, int] | None):
        self.setVisible(bounds is not None)
        if bounds is None:
            return
        old_full = self.value() is None
        self._bounds = bounds
        for sp in (self.lo, self.hi):
            sp.blockSignals(True)
            sp.setRange(*bounds)
        if old_full:
            self.lo.setValue(bounds[0])
            self.hi.setValue(bounds[1])
        for sp in (self.lo, self.hi):
            sp.blockSignals(False)

    def _lo_changed(self, v):
        if v > self.hi.value():
            self.hi.setValue(v)
        self.changed.emit()

    def _hi_changed(self, v):
        if v < self.lo.value():
            self.lo.setValue(v)
        self.changed.emit()

    def value(self) -> tuple[int, int] | None:
        """None when the full range is selected (no filtering)."""
        v = (self.lo.value(), self.hi.value())
        return None if v == self._bounds else v

    def reset(self):
        self.lo.setValue(self._bounds[0])
        self.hi.setValue(self._bounds[1])


class HoverPreview(QLabel):
    """Large, readable card image that follows the mouse while hovering a thumbnail."""

    _instance = None

    @classmethod
    def get(cls) -> "HoverPreview":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background:#111; border:2px solid #e04a52; padding:4px;")
        self._cache: dict[tuple, QPixmap] = {}

    def show_for(self, path: Path, global_pos: QPoint, max_width: int):
        """Show the card at most max_width px wide, never enlarged past the source image,
        and never more than 45% of the screen width / 85% of its height."""
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        key = (str(path), max_width, avail.width(), avail.height())
        pix = self._cache.get(key)
        if pix is None:
            reader = QImageReader(str(path))
            size = reader.size()
            if size.isValid() and size.width() > 0:
                scale = min(max_width / size.width(), avail.width() * 0.45 / size.width(),
                            avail.height() * 0.85 / size.height(), 1.0)
                reader.setScaledSize(QSize(round(size.width() * scale), round(size.height() * scale)))
            img = reader.read()
            if img.isNull():
                return
            pix = self._cache[key] = QPixmap.fromImage(img)
        self.setPixmap(pix)
        self.adjustSize()
        # to the right of the cursor if it fits, otherwise to the left; keep on screen
        x = global_pos.x() + 24
        if x + self.width() > avail.right():
            x = global_pos.x() - 24 - self.width()
        y = min(max(global_pos.y() - self.height() // 2, avail.top()), avail.bottom() - self.height())
        self.move(max(x, avail.left()), y)
        self.show()
        self.raise_()

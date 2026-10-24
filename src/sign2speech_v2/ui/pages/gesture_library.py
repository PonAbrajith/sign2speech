from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem

from sign2speech_v2.ui.pages.base import Page
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.widgets import Card, label, set_text

COLUMNS = ("Gesture", "Pattern", "Sentence", "Emotion / Meaning", "Default Style", "Enabled", "Built-in Demo")


class GestureLibraryPage(Page):
    title = "Gesture Library"

    def __init__(self, controller, notify, open_builder: Callable[[], None] | None = None,
                 confirm: Callable[[str], bool] | None = None) -> None:
        super().__init__(controller, notify)
        self._open_builder = open_builder or (lambda: None)
        self._confirm = confirm or self._ask
        card = Card("STORED GESTURES")
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(320)
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.itemDoubleClicked.connect(lambda _: self._edit())
        self.conflicts = label("", color=theme.ERROR, bold=True)
        self.conflicts.setWordWrap(True)
        card.body.addWidget(self.conflicts)
        card.body.addWidget(self.table)
        row = QHBoxLayout()
        self.buttons = {}
        for text, fn in (("New", self._new), ("Edit", self._edit), ("Delete", self._delete),
                         ("Enable", lambda: self._enable(True)), ("Disable", lambda: self._enable(False))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
            self.buttons[text] = b
        self.buttons["Delete"].setObjectName("danger")
        row.addStretch(1)
        card.body.addLayout(row)
        note = label("Selecting a row only chooses what to edit. Deleting a gesture never changes calibration, "
                     "hardware settings, live sensor data or its sentence. Built-in demo gestures are prototype "
                     "trigger patterns for this glove, not official ISL handshapes.", "muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        self.layout_.addWidget(card)
        self.layout_.addStretch(1)
        self._key = None

    def _ask(self, text: str) -> bool:
        return QMessageBox.question(self, "Sign2Speech V2", text) == QMessageBox.Yes

    def _selected_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(0x0100)

    def _selected(self) -> None:
        self.c.select_gesture(self._selected_id())

    def _new(self) -> None:
        self.act(self.c.new_gesture())
        self._open_builder()

    def _edit(self) -> None:
        gid = self._selected_id()
        if gid is not None:
            self.act(self.c.edit_gesture(gid))
            self._open_builder()

    def _delete(self) -> None:
        gid = self._selected_id()
        if gid is None:
            return
        g = self.c.repo.get(gid)
        if g and self._confirm(f"Delete gesture {g.name}? Recorded sample files are kept."):
            self.act(self.c.delete_gesture(gid))

    def _enable(self, on: bool) -> None:
        gid = self._selected_id()
        if gid is not None:
            self.act(self.c.set_gesture_enabled(gid, on))

    def refresh(self) -> None:
        gestures = self.c.store.gestures
        set_text(self.conflicts, "\n".join(f"AMBIGUOUS: {a} and {b} can match the same hand shape - disable or "
                                            "change one of them." for a, b in self.c.store.conflicts))
        key = tuple((g.id, g.name, g.finger_pattern.compact(), g.enabled, g.speech_style, g.builtin_demo,
                     g.sentence_id, getattr(self.c.sentence_for(g), "sentence", None))
                    for g in gestures)
        has = self._selected_id() is not None
        for name in ("Edit", "Delete", "Enable", "Disable"):
            self.buttons[name].setEnabled(has)
        if key == self._key:
            return
        self._key = key
        selected = self.c.store.selected_id
        self.table.blockSignals(True)
        self.table.setRowCount(len(gestures))
        for r, g in enumerate(gestures):
            s = self.c.sentence_for(g)
            for col, v in enumerate((g.name, str(g.finger_pattern), s.sentence if s else f"({g.display_text})",
                                     f"{s.icon} {s.emotion}".strip() if s else "",
                                     g.speech_style, "yes" if g.enabled else "no",
                                     "Built-in Demo" if g.builtin_demo else "User")):
                item = QTableWidgetItem(v)
                if col == 0:
                    item.setData(0x0100, g.id)
                self.table.setItem(r, col, item)
            if g.id == selected:
                self.table.selectRow(r)
        self.table.blockSignals(False)

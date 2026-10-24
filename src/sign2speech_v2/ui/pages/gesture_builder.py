from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QPushButton, QWidget)

from sign2speech_v2.config.pins import FINGER_NAMES
from sign2speech_v2.core.patterns import PatternToken
from sign2speech_v2.gestures.repository import CATEGORIES, SENTENCE_ROLES, SPEECH_STYLES
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.pages.base import Page
from sign2speech_v2.ui.widgets import Card, label, pattern_row, set_color, set_text

TOKENS = (("O  open", PatternToken.OPEN), ("B  bent", PatternToken.BENT), ("X  any", PatternToken.ANY))


class GestureBuilderPage(Page):
    title = "Gesture Builder"

    def __init__(self, *a) -> None:
        super().__init__(*a)
        top = QHBoxLayout()
        self.btn_new = QPushButton("+ NEW GESTURE")
        self.btn_new.setObjectName("primary")
        self.btn_new.clicked.connect(self._new)
        top.addWidget(self.btn_new)
        top.addWidget(label("Editor target:", "muted"))
        self.target = QComboBox()
        self.target.setMinimumWidth(260)
        self.target.activated.connect(self._target_chosen)
        top.addWidget(self.target)
        self.editor_title = label("", color=theme.STORED, size=12, bold=True)
        top.addWidget(self.editor_title, 1)
        self.layout_.addLayout(top)
        note = label("The editor target is only what you are editing. It never changes LIVE DETECTION, which "
                     "always comes from the current glove data.", "muted")
        note.setWordWrap(True)
        self.layout_.addWidget(note)

        live = Card("LIVE GLOVE  (current hand)")
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        self.live_states = []
        for i, name in enumerate(FINGER_NAMES):
            grid.addWidget(label(name, "muted"), 0, i)
            s = label("—", size=13, bold=True)
            grid.addWidget(s, 1, i)
            self.live_states.append(s)
        live.body.addWidget(grid_w)
        w1, self.live_pattern = pattern_row("LIVE PATTERN:", theme.LIVE)
        w2, self.live_detection = pattern_row("LIVE DETECTION:", theme.DETECT)
        live.body.addWidget(w1)
        live.body.addWidget(w2)
        self.btn_capture = QPushButton("CAPTURE FROM LIVE GLOVE")
        self.btn_capture.setObjectName("live")
        self.btn_capture.clicked.connect(self._capture)
        live.body.addWidget(self.btn_capture)
        self.layout_.addWidget(live)

        ed = Card("GESTURE EDITOR  (stored definition)")
        pat_w = QWidget()
        pat = QGridLayout(pat_w)
        pat.setContentsMargins(0, 0, 0, 0)
        self.token_boxes = []
        for i, name in enumerate(FINGER_NAMES):
            pat.addWidget(label(name, "muted"), 0, i)
            box = QComboBox()
            for text, tok in TOKENS:
                box.addItem(text, tok)
            box.setCurrentIndex(-1)
            box.activated.connect(lambda idx, i=i, box=box: self._token(i, box))
            pat.addWidget(box, 1, i)
            self.token_boxes.append(box)
        w3, self.editor_pattern = pattern_row("GESTURE PATTERN:", theme.STORED)
        ed.body.addWidget(w3)
        ed.body.addWidget(pat_w)

        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. HELLO  (A-Z, 0-9, _)")
        self.display = QLineEdit()
        self.display.setPlaceholderText("Spoken by the laptop, e.g. Hello")
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("How the ISL sign is made")
        self.description.setFixedHeight(70)
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        self.style = QComboBox()
        self.style.addItems(SPEECH_STYLES)
        self.role = QComboBox()
        self.role.addItems(SENTENCE_ROLES)
        self.enabled = QCheckBox("Enabled (used by live detection)")
        form.addRow("Gesture name", self.name)
        form.addRow("Display text", self.display)
        form.addRow("ISL description", self.description)
        form.addRow("Category", self.category)
        form.addRow("Speech style", self.style)
        form.addRow("Sentence role", self.role)
        self.sentence_box = QComboBox()
        self.sentence_box.activated.connect(
            lambda _: setattr(self.c.editor, "sentence_id", self.sentence_box.currentData()))
        form.addRow("Spoken sentence", self.sentence_box)
        form.addRow("", self.enabled)
        ed.body.addLayout(form)
        style_note = label("Speech style is chosen by you per gesture; the system does not detect emotion.", "muted")
        ed.body.addWidget(style_note)
        self.name.textEdited.connect(lambda t: setattr(self.c.editor, "name", t))
        self.display.textEdited.connect(lambda t: setattr(self.c.editor, "display_text", t))
        self.description.textChanged.connect(
            lambda: setattr(self.c.editor, "description", self.description.toPlainText()))
        self.category.activated.connect(lambda _: setattr(self.c.editor, "category", self.category.currentText()))
        self.style.activated.connect(lambda _: setattr(self.c.editor, "speech_style", self.style.currentText()))
        self.role.activated.connect(lambda _: setattr(self.c.editor, "sentence_role", self.role.currentText()))
        self.enabled.clicked.connect(lambda v: setattr(self.c.editor, "enabled", bool(v)))

        row = QHBoxLayout()
        self.btn_save = QPushButton("SAVE GESTURE")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save)
        row.addWidget(self.btn_save)
        row.addStretch(1)
        self.btn_rec = QPushButton("Record samples (optional)")
        self.btn_rec.clicked.connect(self._record)
        row.addWidget(self.btn_rec)
        self.samples = label("", "muted")
        row.addWidget(self.samples)
        ed.body.addLayout(row)
        self.message = label("", size=10)
        self.message.setWordWrap(True)
        ed.body.addWidget(self.message)
        self.layout_.addWidget(ed)
        self.layout_.addStretch(1)
        self._targets_key = None
        self._load_editor()

    # ---- actions ------------------------------------------------------------
    def _new(self) -> None:
        self.act(self.c.new_gesture())
        self._load_editor()

    def _target_chosen(self, index: int) -> None:
        gid = self.target.itemData(index)
        self.act(self.c.new_gesture() if gid is None else self.c.edit_gesture(gid))
        self._load_editor()

    def _capture(self) -> None:
        self.act(self.c.capture_from_live())

    def _token(self, finger: int, box: QComboBox) -> None:
        self.c.set_editor_token(finger, box.currentData())

    def _save(self) -> None:
        self.c.editor.description = self.description.toPlainText()
        self.act(self.c.save_editor())
        self._load_editor()

    def _record(self) -> None:
        self.act(self.c.stop_recording() if self.c.editor.recording else self.c.start_recording())

    # ---- sync ------------------------------------------------------------------
    def _load_editor(self) -> None:
        """Editor state -> widgets (after new/edit/save)."""
        e = self.c.editor
        self.name.setText(e.name)
        self.display.setText(e.display_text)
        self.description.blockSignals(True)
        self.description.setPlainText(e.description)
        self.description.blockSignals(False)
        self.category.setCurrentText(e.category)
        self.style.setCurrentText(e.speech_style)
        self.role.setCurrentText(e.sentence_role)
        self.enabled.setChecked(e.enabled)
        self._sentences_key = None
        self._targets_key = None
        self.refresh()

    def _refresh_sentences(self) -> None:
        key = (tuple((s.id, s.sentence, s.style) for s in self.c.sentences), self.c.editor.sentence_id)
        if key == getattr(self, "_sentences_key", None):
            return
        self._sentences_key = key
        self.sentence_box.blockSignals(True)
        self.sentence_box.clear()
        self.sentence_box.addItem("(none - speak the display text)", None)
        for s in self.c.sentences:
            self.sentence_box.addItem(f"{s.icon} {s.sentence}  [{s.name}, {s.style}]".strip(), s.id)
        self.sentence_box.setCurrentIndex(max(0, self.sentence_box.findData(self.c.editor.sentence_id)))
        self.sentence_box.blockSignals(False)

    def _refresh_targets(self) -> None:
        gestures = self.c.store.gestures
        key = (tuple((g.id, g.name) for g in gestures), self.c.editor.current_id)
        if key == self._targets_key:
            return
        self._targets_key = key
        self.target.blockSignals(True)
        self.target.clear()
        self.target.addItem("New gesture (unsaved)", None)
        for g in gestures:
            self.target.addItem(f"{g.name}  —  {g.finger_pattern}" + ("  [BUILT-IN DEMO]" if g.builtin_demo else ""),
                                g.id)
        idx = self.target.findData(self.c.editor.current_id) if self.c.editor.current_id is not None else 0
        self.target.setCurrentIndex(max(0, idx))
        self.target.blockSignals(False)

    def refresh(self) -> None:
        c = self.c
        ls, d, e = c.live_sensor, c.detection, c.editor
        self._refresh_targets()
        self._refresh_sentences()
        set_text(self.editor_title, e.title)
        states = ls.finger_states if ls.live else None
        for i, lab in enumerate(self.live_states):
            word = states[i].word if states else "—"
            set_text(lab, word)
            set_color(lab, theme.LIVE if word == "OPEN" else theme.DETECT if word == "BENT" else theme.MUTED, 13, True)
        set_text(self.live_pattern, str(d.live_pattern) if d.live_pattern and ls.live else "—")
        set_text(self.live_detection, d.display if ls.live else ls.link)
        self.btn_capture.setEnabled(ls.live and d.live_pattern is not None)

        set_text(self.editor_pattern, str(e.pattern) if e.pattern else "(not captured)")
        for i, box in enumerate(self.token_boxes):
            want = -1 if e.pattern is None else box.findData(e.pattern.tokens[i])
            if box.currentIndex() != want:
                box.setCurrentIndex(want)
        self.btn_rec.setText("STOP & SAVE SAMPLES" if e.recording else "Record samples (optional)")
        set_text(self.samples, f"{e.samples_recorded} samples" + (" (recording)" if e.recording else ""))
        set_text(self.message, e.message)

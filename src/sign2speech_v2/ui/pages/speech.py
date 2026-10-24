from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTableWidget,
                               QTableWidgetItem)

from sign2speech_v2.controller import Result
from sign2speech_v2.gestures.repository import SPEECH_STYLES
from sign2speech_v2.speech.engine import STYLE_VOICES, create_engine
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.pages.base import Page
from sign2speech_v2.ui.widgets import Card, label, set_color, set_text

TRIGGERS = (("Studio live detection (recommended)", "studio"), ("ESP32 '# GESTURE:' lines (on-device)", "esp32"))
SENTENCE_COLUMNS = ("Name", "Sentence", "Style", "Enabled", "Type", "Emotion / meaning")
ENGINES = (("Auto (pyttsx3 if installed, else Windows SAPI)", "auto"), ("pyttsx3", "pyttsx3"),
           ("Windows SAPI (System.Speech)", "sapi"), ("None", "none"))


class SpeechPage(Page):
    title = "Speech"

    def __init__(self, *a) -> None:
        super().__init__(*a)
        st = Card("LAPTOP SPEECH  (built-in speaker, offline, no cloud)")
        self.engine = label("", size=12, bold=True)
        self.state = label("", "muted")
        st.body.addWidget(self.engine)
        st.body.addWidget(self.state)
        form = QFormLayout()
        self.enabled = QCheckBox("Speak accepted gestures")
        self.enabled.setChecked(self.c.settings.speech_enabled)
        self.enabled.clicked.connect(self._enabled)
        self.trigger = QComboBox()
        for text, key in TRIGGERS:
            self.trigger.addItem(text, key)
        self.trigger.setCurrentIndex(max(0, self.trigger.findData(self.c.settings.speech_trigger)))
        self.trigger.activated.connect(self._trigger)
        self.engine_box = QComboBox()
        for text, key in ENGINES:
            self.engine_box.addItem(text, key)
        self.engine_box.setCurrentIndex(max(0, self.engine_box.findData(self.c.settings.speech_engine)))
        self.engine_box.activated.connect(self._engine)
        voice_row = QHBoxLayout()
        self.voice = QComboBox()
        self.voice.addItem(self.c.settings.voice or "(system default)", self.c.settings.voice)
        self.voice.activated.connect(self._voice)
        load = QPushButton("Load voices")
        load.clicked.connect(self._load_voices)
        voice_row.addWidget(self.voice, 1)
        voice_row.addWidget(load)
        form.addRow("", self.enabled)
        form.addRow("Speak when", self.trigger)
        form.addRow("Engine", self.engine_box)
        form.addRow("Voice", voice_row)
        st.body.addLayout(form)
        self.layout_.addWidget(st)

        builtin = Card("BUILT-IN SENTENCES")
        self.builtin_list = QListWidget()
        self.builtin_list.setMinimumHeight(540)   # all ten entries visible
        self.builtin_list.setStyleSheet("font-size: 11pt;")
        self.builtin_list.itemClicked.connect(self._builtin_clicked)
        builtin.body.addWidget(self.builtin_list)
        builtin.body.addWidget(label("Click a sentence to load it into the editor.", "muted"))
        self.layout_.addWidget(builtin)
        self._builtin_key = None

        ed = Card("SENTENCE  (a speech item - separate from gestures)")
        form2 = QFormLayout()
        self.s_name = QLineEdit()
        self.s_name.setPlaceholderText("optional, e.g. WATER_REQUEST (derived from the sentence if empty)")
        self.s_text = QLineEdit()
        self.s_text.setPlaceholderText("e.g. I need some water.")
        self.s_style = QComboBox()
        self.s_style.addItems(SPEECH_STYLES)
        self.s_name.textEdited.connect(lambda t: setattr(self.c.sentence_editor, "name", t))
        self.s_text.textEdited.connect(lambda t: setattr(self.c.sentence_editor, "sentence", t))
        # changing the style changes ONLY the style, never the sentence text
        self.s_style.activated.connect(
            lambda _: setattr(self.c.sentence_editor, "style", self.s_style.currentText()))
        self.s_emotion = QLineEdit()
        self.s_emotion.setPlaceholderText("optional metadata, e.g. Need / request")
        self.s_meaning = QLineEdit()
        self.s_meaning.setPlaceholderText("optional, e.g. Need / request")
        self.s_icon = QLineEdit()
        self.s_icon.setMaximumWidth(80)
        # editing the emotion / meaning never changes the sentence text or the speech style
        self.s_emotion.textEdited.connect(lambda t: setattr(self.c.sentence_editor, "emotion", t))
        self.s_meaning.textEdited.connect(lambda t: setattr(self.c.sentence_editor, "meaning", t))
        self.s_icon.textEdited.connect(lambda t: setattr(self.c.sentence_editor, "icon", t))
        form2.addRow("Name", self.s_name)
        form2.addRow("SENTENCE", self.s_text)
        form2.addRow("EMOTION / MEANING", self.s_emotion)
        form2.addRow("Meaning", self.s_meaning)
        form2.addRow("Icon", self.s_icon)
        form2.addRow("SPEECH STYLE", self.s_style)
        ed.body.addLayout(form2)
        sug = QHBoxLayout()
        self.suggestion = label("", "muted")
        self.btn_suggest = QPushButton("Use suggested style")
        self.btn_suggest.setToolTip("Only a suggestion - you can pick any speech style.")
        self.btn_suggest.clicked.connect(self._use_suggestion)
        sug.addWidget(self.suggestion)
        sug.addWidget(self.btn_suggest)
        sug.addStretch(1)
        ed.body.addLayout(sug)
        emo_note = label("Emotion describes the meaning/context of the sentence. Speech style controls how the "
                         "sentence is spoken.", "muted")
        emo_note.setWordWrap(True)
        ed.body.addWidget(emo_note)
        row = QHBoxLayout()
        self.btn_save_s = QPushButton("SAVE SENTENCE")
        self.btn_save_s.setObjectName("primary")
        self.btn_speak_s = QPushButton("SPEAK")
        self.btn_speak_s.setObjectName("live")
        self.btn_clear_s = QPushButton("CLEAR")
        self.btn_save_s.clicked.connect(lambda: self._sentence_action(self.c.save_sentence()))
        self.btn_speak_s.clicked.connect(lambda: self.act(self.c.speak_sentence()))
        self.btn_clear_s.clicked.connect(lambda: self._sentence_action(self.c.clear_sentence()))
        for btn in (self.btn_save_s, self.btn_speak_s, self.btn_clear_s):
            row.addWidget(btn)
        row.addStretch(1)
        ed.body.addLayout(row)
        self.s_message = label("", "muted")
        ed.body.addWidget(self.s_message)
        self.last = label("", size=11)
        ed.body.addWidget(self.last)
        style_note = label("Selectable expressive speech styles: the same sentence, a different voice delivery "
                           "(rate, pitch, volume). You choose the style - the system does not detect emotion.",
                           "muted")
        style_note.setWordWrap(True)
        ed.body.addWidget(style_note)
        self.layout_.addWidget(ed)

        lib = Card("SAVED SENTENCES")
        self.s_table = QTableWidget(0, len(SENTENCE_COLUMNS))
        self.s_table.setHorizontalHeaderLabels(SENTENCE_COLUMNS)
        self.s_table.verticalHeader().setVisible(False)
        self.s_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.s_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.s_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.s_table.horizontalHeader().setStretchLastSection(True)
        self.s_table.setMinimumHeight(200)
        self.s_table.itemSelectionChanged.connect(self._sentence_selected)
        lib.body.addWidget(self.s_table)
        row2 = QHBoxLayout()
        self.btn_del_s = QPushButton("Delete")
        self.btn_del_s.setObjectName("danger")
        self.btn_del_s.clicked.connect(self._delete_sentence)
        self.btn_toggle_s = QPushButton("Enable / Disable")
        self.btn_toggle_s.clicked.connect(self._toggle_sentence)
        row2.addWidget(self.btn_del_s)
        row2.addWidget(self.btn_toggle_s)
        row2.addStretch(1)
        lib.body.addLayout(row2)
        self.layout_.addWidget(lib)
        self._s_key = None
        self._load_sentence_editor()

        styles = Card("SELECTABLE EXPRESSIVE SPEECH STYLES")
        note = label("Selectable expressive speech styles are voice presets chosen manually (per gesture in the "
                     "Gesture Builder, per sentence above). The system does not detect or understand the user's "
                     "emotional state.", "muted")
        note.setWordWrap(True)
        styles.body.addWidget(note)
        tbl = QTableWidget(len(STYLE_VOICES), 4)
        tbl.setHorizontalHeaderLabels(("Style", "Rate (-10..10)", "Pitch (SSML)", "Volume"))
        tbl.verticalHeader().setVisible(False)
        tbl.setMinimumHeight(250)
        for r, (name, sv) in enumerate(STYLE_VOICES.items()):
            for col, v in enumerate((name, f"{sv.rate:+d}", sv.pitch_text, f"{sv.volume}%")):
                tbl.setItem(r, col, QTableWidgetItem(v))
        styles.body.addWidget(tbl)
        self.layout_.addWidget(styles)
        self.layout_.addStretch(1)

    # ---- sentences ----------------------------------------------------------
    def _sentence_action(self, result: Result) -> None:
        self.act(result)
        self._load_sentence_editor()

    def _load_sentence_editor(self) -> None:
        """Sentence editor state -> widgets."""
        e = self.c.sentence_editor
        self.s_name.setText(e.name)
        self.s_text.setText(e.sentence)
        self.s_style.setCurrentText(e.style)
        self.s_emotion.setText(e.emotion)
        self.s_meaning.setText(e.meaning)
        self.s_icon.setText(e.icon)

    def _use_suggestion(self) -> None:
        self.act(self.c.apply_suggested_style())
        self._load_sentence_editor()

    def _builtin_clicked(self, item) -> None:
        self.notify(self.c.load_sentence(item.data(0x0100)))
        self._load_sentence_editor()

    def _refresh_builtin(self) -> None:
        # in the library's defined order (water, hunger, happiness, sadness, anger, fear)
        items = sorted((x for x in self.c.sentences if x.builtin_demo and x.emotion), key=lambda x: x.id)
        key = tuple((x.id, x.icon, x.sentence, x.emotion) for x in items)
        if key == self._builtin_key:
            return
        self._builtin_key = key
        self.builtin_list.clear()
        for x in items:
            it = QListWidgetItem(f"{x.icon}  {x.sentence}\n      Emotion: {x.emotion}")
            it.setData(0x0100, x.id)
            self.builtin_list.addItem(it)

    def _selected_sentence_id(self) -> int | None:
        rows = self.s_table.selectionModel().selectedRows()
        return self.s_table.item(rows[0].row(), 0).data(0x0100) if rows else None

    def _sentence_selected(self) -> None:
        sid = self._selected_sentence_id()
        if sid is not None:
            self.notify(self.c.load_sentence(sid))
            self._load_sentence_editor()

    def _delete_sentence(self) -> None:
        sid = self._selected_sentence_id()
        if sid is not None:
            self.act(self.c.delete_sentence(sid))

    def _toggle_sentence(self) -> None:
        sid = self._selected_sentence_id()
        s = self.c.sentence_repo.get(sid) if sid is not None else None
        if s is not None:
            self.act(self.c.set_sentence_enabled(s.id, not s.enabled))

    def _refresh_sentences(self) -> None:
        items = self.c.sentences
        key = tuple((x.id, x.name, x.sentence, x.style, x.enabled, x.builtin_demo, x.emotion, x.icon)
                    for x in items)
        has = self._selected_sentence_id() is not None
        self.btn_del_s.setEnabled(has)
        self.btn_toggle_s.setEnabled(has)
        if key == self._s_key:
            return
        self._s_key = key
        current = self.c.sentence_editor.current_id
        self.s_table.blockSignals(True)
        self.s_table.clearSelection()
        self.s_table.setRowCount(len(items))
        for r, x in enumerate(items):
            for col, v in enumerate((x.name, x.sentence, x.style, "yes" if x.enabled else "no",
                                     "BUILT-IN SPEECH DEMO" if x.builtin_demo else "User",
                                     f"{x.icon} {x.emotion}".strip())):
                item = QTableWidgetItem(v)
                if col == 0:
                    item.setData(0x0100, x.id)
                self.s_table.setItem(r, col, item)
            if x.id == current:
                self.s_table.selectRow(r)
        self.s_table.blockSignals(False)

    def _enabled(self, on: bool) -> None:
        self.c.settings.speech_enabled = bool(on)
        self.c.speech_state.enabled = bool(on)
        self.c.save_settings()

    def _trigger(self, _i: int) -> None:
        self.c.settings.speech_trigger = self.trigger.currentData()
        self.c.save_settings()

    def _engine(self, _i: int) -> None:
        self.c.settings.speech_engine = self.engine_box.currentData()
        self.c.speech.set_engine(create_engine(self.c.settings.speech_engine, self.c.settings.voice))
        self.c.save_settings()

    def _voice(self, _i: int) -> None:
        self.c.settings.voice = self.voice.currentData() or ""
        self.c.speech.engine.set_voice(self.c.settings.voice)
        self.c.save_settings()

    def _load_voices(self) -> None:
        voices = self.c.speech.engine.voices()
        self.voice.clear()
        self.voice.addItem("(system default)", "")
        for v in voices:
            self.voice.addItem(v, v)
        self.voice.setCurrentIndex(max(0, self.voice.findData(self.c.settings.voice)))
        self.notify(Result(bool(voices), f"{len(voices)} installed voices" if voices else "No voices reported"))

    def refresh(self) -> None:
        sp = self.c.speech_state
        set_text(self.engine, f"{sp.engine_name} — " + ("AVAILABLE" if sp.available else "UNAVAILABLE"))
        set_color(self.engine, theme.LIVE if sp.available else theme.ERROR, 12, True)
        set_text(self.state, ("SPEAKING…" if sp.speaking else "idle") + f"  |  {sp.last_message}")
        last = f'"{sp.last_text}"' if sp.last_text else "-"
        set_text(self.last, f"Last spoken: {last}   Style: {sp.last_style or '-'}   "
                            f"(last gesture: {sp.last_spoken_gesture or '-'}; {sp.spoken_count} spoken)")
        set_text(self.s_message, self.c.sentence_editor.message)
        sugg = self.c.suggested_sentence_style()
        e = self.c.sentence_editor
        set_text(self.suggestion, f"Sentence emotion: {e.emotion}   Suggested style: {sugg} (suggestion only)"
                 if sugg else "")
        self.btn_suggest.setVisible(sugg is not None and sugg != e.style)
        self._refresh_builtin()
        self._refresh_sentences()

"""Sentence speech: `speech_sentences` table, sentence editor, style testing, UI (temporary DBs only)."""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from sign2speech_v2.speech.sentences import Sentence, SentenceError, SentenceRepository, name_from_sentence

from conftest import Rig

WATER = "I need some water."


def add(rig, style, name="WATER_REQUEST", text=WATER):
    e = rig.c.sentence_editor
    e.name, e.sentence, e.style = name, text, style
    res = rig.c.save_sentence()
    assert res.ok, res.message
    return e.current_id


# 1, 2
def test_create_and_save_sentence(rig):
    sid = add(rig, "Neutral")
    s = rig.c.sentence_repo.get(sid)
    assert (s.name, s.sentence, s.style, s.enabled) == ("WATER_REQUEST", WATER, "Neutral", True)
    assert s.created_at and s.updated_at
    assert [x.id for x in rig.c.sentences] == [sid]


def test_table_schema_is_separate_from_gestures(rig):
    con = sqlite3.connect(rig.ws.db_path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(speech_sentences)")]
    # order differs between a fresh table and a migrated one, so compare the set of columns
    assert set(cols) == {"id", "name", "sentence", "style", "enabled", "builtin_demo", "emotion", "icon",
                         "meaning", "created_at", "updated_at"}
    assert "sentence" not in [r[1] for r in con.execute("PRAGMA table_info(gestures)")]


# 3, 6, 7
def test_load_sentence_selects_its_style(rig):
    sad, happy = add(rig, "Sad"), add(rig, "Happy")
    rig.c.clear_sentence()
    assert rig.c.load_sentence(sad).ok
    assert (rig.c.sentence_editor.sentence, rig.c.sentence_editor.style) == (WATER, "Sad")
    assert rig.c.load_sentence(happy).ok
    assert (rig.c.sentence_editor.sentence, rig.c.sentence_editor.style) == (WATER, "Happy")


# 4
def test_sentences_persist_across_restart(tmp_path):
    r1 = Rig(tmp_path)
    add(r1, "Sad")
    add(r1, "Happy")
    r1.c.close()
    r2 = Rig(tmp_path)
    assert [(s.sentence, s.style) for s in r2.c.sentences] == [(WATER, "Happy"), (WATER, "Sad")]
    r2.c.close()


# 5
def test_same_sentence_in_three_styles_are_three_records(rig):
    ids = [add(rig, st) for st in ("Neutral", "Happy", "Sad")]
    assert len(set(ids)) == 3
    assert sorted(s.style for s in rig.c.sentences) == ["Happy", "Neutral", "Sad"]


def test_exact_duplicate_is_refused_not_duplicated(rig):
    add(rig, "Sad")
    e = rig.c.sentence_editor
    e.name, e.sentence, e.style = "WATER_REQUEST", WATER, "Sad"
    res = rig.c.save_sentence()
    assert not res.ok and "already saved" in res.message
    assert len(rig.c.sentences) == 1


# 8
def test_changing_style_then_saving_keeps_the_original(rig):
    sad = add(rig, "Sad")
    rig.c.load_sentence(sad)
    rig.c.sentence_editor.style = "Happy"                 # only the style changes
    assert rig.c.sentence_editor.sentence == WATER
    assert rig.c.save_sentence().ok                         # a NEW record
    assert rig.c.sentence_repo.get(sad).style == "Sad"      # the Sad record is untouched
    assert sorted(s.style for s in rig.c.sentences) == ["Happy", "Sad"]


# 9
def test_deleting_sad_keeps_happy(rig):
    sad, happy = add(rig, "Sad"), add(rig, "Happy")
    assert rig.c.delete_sentence(sad).ok
    assert [s.id for s in rig.c.sentences] == [happy]
    assert rig.c.sentence_repo.get(happy).sentence == WATER


# 10, 11
def test_speak_uses_current_sentence_and_style(rig):
    add(rig, "Sad")
    e = rig.c.sentence_editor
    assert rig.c.speak_sentence().ok
    e.style = "Happy"
    assert rig.c.speak_sentence().ok
    e.sentence = "Please call my family."
    assert rig.c.speak_sentence().ok
    assert rig.engine.spoken == [(WATER, "Sad"), (WATER, "Happy"), ("Please call my family.", "Happy")]
    st = rig.c.speech_state
    assert (st.last_text, st.last_style) == ("Please call my family.", "Happy")
    assert st.last_spoken_gesture is None                  # manual speech is not a gesture


def test_speak_requires_text(rig):
    assert not rig.c.speak_sentence().ok and rig.engine.spoken == []


def test_validation_and_name_derivation():
    assert name_from_sentence(WATER) == "I_NEED_SOME_WATER"
    import tempfile
    from pathlib import Path
    repo = SentenceRepository(Path(tempfile.mkdtemp()) / "db.sqlite")
    assert repo.add(Sentence("", WATER)).name == "I_NEED_SOME_WATER"
    for bad in (Sentence("X", "   "), Sentence("X", WATER, style="Sarcastic"), Sentence("bad name!", WATER)):
        with pytest.raises(SentenceError):
            repo.add(bad)
    repo.close()


def test_enable_disable(rig):
    sid = add(rig, "Calm")
    rig.c.set_sentence_enabled(sid, False)
    assert rig.c.sentence_repo.get(sid).enabled is False
    rig.c.set_sentence_enabled(sid, True)
    assert rig.c.sentence_repo.get(sid).enabled is True


# 12, 13
def test_gestures_and_recognition_are_unaffected(calibrated_rig):
    rig = calibrated_rig
    rig.hold("OBOBO", 6)
    rig.c.capture_from_live()
    rig.c.editor.name, rig.c.editor.display_text = "WATER", "Water"
    rig.c.save_editor()
    gesture_before = rig.c.repo.by_name("WATER")
    live_before = dataclasses.asdict(rig.c.live_sensor)
    for st in ("Neutral", "Sad", "Happy"):
        add(rig, st)
    rig.c.speak_sentence()
    rig.c.delete_sentence(rig.c.sentences[0].id)
    assert rig.c.repo.by_name("WATER") == gesture_before        # gesture row unchanged
    assert [g.name for g in rig.c.repo.all()] == ["WATER"]
    assert dataclasses.asdict(rig.c.live_sensor) == live_before
    rig.hold("OOOOO", 30)  # neutral hand: arms gesture speech
    rig.hold("OBOBO", 12)
    assert rig.c.detection.display == "WATER"                    # recognition still works
    assert rig.engine.spoken[-1] == ("Water", "Neutral")         # gesture speaks its display text


# UI
def test_speech_page_sentence_workflow(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    QApplication.instance() or QApplication([])
    rig = Rig(tmp_path)
    win = MainWindow(rig.c, start_timers=False)
    win.nav.setCurrentRow(PAGE_ORDER.index("Speech"))
    page = win.pages["Speech"]
    page.s_name.setText("WATER_REQUEST")
    page.s_name.textEdited.emit("WATER_REQUEST")
    page.s_text.setText(WATER)
    page.s_text.textEdited.emit(WATER)
    for style in ("Sad", "Happy"):
        page.s_style.setCurrentText(style)
        page.s_style.activated.emit(page.s_style.currentIndex())
        page.btn_save_s.click()
    win.refresh()
    t = page.s_table
    rows = [[t.item(r, c).text() for c in range(4)] for r in range(t.rowCount())]
    assert rows == [["WATER_REQUEST", WATER, "Happy", "yes"], ["WATER_REQUEST", WATER, "Sad", "yes"]]
    page.btn_clear_s.click()
    assert page.s_text.text() == "" and page.s_style.currentText() == "Neutral"
    t.selectRow(1)                                               # Sad
    assert page.s_text.text() == WATER and page.s_style.currentText() == "Sad"
    page.btn_speak_s.click()
    t.selectRow(0)                                               # Happy
    assert page.s_style.currentText() == "Happy"
    page.btn_speak_s.click()
    assert rig.engine.spoken == [(WATER, "Sad"), (WATER, "Happy")]
    win.refresh()
    assert 'Last spoken: "I need some water."   Style: Happy' in page.last.text()
    t.selectRow(1)
    page.btn_del_s.click()                                       # delete Sad
    win.refresh()
    assert [t.item(r, 2).text() for r in range(t.rowCount())] == ["Happy"]
    win.close()
    rig.c.close()


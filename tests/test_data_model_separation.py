"""DATA MODEL: finger pattern -> GESTURE -> mapped SENTENCE -> sentence's emotion metadata; style per mapping.

The recognizer only maps the live finger pattern to a gesture. Emotion is metadata of the sentence: it is
never stored on a gesture, never a recognition key, and never selects the speech style.
"""

from __future__ import annotations

import dataclasses
import inspect
import sqlite3

import pytest

from sign2speech_v2.config.demo import BUILTIN_SENTENCES, PROTOTYPE_CATEGORY, PROTOTYPE_GESTURES
from sign2speech_v2.core.patterns import GesturePattern
from sign2speech_v2.gestures.repository import CATEGORIES, Gesture, GestureRepository
from sign2speech_v2.recognition import recognizer, stability

from conftest import Rig
from test_demo_mode import NEUTRAL, SPEC, adc, connect_armed


@pytest.fixture
def demo(tmp_path):
    rig = Rig(tmp_path, demo=True)
    yield rig
    rig.c.close()


def by_name(rig):
    return {g.name: g for g in rig.c.repo.all()}


def set_sentence_emotion(rig, sentence_id: int, emotion: str) -> None:
    con = sqlite3.connect(rig.ws.db_path)
    with con:
        con.execute("UPDATE speech_sentences SET emotion = ?, meaning = ? WHERE id = ?", (emotion, emotion, sentence_id))
    con.close()
    rig.c.reload_sentences()


# ------------------------------------------------------------------ schema / model
def test_gestures_carry_no_emotion(demo):
    con = sqlite3.connect(demo.ws.db_path)
    gesture_cols = {r[1] for r in con.execute("PRAGMA table_info(gestures)")}
    sentence_cols = {r[1] for r in con.execute("PRAGMA table_info(speech_sentences)")}
    con.close()
    assert not any("emotion" in c or "meaning" in c for c in gesture_cols)         # not on the gesture
    assert {"emotion", "meaning"} <= sentence_cols                                   # on the sentence
    assert not any("emotion" in f.name for f in dataclasses.fields(Gesture))
    assert "sentence_id" in gesture_cols and "speech_style" in gesture_cols        # gesture -> sentence, style


def test_builtin_table_is_pattern_gesture_sentence_style(demo):
    sentence_names = {e[0] for e in BUILTIN_SENTENCES}
    emotions = {e[2] for e in BUILTIN_SENTENCES}
    for name, pattern, display, sentence_name, style in PROTOTYPE_GESTURES:
        assert sentence_name in sentence_names
        assert not {name, pattern, display, sentence_name, style} & emotions        # no emotion in the row


def test_no_emotion_category_on_gestures(demo):
    assert "Emotion" not in CATEGORIES
    assert all(g.category == PROTOTYPE_CATEGORY != "Emotion" for g in demo.c.repo.all())


def test_recognition_code_never_sees_sentences_or_emotion():
    for module in (recognizer, stability):
        src = inspect.getsource(module).lower()
        assert "emotion" not in src and "sentence" not in src


# ------------------------------------------------------------------ runtime chain
def test_chain_pattern_gesture_sentence_emotion_style(demo):
    connect_armed(demo)
    demo.frames(adc("O O O X O"), 20)
    d = demo.c.detection
    water = by_name(demo)["WATER"]
    s = demo.c.sentence_for(water)
    assert d.matched_gesture == "WATER"                                              # pattern -> gesture
    assert water.sentence_id == s.id and d.matched_sentence == s.sentence            # gesture -> sentence
    assert d.matched_emotion == s.emotion == "Need / request"                        # sentence -> its metadata
    assert demo.engine.spoken == [(s.sentence, water.speech_style)]                  # only the sentence, mapping style
    shows = [c for c in demo.written() if c.startswith("SHOW ")]
    assert shows == ["SHOW WATER|Please give me some water."]                        # OLED: sentence only
    assert not any(e in shows[0] for e in ("Need", "request", "Urgent"))


def test_changing_a_sentence_emotion_changes_neither_recognition_nor_style(demo):
    water = by_name(demo)["WATER"]
    set_sentence_emotion(demo, water.sentence_id, "Happiness")                       # metadata edited
    connect_armed(demo)
    demo.frames(adc("O O O X O"), 20)
    d = demo.c.detection
    assert d.display == "WATER" and d.matched_emotion == "Happiness"                 # new metadata is shown
    assert demo.engine.spoken == [("Please give me some water.", "Urgent")]          # style NOT from emotion
    assert by_name(demo)["WATER"].speech_style == "Urgent"


def test_two_sentences_with_the_same_emotion_keep_their_own_styles(demo):
    g = by_name(demo)
    set_sentence_emotion(demo, g["HUNGRY"].sentence_id, "Illness / discomfort")      # same as NOT_WELL
    connect_armed(demo)
    demo.frames(adc("B B B X B"), 20)
    demo.frames(adc(NEUTRAL), 20)
    demo.frames(adc("B O O X B"), 20)
    assert demo.engine.spoken == [("I am feeling hungry.", "Sad"), ("I am not feeling well.", "Calm")]


def test_remapping_the_sentence_changes_output_not_recognition(demo):
    g = by_name(demo)
    thanks = demo.c.sentence_for(g["THANK_YOU"])
    demo.c.repo.save(dataclasses.replace(g["WATER"], sentence_id=thanks.id))         # WATER -> another sentence
    demo.c.reload_gestures()
    connect_armed(demo)
    demo.frames(adc("O O O X O"), 20)
    d = demo.c.detection
    assert d.display == "WATER" and str(d.saved_pattern) == "O O O X O"              # same recognition key
    assert d.matched_emotion == "Gratitude"                                          # the NEW sentence's metadata
    assert demo.engine.spoken == [("Thank you for helping me.", "Urgent")]           # WATER mapping's style


def test_library_shows_emotion_from_the_sentence(demo):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    QApplication.instance() or QApplication([])
    set_sentence_emotion(demo, by_name(demo)["SAD"].sentence_id, "Edited meaning")
    demo.c.reload_gestures()
    win = MainWindow(demo.c, start_timers=False)
    win.nav.setCurrentRow(PAGE_ORDER.index("Gesture Library"))
    win.refresh()
    t = win.pages["Gesture Library"].table
    rows = {t.item(r, 0).text(): [t.item(r, c).text() for c in range(7)] for r in range(t.rowCount())}
    assert rows["SAD"][2:5] == ["I am feeling sad today.", "\U0001F622 Edited meaning", "Sad"]
    win.nav.setCurrentRow(PAGE_ORDER.index("Live Glove"))
    connect_armed(demo)
    demo.frames(adc("O B O X O"), 20)
    win.refresh()
    live = win.pages["Live Glove"]
    assert live.sentence.text() == "I am feeling sad today."
    assert "Edited meaning" in live.meaning.text() and "metadata" in live.meaning.text()
    win.close()


# ------------------------------------------------------------------ upgrade of earlier workspaces
def test_earlier_emotion_category_is_moved_once_user_rows_untouched(tmp_path):
    from sign2speech_v2.config.demo import META_NO_EMOTION
    from sign2speech_v2.config.settings import Workspace

    first = Rig(tmp_path, demo=True)
    first.c.close()
    ws = Workspace(tmp_path / "ws")
    con = sqlite3.connect(ws.db_path)
    with con:                                                                        # as earlier releases left it
        con.execute("UPDATE gestures SET category = 'Emotion' WHERE builtin_demo = 1")
        con.execute("DELETE FROM meta WHERE key LIKE ?", (META_NO_EMOTION + ":%",))
    con.close()
    repo = GestureRepository(ws.db_path)
    repo.save(Gesture(name="MINE", display_text="Mine", finger_pattern=GesturePattern.parse("B B B B O"),
                      category="Emotion"))                                           # the user's own choice
    repo.close()
    rig = Rig(tmp_path, demo=True)
    g = by_name(rig)
    assert all(g[n].category == "General" for n in SPEC)
    assert g["MINE"].category == "Emotion"
    ids = {n: (g[n].id, g[n].sentence_id, g[n].speech_style, g[n].finger_pattern.compact()) for n in SPEC}
    rig.c.close()
    again = Rig(tmp_path, demo=True)
    g2 = by_name(again)
    assert {n: (g2[n].id, g2[n].sentence_id, g2[n].speech_style, g2[n].finger_pattern.compact()) for n in SPEC} == ids
    again.c.close()

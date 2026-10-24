"""Built-in sentence library with emotion / icon / meaning metadata (not emotion detection)."""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from sign2speech_v2.config.demo import BUILTIN_SENTENCES, suggested_style
from sign2speech_v2.gestures.repository import SPEECH_STYLES

from conftest import Rig

EXPECTED = [
    ("Please give me some water.", "Need / request", "\U0001F4A7", "Need / request"),
    ("I am feeling hungry.", "Hunger / discomfort", "\U0001F37D️", "Hunger / discomfort"),
    ("I am very happy to see you.", "Happiness", "\U0001F60A", "Happiness"),
    ("I am feeling sad today.", "Sadness", "\U0001F622", "Sadness"),
    ("Please don't touch my things.", "Anger", "\U0001F620", "Anger"),
    ("I am scared, please help me.", "Fear / emergency", "\U0001F628", "Fear / emergency"),
    ("I am feeling tired.", "Tiredness / discomfort", "\U0001F634", "Tiredness / discomfort"),
    ("Please leave me alone.", "Need for personal space", "\U0001F6D1", "Need for personal space"),
    ("I am not feeling well.", "Illness / discomfort", "\U0001F912", "Illness / discomfort"),
    ("Thank you for helping me.", "Gratitude", "\U0001F64F", "Gratitude"),
]


@pytest.fixture
def lib(tmp_path):
    rig = Rig(tmp_path, demo=True)
    yield rig
    rig.c.close()


def library(rig):
    return {s.sentence: s for s in rig.c.sentences if s.builtin_demo and s.emotion}


# 1-5
def test_ten_builtin_sentences_with_exact_text_emotion_icon_meaning(lib):
    got = library(lib)
    assert len(got) == 10
    for text, emotion, icon, meaning in EXPECTED:
        s = got[text]                                        # 2: exact sentence text
        assert s.emotion == emotion                          # 3
        assert s.icon == icon                                # 4
        assert s.meaning == meaning                          # 5
        assert s.style == "Neutral" and s.enabled
    assert [(t, e, i, m) for _, t, e, i, m in BUILTIN_SENTENCES] == EXPECTED


def test_icons_survive_a_restart_exactly(tmp_path):
    r1 = Rig(tmp_path, demo=True)
    r1.c.close()
    con = sqlite3.connect(tmp_path / "ws" / "studio.db")
    icons = [r[0] for r in con.execute("SELECT icon FROM speech_sentences WHERE emotion != '' ORDER BY id")]
    assert icons == [e[2] for e in EXPECTED]


def test_library_is_seeded_once(tmp_path):
    r1 = Rig(tmp_path, demo=True)
    r1.c.delete_sentence(library(r1)["I am feeling hungry."].id)
    r1.c.close()
    r2 = Rig(tmp_path, demo=True)
    assert "I am feeling hungry." not in library(r2) and len(library(r2)) == 9
    r2.c.close()


# 6
@pytest.mark.parametrize("text,emotion,icon,meaning", EXPECTED)
def test_each_sentence_loads_independently(lib, text, emotion, icon, meaning):
    lib.c.clear_sentence()
    assert lib.c.load_sentence(library(lib)[text].id).ok
    e = lib.c.sentence_editor
    assert (e.sentence, e.emotion, e.icon, e.meaning, e.style) == (text, emotion, icon, meaning, "Neutral")


# 7, 8
def test_same_sentence_with_every_style_keeps_emotion(lib):
    lib.c.load_sentence(library(lib)["Please give me some water."].id)
    e = lib.c.sentence_editor
    for style in SPEECH_STYLES:
        e.style = style
        assert lib.c.speak_sentence().ok
        assert e.emotion == "Need / request" and e.meaning == "Need / request"   # 8
    assert lib.engine.spoken == [("Please give me some water.", st) for st in SPEECH_STYLES]
    assert all("\U0001F4A7" not in text for text, _ in lib.engine.spoken)         # icon is never spoken


# 9
def test_changing_emotion_does_not_change_text_or_style(lib):
    lib.c.load_sentence(library(lib)["I am feeling sad today."].id)
    e = lib.c.sentence_editor
    e.style = "Calm"
    e.emotion, e.meaning = "Tired", "Tired"
    assert e.sentence == "I am feeling sad today." and e.style == "Calm"
    assert library(lib)["I am feeling sad today."].emotion == "Sadness"            # stored record unchanged


def test_emotion_never_selects_the_style_automatically(lib):
    lib.c.load_sentence(library(lib)["I am scared, please help me."].id)
    assert lib.c.sentence_editor.style == "Neutral"                                # not auto-changed
    assert lib.c.suggested_sentence_style() == "Urgent"                            # only a suggestion
    assert lib.c.sentence_editor.style == "Neutral"
    assert lib.c.apply_suggested_style().ok                                        # explicit user action
    assert lib.c.sentence_editor.style == "Urgent"
    assert suggested_style("Happiness") == "Happy" and suggested_style("unknown thing") is None
    assert all(v in SPEECH_STYLES for v in (suggested_style(e[1]) for e in EXPECTED))


def test_saving_a_variant_keeps_metadata(lib):
    lib.c.load_sentence(library(lib)["Please give me some water."].id)
    lib.c.sentence_editor.style = "Urgent"
    assert lib.c.save_sentence().ok
    v = lib.c.sentence_repo.get(lib.c.sentence_editor.current_id)
    assert (v.sentence, v.style, v.emotion, v.icon) == ("Please give me some water.", "Urgent", "Need / request",
                                                        "\U0001F4A7")
    assert not v.builtin_demo


# 10
def test_deleting_one_sentence_keeps_the_others(lib):
    before = library(lib)
    assert lib.c.delete_sentence(before["Please don't touch my things."].id).ok
    after = library(lib)
    assert set(before) - set(after) == {"Please don't touch my things."}
    assert all(after[t] == before[t] for t in after)


# 11
def test_gesture_database_unchanged(tmp_path):
    rig = Rig(tmp_path, demo=True)
    gestures = [dataclasses.astuple(g) for g in rig.c.repo.all()]
    rig.c.load_sentence(library(rig)["Please give me some water."].id)
    rig.c.sentence_editor.style = "Urgent"
    rig.c.save_sentence()
    rig.c.speak_sentence()
    unmapped = next(s for s in rig.c.sentences if s.name == "HELP_REQUEST")   # no gesture uses it
    rig.c.delete_sentence(unmapped.id)
    assert [dataclasses.astuple(g) for g in rig.c.repo.all()] == gestures
    assert len(rig.c.repo.all()) == 10                                  # the prototype gestures
    con = sqlite3.connect(rig.ws.db_path)
    gcols = {r[1] for r in con.execute("PRAGMA table_info(gestures)")}
    assert not {"emotion", "icon", "meaning", "sentence"} & gcols
    rig.c.close()


def test_existing_sentences_stay_valid_after_migration(tmp_path):
    """A speech_sentences table from before this feature gets the new columns with empty values."""
    from sign2speech_v2.speech.sentences import SentenceRepository

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE speech_sentences (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        sentence TEXT NOT NULL, style TEXT NOT NULL DEFAULT 'Neutral', enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (name, sentence, style));
        INSERT INTO speech_sentences (name, sentence, style, created_at, updated_at)
        VALUES ('OLD', 'An old sentence.', 'Sad', 'x', 'x');""")
    con.commit()
    con.close()
    repo = SentenceRepository(db)
    (old,) = repo.all()
    assert (old.name, old.sentence, old.style, old.emotion, old.icon, old.meaning, old.builtin_demo) == \
        ("OLD", "An old sentence.", "Sad", "", "", "", False)
    repo.close()


# UI
def test_speech_page_builtin_sentences_section(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLabel

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    QApplication.instance() or QApplication([])
    rig = Rig(tmp_path, demo=True)
    win = MainWindow(rig.c, start_timers=False)
    win.nav.setCurrentRow(PAGE_ORDER.index("Speech"))
    win.refresh()
    page = win.pages["Speech"]
    lst = page.builtin_list
    texts = [lst.item(i).text() for i in range(lst.count())]
    assert texts == [f"{icon}  {text}\n      Emotion: {emo}" for text, emo, icon, _ in EXPECTED]
    lst.itemClicked.emit(lst.item(0))                              # water
    assert page.s_text.text() == "Please give me some water."
    assert page.s_emotion.text() == "Need / request" and page.s_icon.text() == "\U0001F4A7"
    assert page.s_style.currentText() == "Neutral"                  # emotion does not pick the style
    win.refresh()
    assert "Suggested style: Urgent (suggestion only)" in page.suggestion.text()
    page.s_style.setCurrentText("Sad")
    page.s_style.activated.emit(page.s_style.currentIndex())
    page.btn_speak_s.click()
    assert rig.engine.spoken[-1] == ("Please give me some water.", "Sad")
    labels = " ".join(lab.text() for lab in page.findChildren(QLabel))
    assert ("Emotion describes the meaning/context of the sentence. Speech style controls how the sentence is "
            "spoken.") in labels
    win.close()
    rig.c.close()


def test_existing_workspace_receives_new_sentences_but_not_deleted_ones(tmp_path):
    """A workspace seeded by the first release (6 sentences, one flag) gets sentences 7-10 on the next start;
    a first-release sentence the user deleted is not restored."""
    r1 = Rig(tmp_path, demo=True)
    new_names = ("TIRED", "LEAVE_ME_ALONE", "NOT_WELL", "THANK_YOU")
    con = sqlite3.connect(r1.ws.db_path)
    with con:   # rebuild the first-release state: only the legacy flag, no per-record markers, no 7-10
        con.execute("DELETE FROM meta WHERE key LIKE 'builtin_sentence:%'")
        con.execute("DELETE FROM speech_sentences WHERE name IN (?, ?, ?, ?)", new_names)
    con.close()
    r1.c.reload_sentences()
    r1.c.delete_sentence(library(r1)["I am feeling hungry."].id)       # user deleted one earlier
    r1.c.close()
    r2 = Rig(tmp_path, demo=True)
    got = library(r2)
    assert {s.name for s in got.values()} >= set(new_names)
    assert "I am feeling hungry." not in got and len(got) == 9
    assert len([s for s in r2.c.sentences if s.name == "WATER_REQUEST"]) == 1     # no duplicates
    r2.c.close()
    r3 = Rig(tmp_path, demo=True)                                                  # further restarts: stable
    assert len(library(r3)) == 9
    r3.c.close()


def test_new_emotions_have_valid_suggestions():
    assert suggested_style("Gratitude") == "Happy"
    assert suggested_style("Tiredness / discomfort") == "Calm"
    assert all(suggested_style(e[1]) in SPEECH_STYLES for e in EXPECTED)

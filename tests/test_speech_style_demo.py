"""Same sentence, six selectable expressive speech styles (BUILT-IN SPEECH DEMO)."""

from __future__ import annotations

import struct
import sys
import wave

import pytest

from sign2speech_v2.config.demo import SPEECH_DEMO_NAME, SPEECH_DEMO_SENTENCE, SPEECH_DEMO_STYLES
from sign2speech_v2.speech.engine import STYLE_VOICES, SapiEngine

from conftest import Rig

HELP = "I need your help right now."


@pytest.fixture
def demo(tmp_path):
    rig = Rig(tmp_path, demo=True)
    yield rig
    rig.c.close()


def by_style(rig):
    """The HELP_REQUEST style demo only (other built-in sentences exist too)."""
    return {s.style: s for s in rig.c.sentences if s.builtin_demo and s.name == SPEECH_DEMO_NAME}


def help_records(rig):
    return [s for s in rig.c.sentences if s.name == SPEECH_DEMO_NAME]


# 1, 2, 3
def test_six_builtin_style_records_share_one_sentence(demo):
    recs = by_style(demo)
    assert SPEECH_DEMO_SENTENCE == HELP
    assert set(recs) == {"Neutral", "Happy", "Sad", "Angry", "Calm", "Urgent"} == set(SPEECH_DEMO_STYLES)
    assert {s.sentence for s in recs.values()} == {HELP}
    assert {s.name for s in recs.values()} == {SPEECH_DEMO_NAME}
    assert len({s.id for s in recs.values()}) == 6                  # six separate records


def test_speech_page_opens_ready_with_the_demo_sentence(demo):
    e = demo.c.sentence_editor
    assert (e.sentence, e.style) == (HELP, "Neutral")


def test_demo_is_seeded_once_and_not_duplicated(tmp_path):
    r1 = Rig(tmp_path, demo=True)
    happy = by_style(r1)["Happy"].id
    r1.c.delete_sentence(happy)                                       # user removes one style
    r1.c.close()
    r2 = Rig(tmp_path, demo=True)                                     # restart
    assert sorted(by_style(r2)) == ["Angry", "Calm", "Neutral", "Sad", "Urgent"]
    assert len(help_records(r2)) == 5
    r2.c.close()


# 4, 5, 6
@pytest.mark.parametrize("style", ["Happy", "Sad"])
def test_loading_a_style_loads_same_sentence_with_that_style(demo, style):
    assert demo.c.load_sentence(by_style(demo)[style].id).ok
    assert (demo.c.sentence_editor.sentence, demo.c.sentence_editor.style) == (HELP, style)


def test_changing_style_does_not_change_text(demo):
    demo.c.load_sentence(by_style(demo)["Sad"].id)
    for style in SPEECH_DEMO_STYLES:
        demo.c.sentence_editor.style = style
        assert demo.c.sentence_editor.sentence == HELP


# 7
def test_speak_sends_the_same_text_for_every_style(demo):
    demo.c.load_sentence(by_style(demo)["Neutral"].id)
    for style in SPEECH_DEMO_STYLES:
        demo.c.sentence_editor.style = style
        assert demo.c.speak_sentence().ok
    assert demo.engine.spoken == [(HELP, s) for s in SPEECH_DEMO_STYLES]
    st = demo.c.speech_state
    assert (st.last_text, st.last_style) == (HELP, "Urgent")


# 8
def test_style_parameters_differ():
    params = {s: (STYLE_VOICES[s].rate, STYLE_VOICES[s].pitch, STYLE_VOICES[s].volume) for s in SPEECH_DEMO_STYLES}
    assert len(set(params.values())) == 6                             # every style is a distinct preset
    v = STYLE_VOICES
    assert v["Happy"].rate > v["Neutral"].rate and v["Happy"].pitch > v["Neutral"].pitch
    assert v["Sad"].rate < v["Neutral"].rate and v["Sad"].pitch < 0 and v["Sad"].volume < v["Neutral"].volume
    assert v["Angry"].volume > v["Neutral"].volume and v["Angry"].rate > 0
    assert v["Calm"].rate < 0 and v["Calm"].volume < v["Neutral"].volume
    assert v["Urgent"].rate == max(x.rate for x in v.values()) and v["Urgent"].volume == 100
    assert all(-10 <= x.rate <= 10 and 0 <= x.volume <= 100 and -20 <= x.pitch <= 20 for x in v.values())


def test_sapi_request_carries_rate_pitch_volume_and_same_text():
    eng = SapiEngine()
    reqs = {s: eng.request(HELP, s) for s in SPEECH_DEMO_STYLES}
    assert {r["text"] for r in reqs.values()} == {HELP}
    assert reqs["Happy"]["pitch"] == "+20%" and reqs["Sad"]["pitch"] == "-20%" and reqs["Neutral"]["pitch"] == "+0%"
    assert len({(r["rate"], r["pitch"], r["volume"]) for r in reqs.values()}) == 6


def _measure(path):
    with wave.open(str(path)) as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    x = struct.unpack("<%dh" % (len(raw) // 2), raw)[::ch]
    rms = (sum(v * v for v in x) / len(x)) ** 0.5
    L, f0s = int(sr * 0.04), []
    for i in range(0, len(x) - L, L):
        fr = x[i:i + L]
        if sum(v * v for v in fr) / L < (rms * 0.8) ** 2:
            continue
        best, lag_best = 0, 0
        for lag in range(int(sr / 400), int(sr / 70)):
            c = sum(fr[j] * fr[j + lag] for j in range(0, L - lag, 2))
            if c > best:
                best, lag_best = c, lag
        if lag_best:
            f0s.append(sr / lag_best)
    f0s.sort()
    return len(x) / sr, rms, f0s[len(f0s) // 2]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows speech")
def test_windows_speech_really_changes_delivery(tmp_path):
    """Real offline Windows speech, rendered to WAV (silent): Sad vs Happy vs Neutral."""
    got = {}
    for style in ("Neutral", "Happy", "Sad"):
        wav = tmp_path / f"{style}.wav"
        eng = SapiEngine(wav_path=str(wav))
        assert eng.speak(HELP, style).ok
        assert eng.wait_idle(60)
        eng.close()
        assert eng.last_error == ""
        got[style] = _measure(wav)
    (dn, rn, fn), (dh, rh, fh), (ds, rs, fs) = got["Neutral"], got["Happy"], got["Sad"]
    assert ds > dn > dh                      # Sad slowest, Happy faster
    assert fh > fn > fs                      # Happy higher pitch, Sad lower
    assert rs < rn                           # Sad softer


# 9
def test_same_style_and_sentence_is_not_duplicated(demo):
    e = demo.c.sentence_editor
    e.name, e.sentence, e.style = SPEECH_DEMO_NAME, HELP, "Happy"
    res = demo.c.save_sentence()
    assert not res.ok and "already saved" in res.message
    assert len(by_style(demo)) == 6 and len(help_records(demo)) == 6


def test_user_sentences_still_work(demo):
    e = demo.c.sentence_editor
    demo.c.clear_sentence()
    e.sentence, e.style = "Please call my family.", "Calm"
    assert demo.c.save_sentence().ok
    assert demo.c.speak_sentence().ok
    user = [s for s in demo.c.sentences if not s.builtin_demo]
    assert [(s.sentence, s.style) for s in user] == [("Please call my family.", "Calm")]


# 10
def test_deleting_happy_keeps_sad(demo):
    recs = by_style(demo)
    assert demo.c.delete_sentence(recs["Happy"].id).ok
    left = by_style(demo)
    assert "Happy" not in left and left["Sad"].id == recs["Sad"].id and left["Sad"].sentence == HELP


# 11
def test_gesture_records_unchanged_by_speech_demo(demo):
    gestures = {g.name: g for g in demo.c.repo.all()}
    assert len(gestures) == 10 and all(g.builtin_demo for g in gestures.values())   # prototype gestures only
    assert "HELP_REQUEST" not in {g.name for g in gestures.values()}
    import sqlite3
    con = sqlite3.connect(demo.ws.db_path)
    assert con.execute("SELECT COUNT(*) FROM gestures WHERE display_text LIKE '%help right now%'").fetchone()[0] == 0


def test_speech_page_shows_the_demo(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLabel

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    QApplication.instance() or QApplication([])
    rig = Rig(tmp_path, demo=True)
    win = MainWindow(rig.c, start_timers=False)
    win.nav.setCurrentRow(PAGE_ORDER.index("Speech"))
    win.refresh()
    page = win.pages["Speech"]
    assert page.s_text.text() == HELP and page.s_style.currentText() == "Neutral"
    t = page.s_table
    rows = [[t.item(r, c).text() for c in range(5)] for r in range(t.rowCount())]
    rows = [r for r in rows if r[0] == SPEECH_DEMO_NAME]
    assert sorted(r[2] for r in rows) == sorted(SPEECH_DEMO_STYLES)
    assert all(r[1] == HELP and r[4] == "BUILT-IN SPEECH DEMO" for r in rows)
    all_rows = [[t.item(r, c).text() for c in range(5)] for r in range(t.rowCount())]
    happy_row = next(i for i, r in enumerate(all_rows) if r[0] == SPEECH_DEMO_NAME and r[2] == "Happy")
    t.selectRow(happy_row)
    assert page.s_text.text() == HELP and page.s_style.currentText() == "Happy"
    page.btn_speak_s.click()
    win.refresh()
    assert 'Last spoken: "I need your help right now."   Style: Happy' in page.last.text()
    texts = " ".join(lab.text() for lab in page.findChildren(QLabel))
    assert "Selectable expressive speech styles" in texts
    assert "does not detect emotion" in texts and "does not detect or understand" in texts
    win.close()
    rig.c.close()

"""Preconfigured demo: PRELOADED CALIBRATION + 10 prototype gestures mapped to stored sentences.

Live input is always (fake-transport) serial frames through the real pipeline - never the simulator.
Raw ADC values are near this glove's measured open / closed readings, so every O/B pattern below is
produced by the preloaded calibration, exactly like on the physical glove.

Ring is X (ANY) in all ten built-in patterns: live tests run with Ring OPEN and with Ring BENT.
"""

from __future__ import annotations

import dataclasses
import itertools
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from sign2speech_v2.calibration.model import CalibrationStore
from sign2speech_v2.codegen.arduino_cli import compile_sketch, find_arduino_cli
from sign2speech_v2.codegen.generator import generate
from sign2speech_v2.config import pins
from sign2speech_v2.config.demo import (PREVIOUS_PROTOTYPE_PATTERNS, PRELOADED_LABEL,
                                        PRELOADED_VALUES, PROTOTYPE_GESTURES, ensure_demo_defaults,
                                        pattern_conflicts, primary_code)
from sign2speech_v2.config.settings import Settings
from sign2speech_v2.core.frame import Source
from sign2speech_v2.core.patterns import GesturePattern
from sign2speech_v2.gestures.repository import SPEECH_STYLES, Gesture, GestureRepository

from conftest import Rig
from sign2speech_v2.config.demo import _library_entry as _entry

HOST = Path(__file__).parent / "host"
OPEN_ADC = (200, 60, 70, 20, 40)          # Thumb..Little, near the measured OPEN values
BENT_ADC = (1050, 1150, 1100, 190, 780)   # near the measured CLOSED values

# the specification, restated literally: name -> (pattern, sentence, emotion/meaning, default style)
SPEC = {
    "WATER": ("O O O X O", "Please give me some water.", "Need / request", "Urgent"),
    "HUNGRY": ("B B B X B", "I am feeling hungry.", "Hunger / discomfort", "Sad"),
    "HAPPY": ("O B B X B", "I am very happy to see you.", "Happiness", "Happy"),
    "SAD": ("O B O X O", "I am feeling sad today.", "Sadness", "Sad"),
    "ANGRY": ("B O B X O", "Please don't touch my things.", "Anger", "Angry"),
    "SCARED": ("B B O X O", "I am scared, please help me.", "Fear / emergency", "Urgent"),
    "TIRED": ("O O O X B", "I am feeling tired.", "Tiredness / discomfort", "Sad"),
    "LEAVE_ME_ALONE": ("O O B X B", "Please leave me alone.", "Need for personal space", "Angry"),  # no "Firm"
    "NOT_WELL": ("B O O X B", "I am not feeling well.", "Illness / discomfort", "Calm"),
    "THANK_YOU": ("O B B X O", "Thank you for helping me.", "Gratitude", "Happy"),
}


def _code(pattern: str) -> str:
    """'O O O X O' -> 'OOOO' (Thumb, Index, Middle, Little)."""
    p = pattern.replace(" ", "")
    return p[:3] + p[4]


# the four-finger combinations no built-in gesture uses
UNUSED_CODES = sorted({"".join(c) for c in itertools.product("OB", repeat=4)} - {_code(p) for p, *_ in SPEC.values()})


def live(pattern: str, ring: str = "O") -> str:
    """The real hand for a stored pattern: X (Ring) replaced by an actual O/B state."""
    return pattern.replace("X", ring)


def adc(pattern: str, ring: str = "O") -> tuple[int, ...]:
    letters = live(pattern, ring).replace(" ", "")
    return tuple(BENT_ADC[i] if c == "B" else OPEN_ADC[i] for i, c in enumerate(letters))


def code_adc(code: str, ring: str = "O") -> tuple[int, ...]:
    """ADC for a 4-finger code (T I M L) with the given Ring state."""
    return adc(" ".join(code[:3] + ring + code[3]))


NEUTRAL = "B O O B O"                     # matches no built-in gesture (4-finger code B O O O is unused)


def connect_armed(rig) -> None:
    """Connect, then hold a neutral hand shape until STARTUP ARMING allows gesture speech."""
    rig.connect()
    rig.frames(adc(NEUTRAL), 40)
    assert rig.c.detection.display == "UNKNOWN" and rig.c.speech_state.armed
    rig.engine.spoken.clear()


@pytest.fixture
def demo(tmp_path):
    rig = Rig(tmp_path, demo=True)
    yield rig
    rig.c.close()


def gestures(rig):
    return {g.name: g for g in rig.c.repo.all()}


# ------------------------------------------------------------ calibration
def test_preloaded_calibration_loads(demo):
    cal = demo.c.calibration.calibration
    assert cal is not None and cal.label == PRELOADED_LABEL
    assert {f.finger: (f.open, f.closed) for f in cal.fingers} == PRELOADED_VALUES
    assert demo.c.calibration.open == (181, 37, 44, 6, 19)
    assert demo.c.calibration.closed == (1090, 1183, 1132, 201, 807)
    assert CalibrationStore(demo.ws.calibration_path).load() == cal


def test_existing_calibration_is_never_replaced(tmp_path):
    from sign2speech_v2.config.settings import Workspace
    from conftest import make_calibration

    ws = Workspace(tmp_path / "ws").ensure()
    store = CalibrationStore(ws.calibration_path)
    store.save(make_calibration(500, 2500))
    repo = GestureRepository(ws.db_path)
    ensure_demo_defaults(repo, store)
    assert store.load().fingers[0].open == 500
    repo.close()


# ------------------------------------------------------------ 1-7 gestures, patterns, mapping
def test_ten_prototype_gestures_with_exact_patterns_sentences_emotions_styles(demo):
    got = gestures(demo)
    assert set(got) == set(SPEC)                                                     # 1
    for name, (pattern, sentence, emotion, style) in SPEC.items():
        g = got[name]
        assert str(g.finger_pattern) == pattern
        s = demo.c.sentence_for(g)
        assert s is not None and g.sentence_id == s.id                               # 4 one mapped sentence
        assert s.sentence == sentence                                                # 5
        assert s.emotion == emotion and s.meaning == emotion                         # 6
        assert g.speech_style == style                                               # 7 default style
        assert g.builtin_demo and g.enabled
        assert "not an official Indian Sign Language" in g.isl_description
    assert [p[0] for p in PROTOTYPE_GESTURES] == list(SPEC)


def test_ring_is_any_and_only_ring(demo):                                            # 2
    for g in gestures(demo).values():
        tokens = g.finger_pattern.compact()
        assert tokens[3] == "X"                                                      # Ring = X
        assert "X" not in tokens[:3] + tokens[4]                                     # the 4 others are O/B


def test_four_finger_patterns_are_unique_and_never_ambiguous(demo):                  # 3
    codes = [primary_code(g.finger_pattern) for g in gestures(demo).values()]
    assert sorted(codes) == sorted(_code(p) for p, *_ in SPEC.values())
    assert len(codes) == len(set(codes)) == 10
    assert pattern_conflicts(demo.c.repo.all()) == [] and demo.c.store.conflicts == []
    # the fewest one-finger-apart pairs possible for 10 of the 16 codes (at most 8 codes can be 2 apart)
    close = [(a, b) for a, b in itertools.combinations(codes, 2) if sum(x != y for x, y in zip(a, b)) == 1]
    assert len(close) == 9
    assert all(c[1:] != "BOB" for c in codes)                                        # no middle-finger-only shape


def test_default_style_is_configured_per_gesture_sentence_mapping(demo):
    # gesture, pattern, display, mapped sentence, style - no emotion column in the gesture table
    assert {p[0]: (p[1], _entry(p[3])[1], p[4]) for p in PROTOTYPE_GESTURES} == {
        n: (v[0], v[1], v[3]) for n, v in SPEC.items()}
    assert all(len(p) == 5 for p in PROTOTYPE_GESTURES)
    assert {p[4] for p in PROTOTYPE_GESTURES} <= set(SPEECH_STYLES)
    assert "Firm" not in SPEECH_STYLES                                               # hence LEAVE_ME_ALONE = Angry


def test_sentence_text_is_not_copied_into_gestures(demo):
    con = sqlite3.connect(demo.ws.db_path)
    rows = con.execute("SELECT display_text, sentence_id FROM gestures").fetchall()
    assert all(r[1] is not None for r in rows)
    assert not any(t for t, _ in rows if t in {v[1] for v in SPEC.values()})         # sentence not duplicated
    stored = [r[0] for r in con.execute("SELECT sentence FROM speech_sentences")]
    con.close()
    assert all(stored.count(text) == 1 for _, text, _, _ in SPEC.values())           # 27 stored once, separately


def test_overlap_is_reported_not_guessed(demo):
    demo.c.repo.save(Gesture(name="MINE", display_text="Mine", finger_pattern=GesturePattern.parse("O O O X X")))
    demo.c.reload_gestures()
    assert {frozenset(p) for p in demo.c.store.conflicts} == {frozenset(("MINE", "WATER")),
                                                              frozenset(("MINE", "TIRED"))}
    demo.connect()
    demo.frames(adc("O O O X O"), 20)
    assert demo.c.detection.display == "AMBIGUOUS" and demo.engine.spoken == []


# ------------------------------------------------------------ 8-17, 21, 22, 25 live matching
@pytest.mark.parametrize("ring", ["O", "B"])
@pytest.mark.parametrize("name", list(SPEC))
def test_live_pattern_recognizes_each_gesture_and_speaks_its_sentence(demo, name, ring):
    pattern, sentence, _, style = SPEC[name]
    connect_armed(demo)
    demo.frames(adc(pattern, ring), 20)
    d = demo.c.detection
    assert str(d.live_pattern) == live(pattern, ring)                                # real O/B, never X
    assert str(d.saved_pattern) == pattern                                           # stored X stays visible
    assert d.display == name and d.accepted
    assert d.matched_sentence == sentence
    assert demo.engine.spoken == [(sentence, style)]                                 # 21, 22
    assert f"SHOW {name}|{sentence}" in demo.written()                               # 25
    shows = [c for c in demo.written() if c.startswith(f"SHOW {name}|")]
    assert [c.split("|", 1)[1] for c in shows] == [spoken for spoken, _ in demo.engine.spoken]  # OLED == speech
    st = demo.c.speech_state
    assert (st.last_spoken_gesture, st.last_text, st.last_style) == (name, sentence, style)


@pytest.mark.parametrize("ring", ["O", "B"])
@pytest.mark.parametrize("code", UNUSED_CODES)
def test_unused_patterns_are_unknown(demo, code, ring):                              # 18
    demo.connect()
    demo.frames(code_adc(code, ring), 20)
    assert demo.c.detection.display == "UNKNOWN" and demo.engine.spoken == []
    assert "UNKNOWN" in demo.written()


def test_wrong_pattern_is_unknown(demo):
    demo.connect()
    demo.frames(adc("B O O B O"), 20)
    assert str(demo.c.detection.live_pattern) == "B O O B O"
    assert demo.c.detection.display == "UNKNOWN" and demo.engine.spoken == []
    assert "UNKNOWN" in demo.written()


def test_ring_changes_while_holding_do_not_change_recognition(demo):                 # 19
    connect_armed(demo)
    demo.frames(adc("O O O X O", "O"), 20)
    assert demo.c.detection.display == "WATER"
    for ring in "BOBOB":                                     # Ring flips while the other fingers hold still
        demo.frames(adc("O O O X O", ring), 30)
        assert demo.c.detection.display == "WATER" and str(demo.c.detection.live_pattern) == live("O O O X O", ring)
    assert demo.engine.spoken == [("Please give me some water.", "Urgent")]       # still spoken only once


def test_no_data_is_waiting_not_unknown(demo):
    assert demo.c.detection.display == "WAITING FOR DATA"
    demo.connect()
    for _ in range(30):
        demo.clock.advance(0.1)
        demo.c.tick()
    assert demo.c.detection.display == "WAITING FOR DATA" and demo.c.live_sensor.raw is None


# ------------------------------------------------------------ 20, 23, 24, 26
def test_selected_gesture_cannot_override_live_detection(demo):
    hungry = gestures(demo)["HUNGRY"]
    demo.c.select_gesture(hungry.id)
    demo.c.edit_gesture(hungry.id)
    demo.connect()
    demo.frames(adc("O O O X O"), 20)
    assert str(demo.c.detection.live_pattern) == "O O O O O" and demo.c.detection.display == "WATER"
    assert demo.c.editor.name == "HUNGRY"


def test_held_gesture_speaks_once_and_again_after_leaving(demo):
    connect_armed(demo)
    demo.frames(adc("O O O X O"), 300)                     # 6 s holding WATER
    water = ("Please give me some water.", "Urgent")
    assert demo.engine.spoken == [water]
    demo.frames(adc("B O O B O"), 20)                       # leave (UNKNOWN)
    demo.frames(adc("O O O X O"), 20)                       # return
    assert demo.engine.spoken == [water, water]
    demo.frames(adc("B B B X B"), 20)                       # fist -> HUNGRY
    assert demo.engine.spoken[-1] == ("I am feeling hungry.", "Sad")
    assert demo.written().count("SHOW WATER|Please give me some water.") == 2


def test_oled_command_carries_the_sentence_only(demo):
    demo.connect()
    demo.frames(adc(SPEC["SCARED"][0]), 20)
    shows = [c for c in demo.written() if c.startswith("SHOW ")]
    assert shows == ["SHOW SCARED|I am scared, please help me."]     # the name is the command key only
    text = shows[0].split("|", 1)[1]
    assert not any(w in text for w in ("SCARED", "Gesture:", "Emotion", "Style", "Urgent"))


# ------------------------------------------------------------ delete / restart / upgrade
def test_deleting_a_gesture_keeps_its_sentence(demo):
    g = gestures(demo)["THANK_YOU"]
    sid = g.sentence_id
    assert demo.c.delete_gesture(g.id).ok
    s = demo.c.sentence_repo.get(sid)
    assert s is not None and s.sentence == "Thank you for helping me."


def test_deleting_a_sentence_unlinks_but_keeps_the_gesture(demo):
    g = gestures(demo)["TIRED"]
    assert demo.c.delete_sentence(g.sentence_id).ok
    g2 = gestures(demo)["TIRED"]
    assert g2.sentence_id is None and g2.enabled
    connect_armed(demo)
    demo.frames(adc("O O O X B"), 20)
    assert demo.engine.spoken == [("Tired", "Sad")]          # falls back to the display text


def test_restart_does_not_duplicate_or_recreate(tmp_path):                           # 28
    r1 = Rig(tmp_path, demo=True)
    r1.c.delete_gesture(gestures(r1)["SAD"].id)
    before = {g.name: (g.id, g.finger_pattern.compact(), g.updated_at) for g in r1.c.repo.all()}
    r1.c.close()
    r2 = Rig(tmp_path, demo=True)
    names = [g.name for g in r2.c.repo.all()]
    assert len(names) == len(set(names)) == 9 and "SAD" not in names
    assert {g.name: (g.id, g.finger_pattern.compact(), g.updated_at) for g in r2.c.repo.all()} == before
    r2.c.close()


def _previous_release_workspace(tmp_path):
    """A workspace as the previous release left it: the 10 built-ins with exact O/B patterns (Ring used)."""
    from sign2speech_v2.config.demo import META_RING_X, ensure_all_demo_data
    from sign2speech_v2.config.settings import Workspace
    from sign2speech_v2.speech.sentences import SentenceRepository

    ws = Workspace(tmp_path / "ws").ensure()
    repo, sentences = GestureRepository(ws.db_path), SentenceRepository(ws.db_path)
    ensure_all_demo_data(repo, sentences, CalibrationStore(ws.calibration_path))
    repo.close()
    sentences.close()
    con = sqlite3.connect(ws.db_path)
    with con:
        for name, old in PREVIOUS_PROTOTYPE_PATTERNS.items():
            con.execute("UPDATE gestures SET finger_pattern = ? WHERE name = ?", (old, name))
        con.execute("DELETE FROM meta WHERE key LIKE ?", (META_RING_X + ":%",))
    con.close()
    return ws


def test_previous_release_patterns_are_upgraded_once_in_place(tmp_path):
    ws = _previous_release_workspace(tmp_path)
    repo = GestureRepository(ws.db_path)
    old = {g.name: g for g in repo.all()}
    # the user had edited SAD and disabled ANGRY: SAD's edit must survive, ANGRY stays disabled
    repo.save(dataclasses.replace(old["SAD"], finger_pattern=GesturePattern.parse("O B X B O")))
    repo.set_enabled(old["ANGRY"].id, False)
    repo.close()

    rig = Rig(tmp_path, demo=True)
    got = gestures(rig)
    assert len(got) == 10                                                            # no duplicates
    for name, (pattern, _, _, style) in SPEC.items():
        g = got[name]
        assert (g.id, g.sentence_id) == (old[name].id, old[name].sentence_id)        # same rows, same sentences
        if name == "SAD":
            assert str(g.finger_pattern) == "O B X B O"                              # user edit kept
        else:
            assert str(g.finger_pattern) == pattern and g.speech_style == style
    assert not got["ANGRY"].enabled
    assert any("Updated PROTOTYPE gesture WATER: O O O O O -> O O O X O" in t for _, t in rig.c.log)
    rig.c.close()

    # a later edit back to the old pattern is the user's choice and is never "upgraded" again
    repo = GestureRepository(ws.db_path)
    repo.save(dataclasses.replace(repo.by_name("WATER"), finger_pattern=GesturePattern.parse("O O O O O")))
    repo.close()
    rig2 = Rig(tmp_path, demo=True)
    assert str(gestures(rig2)["WATER"].finger_pattern) == "O O O O O"
    rig2.c.close()


def test_user_gesture_with_same_name_is_kept(tmp_path):
    from sign2speech_v2.config.settings import Workspace

    ws = Workspace(tmp_path / "ws").ensure()
    repo = GestureRepository(ws.db_path)
    repo.save(Gesture(name="WATER", display_text="Mine", finger_pattern=GesturePattern.parse("O O O O O")))
    repo.close()
    rig = Rig(tmp_path, demo=True)
    w = gestures(rig)["WATER"]
    # not built-in, so never "upgraded" although its pattern equals the previous release's WATER
    assert (w.display_text, str(w.finger_pattern), w.builtin_demo) == ("Mine", "O O O O O", False)
    assert len(gestures(rig)) == 10
    rig.c.close()


def test_older_hello_bye_demo_is_disabled_not_deleted(tmp_path):
    """A workspace from the first demo release (HELLO = open hand, BYE = fist) upgrades cleanly."""
    from sign2speech_v2.config.settings import Workspace

    ws = Workspace(tmp_path / "ws").ensure()
    repo = GestureRepository(ws.db_path)
    for name, p in (("HELLO", "O O O O O"), ("BYE", "B B B B B")):
        repo.save(Gesture(name=name, display_text=name.title(), finger_pattern=GesturePattern.parse(p),
                          builtin_demo=True))
    repo.set_meta("demo_gestures_seeded", "1")
    repo.close()
    rig = Rig(tmp_path, demo=True)
    g = gestures(rig)
    assert not g["HELLO"].enabled and not g["BYE"].enabled                 # kept, but disabled
    assert rig.c.store.conflicts == []
    rig.c.set_gesture_enabled(g["HELLO"].id, True)                         # the user may re-enable it
    rig.c.close()
    rig2 = Rig(tmp_path, demo=True)
    assert gestures(rig2)["HELLO"].enabled                                   # and it stays enabled
    assert {frozenset(p) for p in rig2.c.store.conflicts} == {frozenset(("HELLO", "WATER"))}
    rig2.c.close()


def test_gesture_library_page_shows_mapping(demo):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    QApplication.instance() or QApplication([])
    win = MainWindow(demo.c, start_timers=False)
    win.nav.setCurrentRow(PAGE_ORDER.index("Gesture Library"))
    win.refresh()
    t = win.pages["Gesture Library"].table
    heads = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    assert heads == ["Gesture", "Pattern", "Sentence", "Emotion / Meaning", "Default Style", "Enabled",
                     "Built-in Demo"]
    rows = {t.item(r, 0).text(): [t.item(r, c).text() for c in range(7)] for r in range(t.rowCount())}
    assert rows["WATER"] == ["WATER", "O O O X O", "Please give me some water.", "\U0001F4A7 Need / request",
                             "Urgent", "yes", "Built-in Demo"]
    win.nav.setCurrentRow(PAGE_ORDER.index("Live Glove"))
    demo.connect()
    demo.frames(adc("O B B X B", "B"), 20)
    win.refresh()
    page = win.pages["Live Glove"]
    assert page.live_pattern.text() == "O B B B B" and page.saved_pattern.text() == "O B B X B"
    assert page.live_detection.text() == "HAPPY" and page.sentence.text() == "I am very happy to see you."
    win.close()


# ------------------------------------------------------------ 29-31
def test_real_mode_never_uses_simulator_or_fake_values(demo):
    assert demo.c.live_sensor.raw is None                                    # nothing before real data
    demo.connect()
    demo.frames(adc("O O O X O"), 20)
    assert demo.c.acquisition.source is Source.REAL and demo.c.acquisition.simulator is None
    assert demo.c.live_sensor.raw == adc("O O O X O")                          # exactly the received packet
    assert demo.c.runtime_info()["Simulation active"] == "NO"
    before = dataclasses.asdict(demo.c.live_sensor)
    demo.c.edit_gesture(gestures(demo)["SAD"].id)                            # stored data never becomes live
    demo.c.load_sentence(demo.c.sentences[0].id)
    assert dataclasses.asdict(demo.c.live_sensor) == before


def test_calibration_values_unchanged(demo):
    fingers = CalibrationStore(demo.ws.calibration_path).load().to_dict()["fingers"]
    assert [(f["finger"], f["open"], f["closed"]) for f in fingers] == [
        (name, o, c) for name, (o, c) in PRELOADED_VALUES.items()]
    assert PRELOADED_VALUES == {"Thumb": (181, 1090), "Index": (37, 1183), "Middle": (44, 1132),
                                "Ring": (6, 201), "Little": (19, 807)}


# ------------------------------------------------------------ 32-34 firmware
@pytest.fixture
def fw(demo):
    return generate(demo.c.repo.all(), demo.c.calibration.calibration, Settings(), sentences=demo.c.sentence_texts())


def test_firmware_contains_the_ten_patterns_and_sentences(fw):
    g = fw.files["gestures.h"]
    assert "#define GESTURE_COUNT 10" in g
    for name, (pattern, sentence, _, _) in SPEC.items():
        assert f'{{"{name}", "{sentence}", "{pattern.replace(" ", "")}"}},' in g
    assert "CAL_OPEN[FLEX_COUNT] = {181, 37, 44, 6, 19}" in fw.files["config.h"]
    assert "CAL_CLOSED[FLEX_COUNT] = {1090, 1183, 1132, 201, 807}" in fw.files["config.h"]
    assert not fw.warnings                                                   # no overlapping patterns


def test_firmware_pins_and_buses(fw):
    pins.verify_wiring()
    cfg, ino = fw.files["config.h"], fw.files["Sign2Speech_V2.ino"]
    for n, v in (("FLEX_PIN_THUMB", 32), ("FLEX_PIN_INDEX", 35), ("FLEX_PIN_MIDDLE", 34), ("FLEX_PIN_RING", 39),
                 ("FLEX_PIN_LITTLE", 36), ("OLED_SDA_PIN", 18), ("OLED_SCL_PIN", 19), ("MPU_SDA_PIN", 22),
                 ("MPU_SCL_PIN", 21)):
        assert f"#define {n} {v}" in cfg.replace("  ", " ").replace("  ", " ")
    assert "TwoWire& mpuWire = Wire;" in ino and "TwoWire& oledWire = Wire1;" in ino


@pytest.fixture
def harness(fw, tmp_path):
    gxx = shutil.which("g++")
    if not gxx:
        pytest.skip("g++ not available")
    sketch = fw.write(tmp_path / "fw")
    exe = tmp_path / "harness.exe"
    r = subprocess.run([gxx, "-std=gnu++14", "-I", str(HOST / "stub"), "-I", str(sketch),
                        str(HOST / "harness.cpp"), "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return lambda script, *a: subprocess.run([str(exe), *a], input=script, capture_output=True, text=True,
                                             timeout=60).stdout.splitlines()


def oled_sentence(line: str) -> str:
    """'@OLED line1|line2' -> the text the OLED shows, lines joined by spaces."""
    return " ".join(line[len("@OLED "):].split("|"))


@pytest.mark.parametrize("ring", ["O", "B"])
def test_firmware_recognizes_gestures_and_shows_only_the_sentence_on_oled(harness, ring):
    script = "SETUP\n" + "".join(f"ADC {' '.join(map(str, adc(SPEC[n][0], ring)))}\nRUN 30\n" for n in SPEC)
    out = harness(script)
    assert [l for l in out if l.startswith("# GESTURE:")] == [f"# GESTURE: {n}" for n in SPEC]
    oled = [l for l in out if l.startswith("@OLED ")]
    for name, (_, sentence, _, _) in SPEC.items():
        shown = next(l for l in oled if oled_sentence(l) == sentence)       # exactly the spoken sentence
        assert name not in shown and "Gesture:" not in shown                # no name, no label
    assert "@OLED Please give me|some water." in oled                       # word-wrapped, balanced
    assert "@OLED I am scared,|please help me." in oled
    for line in oled:                                                        # every text line fits 21 chars
        assert all(len(part) <= 21 for part in line[len("@OLED "):].split("|"))


@pytest.mark.parametrize("name", list(SPEC))
def test_firmware_shows_studio_sentence_only(harness, name):
    sentence = SPEC[name][1]
    out = harness(f"SETUP\nCMD SHOW {name}|{sentence}\n")
    shown = [l for l in out if l.startswith("@OLED ")][-1]
    assert oled_sentence(shown) == sentence and name not in shown and "Gesture:" not in shown
    parts = shown[len("@OLED "):].split("|")
    assert [w for p in parts for w in p.split()] == sentence.split()        # no word cut in the middle


def test_firmware_oled_wraps_long_text_without_cutting_words(harness):
    text = "Please remember to bring my medicine and a glass of water to the kitchen table now."
    out = harness(f"SETUP\nCMD SHOW X|{text}\n")
    parts = [l for l in out if l.startswith("@OLED ")][-1][len("@OLED "):].split("|")
    assert " ".join(parts) == text and all(len(p) <= 21 for p in parts) and len(parts) <= 8
    assert [w for p in parts for w in p.split()] == text.split()


def test_firmware_oled_waiting_and_unknown(harness):
    out = harness("SETUP\nCMD UNKNOWN\nCMD CLEAR\n")
    oled = [l for l in out if l.startswith("@OLED ")]
    assert oled[-2:] == ["@OLED Unknown", "@OLED Waiting..."]   # same states, no "Gesture:" label


@pytest.mark.firmware_compile
@pytest.mark.skipif(find_arduino_cli() is None, reason="arduino-cli not installed")
def test_firmware_compiles(fw, tmp_path):
    r = compile_sketch(fw.write(tmp_path))
    assert r.ok, r.output[-3000:]

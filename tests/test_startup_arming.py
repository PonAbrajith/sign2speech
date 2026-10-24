"""STARTUP ARMING: a hand already in a gesture when recognition (re)starts is shown, never spoken.

Speech arms after the live result has matched NO gesture for `arm_neutral_samples` frames. Every value is
a serial data line through the real pipeline (fake transport); recognition itself is unchanged.
"""

from __future__ import annotations

import pytest

from sign2speech_v2.config.settings import Settings
from sign2speech_v2.recognition.stability import SpeechArming

from conftest import Rig
from test_demo_mode import NEUTRAL, SPEC, adc

WATER = ("Please give me some water.", "Urgent")
HUNGRY = ("I am feeling hungry.", "Sad")


@pytest.fixture
def demo(tmp_path):
    rig = Rig(tmp_path, demo=True)
    yield rig
    rig.c.close()


# ------------------------------------------------------------------ the state machine
def test_arming_needs_consecutive_neutral_frames():
    a = SpeechArming(3)
    assert not a.armed
    assert [a.update(x) for x in (None, None, "WATER", None, None)] == [False] * 5    # a match restarts it
    assert a.update(None) is True and a.armed                                         # 3rd in a row: armed
    assert a.update("WATER") is False and a.armed                                     # stays armed
    a.disarm()
    assert not a.armed and a.count == 0


# ------------------------------------------------------------------ 20-25
def test_startup_with_water_already_present_does_not_speak(demo):                   # 20
    demo.connect()
    demo.frames(adc("O O O X O"), 300)                                               # hand open for 6 s
    d = demo.c.detection
    assert d.display == "WATER" and d.accepted                                       # still recognized + shown
    assert demo.engine.spoken == []                                                  # but NOT spoken
    assert not demo.c.speech_state.armed
    assert "SHOW WATER|Please give me some water." in demo.written()                 # OLED: sentence only
    assert "NOT SPOKEN" in demo.c.speech_state.last_message


def test_leaving_the_initial_gesture_arms_speech(demo):                             # 21
    demo.connect()
    demo.frames(adc("O O O X O"), 30)
    demo.frames(adc(NEUTRAL), 10)                                                    # too short
    assert not demo.c.speech_state.armed
    demo.frames(adc(NEUTRAL), 30)
    st = demo.c.speech_state
    assert st.armed and st.arm_count >= st.arm_needed == 25
    assert demo.engine.spoken == []                                                  # arming speaks nothing
    assert any("ARMED" in t for _, t in demo.c.log)


def test_water_after_arming_speaks_once_while_held_and_again_after_leaving(demo):   # 22-25
    demo.connect()
    demo.frames(adc("O O O X O"), 30)                                                # startup: silent
    demo.frames(adc(NEUTRAL), 40)                                                    # arm
    demo.frames(adc("O O O X O"), 300)                                               # 22 + 23: 6 s hold
    assert demo.engine.spoken == [WATER]                                             # 25 Urgent style
    demo.frames(adc(NEUTRAL), 20)
    demo.frames(adc("O O O X O"), 20)                                                # 24: return
    assert demo.engine.spoken == [WATER, WATER]
    demo.frames(adc("B B B X B"), 20)
    assert demo.engine.spoken[-1] == HUNGRY


def test_startup_in_a_neutral_hand_arms_without_extra_steps(demo):
    demo.connect()
    demo.frames(adc(NEUTRAL), 40)
    demo.frames(adc("B B B X B"), 20)
    assert demo.engine.spoken == [HUNGRY]


def test_going_straight_from_one_gesture_to_another_does_not_arm(demo):
    demo.connect()
    demo.frames(adc("O O O X O"), 30)
    demo.frames(adc("B B B X B"), 30)                                                # gesture -> gesture
    assert demo.c.detection.display == "HUNGRY" and demo.engine.spoken == []


# ------------------------------------------------------------------ every restart disarms
def test_stream_restart_disarms(demo):
    demo.connect()
    demo.frames(adc(NEUTRAL), 40)
    assert demo.c.speech_state.armed
    demo.clock.advance(2)                                                            # stream stops
    demo.c.tick()
    assert not demo.c.speech_state.armed
    demo.frames(adc("O O O X O"), 30)                                                # data again, hand open
    assert demo.c.detection.display == "WATER" and demo.engine.spoken == []


def test_new_calibration_or_gesture_change_disarms(demo):
    demo.connect()
    demo.frames(adc(NEUTRAL), 40)
    demo.c.set_calibration(demo.c.calibration.calibration)                           # recalibrated
    assert not demo.c.speech_state.armed
    demo.frames(adc(NEUTRAL), 40)
    g = demo.c.repo.by_name("SAD")
    demo.c.set_gesture_enabled(g.id, False)                                          # gesture set changed
    assert not demo.c.speech_state.armed


def test_esp32_trigger_mode_is_armed_too(tmp_path):
    rig = Rig(tmp_path, Settings(auto_connect=False, speech_trigger="esp32"), demo=True)
    rig.connect()
    rig.frames(adc("O O O X O"), 30)
    rig.send("# GESTURE: WATER\n")                                                   # firmware at boot, hand open
    assert rig.engine.spoken == []
    rig.frames(adc(NEUTRAL), 40)
    rig.send("# GESTURE: WATER\n")
    assert rig.engine.spoken == [WATER]
    rig.c.close()


def test_manual_speech_page_is_never_blocked(demo):
    assert not demo.c.speech_state.armed
    demo.c.sentence_editor.sentence = "Testing speech."
    assert demo.c.speak_sentence().ok
    assert demo.engine.spoken == [("Testing speech.", "Neutral")]


def test_arming_does_not_change_recognition_or_oled(demo):
    demo.connect()
    for name, (pattern, sentence, *_) in SPEC.items():                               # all shown while disarmed
        demo.frames(adc(pattern), 20)
        assert demo.c.detection.display == name and demo.c.detection.accepted
        assert f"SHOW {name}|{sentence}" in demo.written()
    assert demo.engine.spoken == [] and not demo.c.speech_state.armed


# ------------------------------------------------------------------ UI
def test_live_glove_shows_arming_and_ignored_ring(demo):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    QApplication.instance() or QApplication([])
    win = MainWindow(demo.c, start_timers=False)
    win.nav.setCurrentRow(PAGE_ORDER.index("Live Glove"))
    page = win.pages["Live Glove"]
    demo.connect()
    demo.frames(adc("O O O X O", "B"), 30)
    win.refresh()
    assert page.fingers.text() == "Thumb OPEN   Index OPEN   Middle OPEN   Ring IGNORED   Little OPEN"
    assert page.meters.state[3].text() == "IGNORED" and "BENT" in page.meters.state[3].toolTip()
    assert page.live_pattern.text() == "O O O B O"                                   # real state, not X
    assert page.saved_pattern.text() == "O O O X O" and page.live_detection.text() == "WATER"
    assert page.speech_arm.text().startswith("NOT ARMED")
    demo.frames(adc(NEUTRAL), 40)
    win.refresh()
    assert page.speech_arm.text().startswith("ARMED")
    win.close()


def test_ring_shown_when_a_gesture_uses_it(demo):
    from sign2speech_v2.core.patterns import GesturePattern
    from sign2speech_v2.gestures.repository import Gesture

    assert demo.c.ignored_fingers() == (3,)
    demo.c.repo.save(Gesture(name="RING_TEST", display_text="Ring", finger_pattern=GesturePattern.parse("BBBBO")))
    demo.c.reload_gestures()
    assert demo.c.ignored_fingers() == ()

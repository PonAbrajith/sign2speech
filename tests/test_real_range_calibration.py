"""Calibration for the glove's REAL ranges (Thumb ~0-500, Index/Middle/Little ~0-200, Ring ~0-10).

Every live value enters as a serial data line through the real pipeline (fake transport, never the
simulator). Thresholds must come from the captured open/closed values, never from absolute ADC numbers.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from sign2speech_v2.calibration.capture import DEAD, GOOD, WEAK, analyze_closed, analyze_open, assess
from sign2speech_v2.calibration.model import (PRELOADED_LABEL, Calibration, CalibrationStore, FingerCalibration,
                                              FingerStateClassifier)
from sign2speech_v2.codegen.generator import generate
from sign2speech_v2.config import pins
from sign2speech_v2.config.pins import FINGER_NAMES
from sign2speech_v2.config.settings import Settings
from sign2speech_v2.core.frame import Source
from sign2speech_v2.core.patterns import FingerState

from conftest import Rig

# the real glove: straight fingers read near 0, bent fingers only reach a few hundred counts
REAL_OPEN = (8, 4, 5, 2, 3)            # Thumb, Index, Middle, Ring, Little
REAL_CLOSED = (480, 190, 190, 10, 190)
NOISE = 2                              # ± counts on every reading

# name -> (pattern, sentence, style): unchanged by recalibration
BUILTINS = {
    "WATER": ("O O O X O", "Please give me some water.", "Urgent"),
    "HUNGRY": ("B B B X B", "I am feeling hungry.", "Sad"),
    "HAPPY": ("O B B X B", "I am very happy to see you.", "Happy"),
    "SAD": ("O B O X O", "I am feeling sad today.", "Sad"),
    "ANGRY": ("B O B X O", "Please don't touch my things.", "Angry"),
    "SCARED": ("B B O X O", "I am scared, please help me.", "Urgent"),
    "TIRED": ("O O O X B", "I am feeling tired.", "Sad"),
    "LEAVE_ME_ALONE": ("O O B X B", "Please leave me alone.", "Angry"),
    "NOT_WELL": ("B O O X B", "I am not feeling well.", "Calm"),
    "THANK_YOU": ("O B B X O", "Thank you for helping me.", "Happy"),
}


def rows(values, n=60, noise=NOISE, seed=1):
    rng = random.Random(seed)
    return [tuple(max(0, int(round(v + rng.uniform(-noise, noise)))) for v in values) for _ in range(n)]


def real_adc(live_pattern: str) -> tuple[int, ...]:
    letters = live_pattern.replace(" ", "")
    return tuple(REAL_CLOSED[i] if c == "B" else REAL_OPEN[i] for i, c in enumerate(letters))


def arm(rig) -> None:
    """Neutral hand (B O O O O: 4-finger code B O O O is unused) until gesture speech is ARMED."""
    rig.frames(real_adc("B O O O O"), 40)
    assert rig.c.speech_state.armed
    rig.engine.spoken.clear()


def capture(rig, kind: str, flex, seconds: float, seed: int) -> None:
    assert rig.c.start_capture(kind).ok
    for r in rows(flex, n=int(seconds / 0.02) + 10, seed=seed):
        rig.frames(r, 1)
    assert rig.c.calibration.capturing is None


@pytest.fixture
def glove(tmp_path):
    """Demo workspace (preloaded calibration + 10 built-ins) recalibrated from the real ranges via the UI flow."""
    rig = Rig(tmp_path, demo=True)
    rig.connect()
    rig.frames(REAL_OPEN, 10)                                                        # stream is live
    capture(rig, "open", REAL_OPEN, 2.0, seed=2)
    capture(rig, "closed", REAL_CLOSED, 3.0, seed=3)
    rig.engine.spoken.clear()          # the relaxed hand during CAPTURE OPEN was (correctly) WATER
    yield rig
    rig.c.close()


# ------------------------------------------------------------------ 1-3, 8 capture analysis
def test_realistic_0_to_200_finger_calibrates():                                    # 1
    c = analyze_closed(analyze_open(rows(REAL_OPEN)), rows(REAL_CLOSED))
    for name in ("Index", "Middle", "Little"):
        i = FINGER_NAMES.index(name)
        f, q = c.fingers[i], c.quality[i]
        assert q.status == GOOD and 180 <= f.range <= 190 and not f.inverted


def test_realistic_0_to_500_thumb_calibrates():                                     # 2
    c = analyze_closed(analyze_open(rows(REAL_OPEN)), rows(REAL_CLOSED))
    assert c.quality[0].status == GOOD and 465 <= c.fingers[0].range <= 480


def test_weak_ring_is_saved_with_a_visible_warning():                                # 3
    c = analyze_closed(analyze_open(rows(REAL_OPEN)), rows(REAL_CLOSED))
    assert c.ok, c.errors                                                            # not blocked
    ring = c.quality[FINGER_NAMES.index("Ring")]
    assert ring.status == WEAK and not ring.blocking
    assert any("WEAK SENSOR — RING RANGE IS VERY SMALL" in w for w in c.warnings)
    assert c.to_calibration().fingers[3].range < 20                                  # measured value kept


def test_the_same_weakness_on_a_primary_finger_blocks():
    closed = (480, 10, 190, 10, 190)                                                 # Index only moved ~6
    c = analyze_closed(analyze_open(rows(REAL_OPEN)), rows(closed))
    assert not c.ok and any("INDEX RANGE IS VERY SMALL" in e for e in c.errors)


def test_minimum_range_is_configurable():
    closed = (480, 40, 190, 10, 190)                                                 # Index moves ~36 counts
    o = analyze_open(rows(REAL_OPEN))
    assert analyze_closed(o, rows(closed), min_range=20).ok
    assert not analyze_closed(o, rows(closed), min_range=50).ok


def test_noise_still_rejects_a_noisy_primary_sensor():                               # 8
    o = analyze_open(rows((8, 20, 5, 2, 3), noise=25))
    c = analyze_closed(o, rows((480, 70, 190, 10, 190), noise=25))                   # 50 counts in ±25 noise
    assert not c.ok and c.quality[1].status == DEAD and any("noise" in e for e in c.errors)


def test_stuck_sensor_is_dead_and_explained():                                       # 8
    o = analyze_open(rows(REAL_OPEN))
    c = analyze_closed(o, rows((480, 190, 0, 10, 190), noise=0))                     # Middle never leaves 0
    q = c.quality[2]
    assert not c.ok and q.status == DEAD and "stuck at ADC 0" in q.reason


def test_zero_while_open_is_not_a_fault():
    o = analyze_open(rows((0, 0, 0, 0, 0), noise=0))
    assert o.ok                                                                      # decided at CLOSED
    assert analyze_closed(o, rows(REAL_CLOSED)).ok


# ------------------------------------------------------------------ 5-7 bending %, OPEN/BENT
def test_bending_percent_uses_actual_open_and_closed():                              # 5
    f = FingerCalibration("Index", 10, 190, 2.0)
    assert f.bend_fraction(10) == 0.0 and f.bend_fraction(190) == 1.0
    assert f.bend_fraction(100) == pytest.approx(0.5)
    assert f.bend_fraction(0) == 0.0 and f.bend_fraction(400) == 1.0                 # clamped


def test_open_bent_uses_calibrated_thresholds_with_hysteresis():                     # 6
    fingers = tuple(FingerCalibration(n, 10, 190, 2.0) for n in FINGER_NAMES)
    clf = FingerStateClassifier(Calibration(fingers, 0.5, 0.08))
    idx = lambda v: clf.update((10, v, 10, 10, 10))[1]
    assert idx(10) is FingerState.OPEN
    assert idx(110) is FingerState.OPEN          # below 10 + 0.58*180 = 114.4 -> stays OPEN (deadband)
    assert idx(120) is FingerState.BENT
    assert idx(90) is FingerState.BENT           # above 10 + 0.42*180 = 85.6 -> stays BENT (deadband)
    assert idx(80) is FingerState.OPEN


def test_no_absolute_adc_threshold():                                                # 7
    small = Calibration(tuple(FingerCalibration(n, 0, 200, 2.0) for n in FINGER_NAMES))
    large = Calibration(tuple(FingerCalibration(n, 181, 1090, 2.0) for n in FINGER_NAMES))
    reading = (150,) * 5
    assert FingerStateClassifier(small).pattern(reading).compact() == "BBBBB"      # 75 % of 0..200
    assert FingerStateClassifier(large).pattern(reading).compact() == "OOOOO"      # below its own open
    assert small.thresholds() == (100,) * 5 and large.thresholds() == (636,) * 5


# ------------------------------------------------------------------ controller flow
def test_capture_save_replaces_the_preloaded_calibration(glove):
    before = glove.c.calibration.calibration
    assert before.preloaded and before.label == PRELOADED_LABEL
    assert glove.c.calibration.closed_result.ok
    res = glove.c.save_calibration()
    assert res.ok and "Ring: WEAK SENSOR" in res.message
    cal = CalibrationStore(glove.ws.calibration_path).load()
    assert not cal.preloaded and cal.captured_at
    assert [f.open for f in cal.fingers] == pytest.approx(list(REAL_OPEN), abs=NOISE)
    assert [f.closed for f in cal.fingers] == pytest.approx(list(REAL_CLOSED), abs=NOISE)


def test_preloaded_label_from_earlier_release_is_marked_needs_real_calibration(tmp_path):
    store = CalibrationStore(tmp_path / "calibration.json")
    data = Calibration(tuple(FingerCalibration(n, 1, 100, 0.0) for n in FINGER_NAMES)).to_dict()
    data["label"] = "PRELOADED CALIBRATION"
    (tmp_path / "calibration.json").write_text(__import__("json").dumps(data), encoding="utf-8")
    assert store.load().label == "PRELOADED / NEEDS REAL CALIBRATION" and store.load().preloaded


def test_preloaded_calibration_keeps_fingers_open_at_real_ranges(tmp_path):
    """Why the fix is needed: the old closed values are never reached by this glove."""
    rig = Rig(tmp_path, demo=True)
    rig.connect()
    rig.frames(REAL_CLOSED, 20)                                                      # full fist
    assert str(rig.c.detection.live_pattern) == "O O O O O"
    rig.c.close()


def test_live_pattern_follows_each_calibrated_finger(glove):                         # 9
    glove.c.save_calibration()
    glove.frames(real_adc("O O O O O"), 20)
    assert str(glove.c.detection.live_pattern) == "O O O O O"
    for i, name in enumerate(FINGER_NAMES):
        if name == "Ring":
            continue
        bent = "".join("B" if j == i else "O" for j in range(5))
        glove.frames(real_adc(bent), 20)
        assert glove.c.detection.live_pattern.states[i] is FingerState.BENT, name
        glove.frames(real_adc("OOOOO"), 20)
        assert glove.c.detection.live_pattern.states[i] is FingerState.OPEN, name
    glove.frames(real_adc("B B B B B"), 20)
    assert str(glove.c.detection.live_pattern) == "B B B B B"
    pct = glove.c.live_sensor.bending
    assert all(p > 0.9 for p in pct)                                                 # % from the real range


@pytest.mark.parametrize("ring", ["O", "B"])
@pytest.mark.parametrize("name", list(BUILTINS))
def test_builtins_recognized_with_real_range_calibration(glove, name, ring):         # 4, 10, 12
    pattern, sentence, style = BUILTINS[name]
    glove.c.save_calibration()                   # new calibration: recognition restarts DISARMED
    arm(glove)
    glove.frames(real_adc(pattern.replace("X", ring)), 20)
    d = glove.c.detection
    assert d.display == name and d.matched_sentence == sentence
    assert glove.engine.spoken == [(sentence, style)]


def test_noisy_weak_ring_never_changes_the_detection(glove):                         # 4
    glove.c.save_calibration()
    arm(glove)
    rng = random.Random(7)
    base = list(real_adc("B B B B B"))
    for _ in range(200):                                                             # Ring wanders 0..12
        base[3] = rng.randint(0, 12)
        glove.frames(tuple(base), 1)
        if glove.c.detection.accepted:
            assert glove.c.detection.display == "HUNGRY"
    assert glove.engine.spoken == [("I am feeling hungry.", "Sad")]


def test_gesture_sentence_mapping_unchanged_by_calibration(glove):                   # 12
    before = {g.name: (g.finger_pattern.compact(), g.sentence_id, g.speech_style) for g in glove.c.repo.all()}
    glove.c.save_calibration()
    glove.c.reload_gestures()
    assert {g.name: (g.finger_pattern.compact(), g.sentence_id, g.speech_style) for g in glove.c.repo.all()} == before
    for g in glove.c.repo.all():
        assert glove.c.sentence_for(g).sentence == BUILTINS[g.name][1]


def test_real_mode_values_are_the_received_packets(glove):                           # 13, 14
    glove.c.save_calibration()
    glove.frames(real_adc("O B B B B"), 20)
    assert glove.c.acquisition.source is Source.REAL and glove.c.acquisition.simulator is None
    assert glove.c.live_sensor.raw == real_adc("O B B B B")
    assert glove.c.runtime_info()["Simulation active"] == "NO"
    before = dataclasses.asdict(glove.c.live_sensor)
    glove.c.save_calibration()                                                       # saving never writes live data
    assert dataclasses.asdict(glove.c.live_sensor)["raw"] == before["raw"]


def test_gpio_mapping_unchanged():                                                   # 11
    pins.verify_wiring()
    assert pins.FLEX_PINS == (32, 35, 34, 39, 36)
    assert (pins.OLED_SDA, pins.OLED_SCL, pins.MPU_SDA, pins.MPU_SCL) == (18, 19, 22, 21)
    assert pins.OLED_I2C_PORT != pins.MPU_I2C_PORT


def test_firmware_uses_the_captured_calibration(glove):
    glove.c.save_calibration()
    cal = glove.c.calibration.calibration
    fw = generate(glove.c.repo.all(), cal, Settings(), sentences=glove.c.sentence_texts())
    cfg = fw.files["config.h"]
    assert f"CAL_OPEN[FLEX_COUNT] = {{{', '.join(str(f.open) for f in cal.fingers)}}}" in cfg
    assert f"CAL_CLOSED[FLEX_COUNT] = {{{', '.join(str(f.closed) for f in cal.fingers)}}}" in cfg
    assert not fw.warnings


def test_saved_calibration_quality_shown_for_each_finger(glove):
    glove.c.save_calibration()
    q = [assess(f).status for f in glove.c.calibration.calibration.fingers]
    assert q == [GOOD, GOOD, GOOD, WEAK, GOOD]


def test_calibration_page_shows_quality_and_derived_thresholds(glove):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    QApplication.instance() or QApplication([])
    win = MainWindow(glove.c, start_timers=False)
    win.nav.setCurrentRow(PAGE_ORDER.index("Calibration"))
    win.refresh()
    page = win.pages["Calibration"]
    t = page.table
    heads = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    assert heads == ["Finger", "Open", "Closed", "Range", "Noise", "Quality", "Threshold", "Inverted"]
    assert "Captured — NOT SAVED" in page.which.text()
    assert page.btn_save.isEnabled()                                                 # weak Ring does not block
    assert t.item(3, 5).text() == "WEAK SENSOR" and "RING RANGE IS VERY SMALL" in t.item(3, 5).toolTip()
    assert t.item(1, 5).text() == "GOOD"
    assert "WEAK SENSOR — RING RANGE IS VERY SMALL" in page.messages.text()
    f = glove.c.calibration.closed_result.fingers[1]
    assert t.item(1, 6).text() == f"OPEN < {f.threshold(0.42)} · BENT > {f.threshold(0.58)}"
    glove.c.save_calibration()
    win.refresh()
    assert "PRELOADED" not in page.which.text()
    win.close()


def test_calibration_page_marks_preloaded_values(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    rig = Rig(tmp_path, demo=True)
    QApplication.instance() or QApplication([])
    win = MainWindow(rig.c, start_timers=False)
    win.nav.setCurrentRow(PAGE_ORDER.index("Calibration"))
    win.refresh()
    assert "PRELOADED / NEEDS REAL CALIBRATION" in win.pages["Calibration"].which.text()
    assert "NOT verified" in win.pages["Calibration"].which.text()
    win.close()
    rig.c.close()

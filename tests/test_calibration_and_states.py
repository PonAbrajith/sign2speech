"""Filtering, calibration capture/sanity checks, OPEN/BENT with hysteresis, patterns."""

import random

import pytest

from sign2speech_v2.calibration.capture import analyze_closed, analyze_open
from sign2speech_v2.calibration.model import (Calibration, CalibrationStore, FingerCalibration,
                                              FingerStateClassifier)
from sign2speech_v2.config.pins import FINGER_NAMES
from sign2speech_v2.core.patterns import (FingerState, GesturePattern, LivePattern, PatternError, PatternToken)
from sign2speech_v2.processing.filter import FlexFilter

from conftest import make_calibration


def rows(values, n=60, noise=3, seed=1):
    rng = random.Random(seed)
    return [tuple(int(v + rng.uniform(-noise, noise)) for v in values) for _ in range(n)]


# -------------------------------------------------------------------- filter
def test_moving_average_filter():
    f = FlexFilter(window=4)
    assert f.update((100, 0, 0, 0, 0))[0] == 100
    f.update((200, 0, 0, 0, 0))
    f.update((300, 0, 0, 0, 0))
    assert f.update((400, 0, 0, 0, 0))[0] == 250
    assert f.update((500, 0, 0, 0, 0))[0] == 350      # oldest value dropped
    f.reset()
    assert f.update((10, 10, 10, 10, 10)) == (10, 10, 10, 10, 10)


# --------------------------------------------------------------- calibration
def test_calibration_from_clean_open_and_closed_captures():
    o = analyze_open(rows((800, 700, 750, 720, 690)))
    assert o.ok and o.open == pytest.approx((800, 700, 750, 720, 690), abs=2)
    c = analyze_closed(o, rows((2400, 2300, 2500, 2200, 2100)))
    assert c.ok, c.errors
    cal = c.to_calibration(0.5, 0.08)
    thumb = cal.fingers[0]
    assert thumb.range == pytest.approx(1600, abs=5) and not thumb.inverted
    assert cal.thresholds()[0] == pytest.approx(1600, abs=5)
    assert all(f.noise >= 2.0 for f in cal.fingers)


def test_inverted_sensor_is_supported():
    o = analyze_open(rows((2500,) * 5))
    c = analyze_closed(o, rows((1200,) * 5))
    assert c.ok and all(f.inverted for f in c.fingers)


def test_small_but_clean_range_is_accepted_no_fixed_50_count_rule():
    o = analyze_open(rows((1000,) * 5, noise=1))
    c = analyze_closed(o, rows((1030,) * 5, noise=1))   # 30 counts, noise ~1
    assert c.ok, c.errors


def test_noisy_small_range_is_rejected():
    o = analyze_open(rows((1000,) * 5, noise=40))
    c = analyze_closed(o, rows((1060,) * 5, noise=40))
    assert not c.ok and any("noise" in e for e in c.errors)


def test_sensor_that_does_not_move_is_rejected():
    o = analyze_open(rows((1000,) * 5, noise=1))
    c = analyze_closed(o, rows((1005,) * 5, noise=1))
    assert not c.ok and any("RANGE IS VERY SMALL" in e for e in c.errors)       # primary fingers block


def test_stuck_at_zero_detected():
    # 0 while open is allowed on this glove; stuck = still 0 when bent
    o = analyze_open(rows((0, 800, 800, 800, 800), noise=0))
    assert o.ok and any("Thumb" in w and "reads ADC 0 while open" in w for w in o.warnings)
    c = analyze_closed(o, rows((0, 2000, 2000, 2000, 2000), noise=0))
    assert not c.ok and "Thumb" in c.errors[0] and "stuck at ADC 0" in c.errors[0]
    assert c.quality[0].status == "DEAD / INVALID"


def test_stuck_at_4095_detected():
    o = analyze_open(rows((800, 4095, 800, 800, 800), noise=0))
    assert o.ok and any("Index" in w and "4095" in w for w in o.warnings)
    c = analyze_closed(o, rows((2000, 4095, 2000, 2000, 2000), noise=0))
    assert not c.ok and any("Index" in e and "stuck at ADC 4095" in e for e in c.errors)


def test_all_zero_reports_check_flex_wiring():
    o = analyze_open(rows((0,) * 5, noise=0))
    assert any("ALL ADC = 0" in w and "CHECK FLEX WIRING" in w for w in o.warnings)
    c = analyze_closed(o, rows((0,) * 5, noise=0))                              # nothing moved: dead
    assert not c.ok and sum("stuck at ADC 0" in e for e in c.errors) == 4       # 4 primary fingers
    assert any("Ring" in w and "stuck at ADC 0" in w for w in c.warnings)       # Ring reported, not hidden


def test_too_few_frames_rejected():
    assert not analyze_open(rows((800,) * 5, n=5)).ok


def test_calibration_values_are_never_altered():
    data = rows((800, 700, 750, 720, 690), noise=0)
    before = [tuple(r) for r in data]
    analyze_open(data)
    assert data == before


def test_calibration_store_roundtrip(tmp_path):
    store = CalibrationStore(tmp_path / "calibration.json")
    assert store.load() is None
    cal = make_calibration()
    store.save(cal)
    assert store.load() == cal
    store.clear()
    assert store.load() is None


def test_calibration_requires_finger_order():
    fingers = tuple(FingerCalibration(n, 1, 2, 1) for n in reversed(FINGER_NAMES))
    with pytest.raises(ValueError):
        Calibration(fingers)


# ----------------------------------------------------------- OPEN / BENT
def test_open_bent_classification_and_bending_percent():
    clf = FingerStateClassifier(make_calibration(700, 2600))
    states = clf.update((700, 2600, 1000, 2400, 700))
    assert states == (FingerState.OPEN, FingerState.BENT, FingerState.OPEN, FingerState.BENT, FingerState.OPEN)
    assert clf.bending((700, 2600, 1650, 5000, 0)) == pytest.approx((0, 1, 0.5, 1, 0))


def test_hysteresis_prevents_flicker():
    cal = make_calibration(0, 1000)          # threshold 500, hysteresis 0.08 -> 420..580
    clf = FingerStateClassifier(cal)
    seq = [490, 510, 495, 530, 560, 575, 505, 470]
    states = [clf.update((v,) * 5)[0] for v in seq]
    assert set(states) == {FingerState.OPEN}          # never crosses 580
    assert clf.update((590,) * 5)[0] is FingerState.BENT
    assert clf.update((450,) * 5)[0] is FingerState.BENT  # stays bent above 420
    assert clf.update((410,) * 5)[0] is FingerState.OPEN


def test_inverted_classification():
    clf = FingerStateClassifier(make_calibration(2600, 700))
    assert clf.update((2600, 700, 2600, 700, 2600)) == tuple(
        FingerState(c) for c in "OBOBO")


# ------------------------------------------------------------------ patterns
def test_live_pattern_never_contains_x():
    with pytest.raises(PatternError):
        LivePattern.parse("O B X B O")
    lp = LivePattern.parse("O B O B O")
    assert str(lp) == "O B O B O" and "X" not in lp.compact()


def test_gesture_pattern_parsing_and_x_matching():
    gp = GesturePattern.parse("O B X B O")
    assert gp.compact() == "OBXBO"
    assert gp.matches(LivePattern.parse("OBOBO"))
    assert gp.matches(LivePattern.parse("OBBBO"))
    assert not gp.matches(LivePattern.parse("BBOBO"))
    with pytest.raises(PatternError):
        GesturePattern.parse("OBOB")
    with pytest.raises(PatternError):
        GesturePattern.parse("OBOBZ")


def test_capture_snapshot_is_independent_of_later_changes():
    live = LivePattern.parse("OBOBO")
    snap = GesturePattern.from_live(live)
    assert snap.tokens == (PatternToken.OPEN, PatternToken.BENT, PatternToken.OPEN, PatternToken.BENT,
                           PatternToken.OPEN)
    assert isinstance(snap, GesturePattern) and not isinstance(snap, LivePattern)

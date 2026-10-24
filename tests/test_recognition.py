"""Pattern matcher, ML placeholder and the stability gate."""

import pytest

from sign2speech_v2.core.patterns import GesturePattern, LivePattern
from sign2speech_v2.gestures.repository import Gesture
from sign2speech_v2.recognition.recognizer import FingerPatternRecognizer, MatchStatus, MLRecognizer
from sign2speech_v2.recognition.stability import StabilityGate


def g(name, pattern, enabled=True):
    return Gesture(name=name, display_text=name.title(), finger_pattern=GesturePattern.parse(pattern),
                   enabled=enabled)


def live(p):
    return LivePattern.parse(p)


def test_no_saved_gestures_is_unknown():
    r = FingerPatternRecognizer().match(live("OBOBO"))
    assert r.status is MatchStatus.UNKNOWN and r.label == "UNKNOWN"


def test_unique_match():
    rec = FingerPatternRecognizer([g("HELLO", "OBOBO"), g("WATER", "BOOOO")])
    r = rec.match(live("OBOBO"))
    assert r.status is MatchStatus.MATCH and r.gesture.name == "HELLO" and r.label == "HELLO"
    assert rec.match(live("BBBBB")).status is MatchStatus.UNKNOWN


def test_x_matches_open_or_bent():
    rec = FingerPatternRecognizer([g("YES", "XBBBB")])
    assert rec.match(live("OBBBB")).label == "YES"
    assert rec.match(live("BBBBB")).label == "YES"
    assert rec.match(live("BOBBB")).label == "UNKNOWN"


def test_ambiguous_never_guesses():
    rec = FingerPatternRecognizer([g("A", "XBOBO"), g("B", "OBXBO")])
    r = rec.match(live("OBOBO"))
    assert r.status is MatchStatus.AMBIGUOUS and r.gesture is None
    assert set(r.candidates) == {"A", "B"} and r.label == "AMBIGUOUS"


def test_disabled_gestures_are_ignored():
    rec = FingerPatternRecognizer([g("HELLO", "OBOBO", enabled=False)])
    assert rec.match(live("OBOBO")).status is MatchStatus.UNKNOWN


def test_ml_recognizer_is_a_placeholder_that_never_matches():
    ml = MLRecognizer()
    assert not ml.available
    assert "not" in FingerPatternRecognizer.description.lower() and "machine learning" in \
        FingerPatternRecognizer.description.lower()


# -------------------------------------------------------------- stability
def feed(gate, key, n):
    return [gate.update(key) for _ in range(n)]


def test_gesture_requires_hold_samples():
    gate = StabilityGate(hold_samples=10, release_samples=5)
    ups = feed(gate, "HELLO", 9)
    assert all(u.accepted is None for u in ups)
    assert gate.update("HELLO").accepted == "HELLO"


def test_single_frame_is_not_recognized():
    gate = StabilityGate(10, 5)
    assert gate.update("HELLO").accepted is None
    assert all(u.accepted is None for u in feed(gate, None, 20))


def test_holding_does_not_repeat():
    gate = StabilityGate(10, 5)
    ups = feed(gate, "HELLO", 200)
    assert [u.accepted for u in ups if u.accepted] == ["HELLO"]


def test_must_leave_and_perform_again_to_repeat():
    gate = StabilityGate(10, 5)
    feed(gate, "HELLO", 10)
    feed(gate, None, 3)                       # brief glitch: not a release
    assert all(u.accepted is None for u in feed(gate, "HELLO", 30))
    ups = feed(gate, None, 5)                 # left the gesture
    assert ups[-1].released == "HELLO"
    again = feed(gate, "HELLO", 10)
    assert again[-1].accepted == "HELLO"


def test_flicker_resets_the_hold_count():
    gate = StabilityGate(10, 5)
    for _ in range(5):
        feed(gate, "HELLO", 6)
        feed(gate, "WATER", 1)
    assert gate.latched is None


def test_switching_directly_to_another_gesture():
    gate = StabilityGate(10, 3)
    feed(gate, "HELLO", 10)
    ups = feed(gate, "WATER", 13)
    assert [u.accepted for u in ups if u.accepted] == ["WATER"]


@pytest.mark.parametrize("hold,release", [(0, 5), (10, 0)])
def test_invalid_gate_settings(hold, release):
    with pytest.raises(ValueError):
        StabilityGate(hold, release)

"""Speech abstraction: engines, styles, speak-once service, and real Windows speech."""

import sys
import wave

import pytest

from sign2speech_v2.core.patterns import GesturePattern
from sign2speech_v2.gestures.repository import SPEECH_STYLES, Gesture
from sign2speech_v2.speech.engine import STYLE_VOICES, NullEngine, SapiEngine, create_engine
from sign2speech_v2.speech.service import SpeechService
from sign2speech_v2.state.states import SpeechState

from conftest import RecordingEngine

HELLO = Gesture(name="HELLO", display_text="Hello", finger_pattern=GesturePattern.parse("OBOBO"),
                speech_style="Happy")


def test_every_speech_style_has_a_voice_preset():
    assert set(SPEECH_STYLES) == set(STYLE_VOICES) == {"Neutral", "Happy", "Sad", "Angry", "Calm", "Urgent",
                                                       "Custom"}


def test_null_engine_never_claims_to_speak():
    r = NullEngine("none").speak("Hello")
    assert not r.ok and "NOT spoken" in r.message


def test_service_speaks_display_text_once_per_acceptance():
    eng = RecordingEngine()
    st = SpeechState(enabled=True)
    svc = SpeechService(eng, st)
    svc.on_accepted(HELLO, 1)
    svc.on_accepted(HELLO, 1)          # same acceptance delivered twice
    assert eng.spoken == [("Hello", "Happy")]
    svc.on_accepted(HELLO, 2)          # a new acceptance
    assert eng.spoken == [("Hello", "Happy")] * 2
    assert st.last_spoken_gesture == "HELLO" and st.last_text == "Hello" and st.spoken_count == 2


def test_first_esp32_event_is_not_swallowed():
    eng = RecordingEngine()
    svc = SpeechService(eng, SpeechState(enabled=True))
    svc.on_accepted(HELLO, -1)
    assert eng.spoken == [("Hello", "Happy")]


def test_service_disabled():
    eng = RecordingEngine()
    svc = SpeechService(eng, SpeechState(enabled=False))
    svc.on_accepted(HELLO, 1)
    assert eng.spoken == []


def test_failed_speech_is_not_recorded_as_spoken():
    st = SpeechState(enabled=True)
    SpeechService(NullEngine("x"), st).on_accepted(HELLO, 1)
    assert st.last_spoken_gesture is None and st.spoken_count == 0


def test_env_can_disable_engine(monkeypatch):
    monkeypatch.setenv("S2S_SPEECH", "none")
    assert not create_engine("auto").available


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SAPI")
def test_windows_sapi_really_synthesizes_hello(tmp_path):
    """Real local Windows speech (offline). Rendered to a WAV file so the test is silent."""
    wav = tmp_path / "hello.wav"
    eng = SapiEngine(wav_path=str(wav))
    assert eng.available
    r = eng.speak("Hello", "Neutral")
    assert r.ok
    assert eng.wait_idle(60), "speech worker did not finish"
    eng.close()
    assert eng.last_error == ""
    with wave.open(str(wav)) as w:
        seconds = w.getnframes() / w.getframerate()
    assert 0.2 < seconds < 5.0          # "Hello" is a short, real utterance

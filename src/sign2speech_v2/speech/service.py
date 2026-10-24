"""SpeechService - speaks an accepted gesture's display_text exactly once.

It is called only for ACCEPTED gestures (after the stability gate), never
per frame. Each acceptance carries an event number; the same event is never
spoken twice even if delivered twice.
"""

from __future__ import annotations

from sign2speech_v2.gestures.repository import Gesture
from sign2speech_v2.speech.engine import SpeakResult, SpeechEngine
from sign2speech_v2.state.states import SpeechState


class SpeechService:
    def __init__(self, engine: SpeechEngine, state: SpeechState) -> None:
        self.engine = engine
        self.state = state
        self._last_event: int | None = None

    def set_engine(self, engine: SpeechEngine) -> None:
        self.engine.close()
        self.engine = engine
        self.state.engine_name = engine.name
        self.state.available = engine.available

    def on_accepted(self, gesture: Gesture, event_id: int, text: str | None = None) -> SpeakResult | None:
        """Speak an accepted gesture once: its mapped sentence (`text`) if any, else its display text,
        in the gesture's default speech style."""
        if event_id == self._last_event:
            return None
        self._last_event = event_id
        if not self.state.enabled:
            self.state.last_message = f"Speech is off: {(text or gesture.display_text)!r} not spoken"
            return None
        spoken = text or gesture.display_text
        result = self.engine.speak(spoken, gesture.speech_style)
        self.state.record(gesture.name, spoken, result.ok, result.message, gesture.speech_style)
        return result

    def say(self, text: str, style: str = "Neutral") -> SpeakResult:
        """Manual speech from the Speech page (sentence + selected style); not tied to recognition."""
        result = self.engine.speak(text, style)
        self.state.record(None, text.strip(), result.ok, result.message, style)
        return result

    def refresh(self) -> None:
        self.state.speaking = self.engine.speaking

    def close(self) -> None:
        self.engine.close()

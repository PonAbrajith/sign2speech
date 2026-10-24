"""Recognition engines behind one interface.

FingerPatternRecognizer is deterministic rule matching (O/B/X per finger).
It is NOT machine learning and does not claim to be. MLRecognizer is the
adapter slot for a trained model later; it takes the same RecognitionInput
(which already carries the raw/filtered sensor values and MPU data), so a
model can replace the pattern matcher without touching acquisition.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from sign2speech_v2.core.frame import SensorFrame
from sign2speech_v2.core.patterns import LivePattern
from sign2speech_v2.gestures.repository import Gesture


class MatchStatus(str, Enum):
    MATCH = "MATCH"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_READY = "NOT READY"     # no live data / not calibrated / engine unavailable


@dataclass(frozen=True)
class RecognitionInput:
    live_pattern: LivePattern
    frame: SensorFrame
    filtered: tuple[float, ...]
    bending: tuple[float, ...]


@dataclass(frozen=True)
class RecognitionResult:
    status: MatchStatus
    gesture: Gesture | None = None
    candidates: tuple[str, ...] = ()
    detail: str = ""

    @property
    def label(self) -> str:
        """Gesture name for a MATCH, otherwise the status word (UNKNOWN / AMBIGUOUS / NOT READY)."""
        return self.gesture.name if self.status is MatchStatus.MATCH and self.gesture else self.status.value


class Recognizer(ABC):
    name: str = "recognizer"
    description: str = ""

    @abstractmethod
    def set_gestures(self, gestures: Sequence[Gesture]) -> None:
        """Replace the enabled gesture set (called whenever the library changes)."""

    @abstractmethod
    def recognize(self, data: RecognitionInput) -> RecognitionResult: ...


class FingerPatternRecognizer(Recognizer):
    name = "FINGER_PATTERN"
    description = "Deterministic finger-pattern matching (O = open, B = bent, X = either). Not machine learning."

    def __init__(self, gestures: Sequence[Gesture] = ()) -> None:
        self._gestures: tuple[Gesture, ...] = ()
        self.set_gestures(gestures)

    def set_gestures(self, gestures: Sequence[Gesture]) -> None:
        self._gestures = tuple(g for g in gestures if g.enabled)

    @property
    def gestures(self) -> tuple[Gesture, ...]:
        return self._gestures

    def recognize(self, data: RecognitionInput) -> RecognitionResult:
        return self.match(data.live_pattern)

    def match(self, live: LivePattern) -> RecognitionResult:
        if not self._gestures:
            return RecognitionResult(MatchStatus.UNKNOWN, detail="no enabled gestures stored")
        hits = [g for g in self._gestures if g.finger_pattern.matches(live)]
        if len(hits) == 1:
            return RecognitionResult(MatchStatus.MATCH, hits[0], (hits[0].name,))
        if not hits:
            return RecognitionResult(MatchStatus.UNKNOWN, detail=f"no stored pattern matches {live}")
        names = tuple(g.name for g in hits)
        return RecognitionResult(MatchStatus.AMBIGUOUS, candidates=names,
                                 detail=f"{live} matches {', '.join(names)}: make their patterns distinct")


class MLRecognizer(Recognizer):
    """Placeholder for a trained model. No model exists yet, so it never
    returns a match - it reports NOT READY instead of guessing."""

    name = "ML"
    description = "Machine-learning recognizer (not trained yet - future adapter)."

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path
        self._gestures: tuple[Gesture, ...] = ()

    @property
    def available(self) -> bool:
        return False

    def set_gestures(self, gestures: Sequence[Gesture]) -> None:
        self._gestures = tuple(gestures)

    def recognize(self, data: RecognitionInput) -> RecognitionResult:
        return RecognitionResult(MatchStatus.NOT_READY, detail="no trained model available")

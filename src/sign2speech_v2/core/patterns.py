"""Finger states and the two kinds of pattern.

LivePattern    - what the glove is doing NOW. Only O (OPEN) and B (BENT).
                 It can never contain X: X is not a sensor state.
GesturePattern - what a stored gesture requires. O, B or X (ANY).

They are different types on purpose, so a stored pattern can never be
passed where a live one is expected (and vice versa).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sign2speech_v2.config.pins import FINGER_NAMES

FINGER_COUNT = len(FINGER_NAMES)


class FingerState(str, Enum):
    OPEN = "O"
    BENT = "B"

    @property
    def word(self) -> str:
        return "OPEN" if self is FingerState.OPEN else "BENT"


class PatternToken(str, Enum):
    OPEN = "O"
    BENT = "B"
    ANY = "X"

    def accepts(self, state: FingerState) -> bool:
        return self is PatternToken.ANY or self.value == state.value


class PatternError(ValueError):
    pass


def _letters(text: str) -> list[str]:
    return [c for c in text.upper() if not c.isspace() and c not in ",-_|"]


@dataclass(frozen=True, slots=True)
class LivePattern:
    states: tuple[FingerState, ...]

    def __post_init__(self) -> None:
        if len(self.states) != FINGER_COUNT:
            raise PatternError(f"a live pattern has {FINGER_COUNT} fingers, got {len(self.states)}")
        if not all(isinstance(s, FingerState) for s in self.states):
            raise PatternError("a live pattern contains only OPEN/BENT finger states")

    @classmethod
    def parse(cls, text: str) -> "LivePattern":
        letters = _letters(text)
        if any(c not in "OB" for c in letters):
            raise PatternError(f"live pattern may contain only O and B, got {text!r}")
        return cls(tuple(FingerState(c) for c in letters))

    def compact(self) -> str:
        return "".join(s.value for s in self.states)

    def __str__(self) -> str:
        return " ".join(s.value for s in self.states)


@dataclass(frozen=True, slots=True)
class GesturePattern:
    tokens: tuple[PatternToken, ...]

    def __post_init__(self) -> None:
        if len(self.tokens) != FINGER_COUNT:
            raise PatternError(f"a gesture pattern has {FINGER_COUNT} fingers, got {len(self.tokens)}")
        if not all(isinstance(t, PatternToken) for t in self.tokens):
            raise PatternError("gesture pattern tokens must be O, B or X")

    @classmethod
    def parse(cls, text: str) -> "GesturePattern":
        letters = _letters(text)
        if any(c not in "OBX" for c in letters):
            raise PatternError(f"gesture pattern may contain only O, B and X, got {text!r}")
        return cls(tuple(PatternToken(c) for c in letters))

    @classmethod
    def from_live(cls, live: LivePattern) -> "GesturePattern":
        """Snapshot of the CURRENT live pattern (capture from live glove)."""
        return cls(tuple(PatternToken(s.value) for s in live.states))

    @property
    def all_any(self) -> bool:
        return all(t is PatternToken.ANY for t in self.tokens)

    def matches(self, live: LivePattern) -> bool:
        return all(t.accepts(s) for t, s in zip(self.tokens, live.states, strict=True))

    def overlaps(self, other: "GesturePattern") -> bool:
        """True if some real hand (O/B per finger) would match BOTH patterns -> AMBIGUOUS."""
        return all(a is PatternToken.ANY or b is PatternToken.ANY or a is b
                   for a, b in zip(self.tokens, other.tokens, strict=True))

    def compact(self) -> str:
        return "".join(t.value for t in self.tokens)

    def __str__(self) -> str:
        return " ".join(t.value for t in self.tokens)

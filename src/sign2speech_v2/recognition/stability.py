"""Gesture stability / debounce.

A gesture is ACCEPTED only after the same MATCH has been seen for
`hold_samples` consecutive frames (default 10 at ~50 Hz = ~0.2 s).
An accepted gesture fires exactly once. Holding the sign keeps it latched;
it can fire again only after the hand has left it for `release_samples`
consecutive frames and then performed it again. The firmware implements
the same state machine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateUpdate:
    accepted: str | None = None     # gesture name accepted on THIS frame (fires once)
    released: str | None = None     # latched gesture released on THIS frame


class StabilityGate:
    def __init__(self, hold_samples: int = 10, release_samples: int = 5) -> None:
        if hold_samples < 1 or release_samples < 1:
            raise ValueError("hold_samples and release_samples must be >= 1")
        self.hold_samples = hold_samples
        self.release_samples = release_samples
        self.reset()

    def reset(self) -> None:
        self.candidate: str | None = None   # current key (gesture name, or None for no match)
        self.count = 0
        self.latched: str | None = None
        self._away = 0

    def update(self, gesture: str | None) -> GateUpdate:
        """Feed one frame's result: a gesture name for a unique MATCH, else None."""
        released = None
        if self.latched is not None:
            if gesture == self.latched:
                self._away = 0
                return GateUpdate()
            self._away += 1
            if self._away < self.release_samples:
                return GateUpdate()
            released, self.latched, self._away = self.latched, None, 0
            self.candidate, self.count = None, 0
        if gesture == self.candidate:
            self.count += 1
        else:
            self.candidate, self.count = gesture, 1
        if gesture is not None and self.count >= self.hold_samples:
            self.latched = gesture
            self._away = 0
            return GateUpdate(accepted=gesture, released=released)
        return GateUpdate(released=released)


class SpeechArming:
    """STARTUP ARMING: automatic gesture speech starts DISARMED.

    Whenever recognition (re)starts - Studio start, the stream (re)connecting, a new calibration or a
    changed gesture set - the hand may already be in a gesture. That gesture is still shown, but not
    spoken. Speech arms only after the live result has been NO MATCH (UNKNOWN / AMBIGUOUS: a neutral hand
    shape) for `neutral_samples` consecutive frames; from then on every accepted gesture is spoken once.
    Recognition itself (patterns, the stability gate) is not changed.
    """

    def __init__(self, neutral_samples: int = 25) -> None:
        if neutral_samples < 1:
            raise ValueError("neutral_samples must be >= 1")
        self.neutral_samples = neutral_samples
        self.disarm()

    def disarm(self) -> None:
        self.armed = False
        self.count = 0

    def update(self, gesture: str | None) -> bool:
        """Feed one frame's result (gesture name for a MATCH, else None). True on the frame it arms."""
        if self.armed:
            return False
        self.count = self.count + 1 if gesture is None else 0
        if self.count >= self.neutral_samples:
            self.armed = True
            return True
        return False

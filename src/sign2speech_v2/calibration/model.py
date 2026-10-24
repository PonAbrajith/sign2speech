"""Calibration data (per finger open/closed/range/threshold/inverted/noise)
and the OPEN/BENT classifier with hysteresis.

Calibration is stored in workspace/calibration.json, separate from gestures.
Without a calibration there are no finger states: raw values are never
turned into OPEN/BENT directly (the direction and span are unknown).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from sign2speech_v2.config.pins import FINGER_NAMES
from sign2speech_v2.core.patterns import FingerState, LivePattern


@dataclass(frozen=True)
class FingerCalibration:
    finger: str
    open: int
    closed: int
    noise: float

    @property
    def range(self) -> int:
        return abs(self.closed - self.open)

    @property
    def inverted(self) -> bool:
        """True when bending LOWERS the ADC reading."""
        return self.closed < self.open

    def threshold(self, fraction: float) -> int:
        """ADC value at `fraction` of the way from open to closed."""
        return int(round(self.open + fraction * (self.closed - self.open)))

    def bend_fraction(self, value: float) -> float:
        """0.0 = open, 1.0 = closed, clamped."""
        if self.closed == self.open:
            return 0.0
        f = (value - self.open) / (self.closed - self.open)
        return min(1.0, max(0.0, f))


# Values seeded from an earlier measurement, NOT captured from the glove as it behaves now.
PRELOADED_LABEL = "PRELOADED / NEEDS REAL CALIBRATION"
_LEGACY_LABELS = {"PRELOADED CALIBRATION": PRELOADED_LABEL}   # label written by earlier releases


@dataclass(frozen=True)
class Calibration:
    fingers: tuple[FingerCalibration, ...]
    bend_threshold: float = 0.5
    hysteresis: float = 0.08
    captured_at: str = ""
    label: str = ""          # PRELOADED_LABEL for seeded values; empty for a calibration captured in the Studio

    @property
    def preloaded(self) -> bool:
        return self.label == PRELOADED_LABEL

    def __post_init__(self) -> None:
        if len(self.fingers) != len(FINGER_NAMES):
            raise ValueError(f"calibration needs {len(FINGER_NAMES)} fingers, got {len(self.fingers)}")
        if [f.finger for f in self.fingers] != list(FINGER_NAMES):
            raise ValueError("calibration fingers must be in order Thumb, Index, Middle, Ring, Little")
        if not 0.05 <= self.bend_threshold <= 0.95:
            raise ValueError("bend threshold must be between 0.05 and 0.95")
        if not 0.0 <= self.hysteresis < min(self.bend_threshold, 1 - self.bend_threshold):
            raise ValueError("hysteresis must be >= 0 and smaller than the distance to 0 and 1")

    def thresholds(self) -> tuple[int, ...]:
        return tuple(f.threshold(self.bend_threshold) for f in self.fingers)

    def to_dict(self) -> dict:
        return {"fingers": [asdict(f) for f in self.fingers], "bend_threshold": self.bend_threshold,
                "hysteresis": self.hysteresis, "captured_at": self.captured_at, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict) -> "Calibration":
        fingers = tuple(FingerCalibration(str(f["finger"]), int(f["open"]), int(f["closed"]), float(f["noise"]))
                        for f in data["fingers"])
        return cls(fingers, float(data.get("bend_threshold", 0.5)), float(data.get("hysteresis", 0.08)),
                   str(data.get("captured_at", "")),
                   _LEGACY_LABELS.get(str(data.get("label", "")), str(data.get("label", ""))))


class CalibrationStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Calibration | None:
        if not self.path.exists():
            return None
        try:
            return Calibration.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, calibration: Calibration) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(calibration.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


class FingerStateClassifier:
    """Filtered ADC -> bend fraction -> OPEN/BENT, with hysteresis per finger.

    A finger switches to BENT only above threshold + hysteresis and back to
    OPEN only below threshold - hysteresis, so noise near the threshold
    cannot make the state flicker.
    """

    def __init__(self, calibration: Calibration) -> None:
        self.calibration = calibration
        self._states: list[FingerState | None] = [None] * len(FINGER_NAMES)

    def reset(self) -> None:
        self._states = [None] * len(FINGER_NAMES)

    def bending(self, filtered: Sequence[float]) -> tuple[float, ...]:
        return tuple(f.bend_fraction(v) for f, v in zip(self.calibration.fingers, filtered, strict=True))

    def update(self, filtered: Sequence[float]) -> tuple[FingerState, ...]:
        t, h = self.calibration.bend_threshold, self.calibration.hysteresis
        out = []
        for i, frac in enumerate(self.bending(filtered)):
            prev = self._states[i]
            if prev is FingerState.BENT:
                state = FingerState.OPEN if frac < t - h else FingerState.BENT
            elif prev is FingerState.OPEN:
                state = FingerState.BENT if frac > t + h else FingerState.OPEN
            else:
                state = FingerState.BENT if frac >= t else FingerState.OPEN
            self._states[i] = state
            out.append(state)
        return tuple(out)

    def pattern(self, filtered: Sequence[float]) -> LivePattern:
        return LivePattern(self.update(filtered))

"""Simulated glove - used ONLY when the user presses Start Simulation.

Produces frames tagged Source.SIMULATION at ~50 Hz. The per-finger bend
(0..1) is set from the UI. The simulated sensor reads ~700 open and ~2600
fully bent, plus small noise.
"""

from __future__ import annotations

import math
import random

from sign2speech_v2.core.frame import SensorFrame, Source

SIM_OPEN = 700
SIM_CLOSED = 2600
SIM_RATE_HZ = 50.0


class SimulatedGlove:
    def __init__(self, seed: int | None = None, rate_hz: float = SIM_RATE_HZ) -> None:
        self.bend = [0.0] * 5
        self.period = 1.0 / rate_hz
        self._rng = random.Random(seed)
        self._next_at: float | None = None
        self._t = 0.0

    def set_bend(self, finger: int, fraction: float) -> None:
        self.bend[finger] = min(1.0, max(0.0, fraction))

    def set_pattern(self, compact: str) -> None:
        """'OBOBO' -> bends (convenience for the UI and tests)."""
        for i, c in enumerate(compact.upper()[:5]):
            self.bend[i] = 1.0 if c == "B" else 0.0

    def frames_due(self, now: float) -> list[SensorFrame]:
        if self._next_at is None:
            self._next_at = now
        out = []
        while self._next_at <= now and len(out) < 50:
            out.append(self._frame(self._next_at))
            self._next_at += self.period
        if self._next_at <= now:  # far behind (debugger pause): skip ahead, never burst
            self._next_at = now + self.period
        return out

    def _frame(self, t: float) -> SensorFrame:
        self._t += self.period
        flex = tuple(int(min(4095, max(0, SIM_OPEN + b * (SIM_CLOSED - SIM_OPEN) + self._rng.gauss(0, 4))))
                     for b in self.bend)
        wobble = math.sin(self._t * 1.3) * 0.02
        accel = (round(wobble, 3), round(0.01 + self._rng.gauss(0, 0.004), 3), round(1.0 + self._rng.gauss(0, 0.004), 3))
        gyro = tuple(round(self._rng.gauss(0, 0.3), 2) for _ in range(3))
        return SensorFrame(flex, accel, gyro, t, Source.SIMULATION)  # type: ignore[arg-type]

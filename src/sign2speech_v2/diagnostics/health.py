"""Hardware diagnostics (pure logic). Readings are reported as they are -
nothing is adjusted to hide a problem.

RAW ADC TEST classification per finger, from a window of readings:
    ZERO            every reading <= ZERO_MARGIN: pin at ~0 V
    STABLE NONZERO  max - min <= STABLE_SPREAD: a defined divider voltage
    CHANGING        moves smoothly (finger bending)
    NOISY           large sample-to-sample jumps: floating input / loose contact
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from sign2speech_v2.config.pins import FINGER_NAMES, FLEX_PINS
from sign2speech_v2.core.status_lines import AdcChannelReport

ZERO_MARGIN = 5
STABLE_SPREAD = 40
NOISY_STEP = 60.0
ALL_ZERO_TEXT = "ALL ADC = 0 — CHECK FLEX WIRING"
ADC_MV_PER_COUNT = 3300 / 4095  # rough estimate for the stream (11 dB attenuation); firmware test reports real mV


class AdcClass(str, Enum):
    ZERO = "ZERO"
    STABLE_NONZERO = "STABLE NONZERO"
    CHANGING = "CHANGING"
    NOISY = "NOISY"
    NO_DATA = "NO DATA"


def classify(minimum: int, maximum: int, mean_step: float, samples: int) -> AdcClass:
    if samples <= 0:
        return AdcClass.NO_DATA
    if maximum <= ZERO_MARGIN:
        return AdcClass.ZERO
    if maximum - minimum <= STABLE_SPREAD:
        return AdcClass.STABLE_NONZERO
    if mean_step > NOISY_STEP:
        return AdcClass.NOISY
    return AdcClass.CHANGING


@dataclass(frozen=True)
class AdcRow:
    finger: str
    gpio: int
    samples: int
    minimum: int | None
    maximum: int | None
    average: float | None
    mean_step: float | None
    millivolts: int | None
    driver: str                  # ATTACHED / NOT ATTACHED / UNKNOWN (stream)
    status: AdcClass
    source: str                  # "firmware ADC test" | "live stream"


def rows_from_values(history: Sequence[Sequence[int]]) -> list[AdcRow]:
    """ADC rows from recent live stream values (history[i] = values of finger i)."""
    rows = []
    for i, values in enumerate(history):
        if not values:
            rows.append(AdcRow(FINGER_NAMES[i], FLEX_PINS[i], 0, None, None, None, None, None, "UNKNOWN",
                               AdcClass.NO_DATA, "live stream"))
            continue
        steps = [abs(b - a) for a, b in zip(values, values[1:])]
        mean_step = statistics.fmean(steps) if steps else 0.0
        avg = statistics.fmean(values)
        rows.append(AdcRow(FINGER_NAMES[i], FLEX_PINS[i], len(values), min(values), max(values), avg, mean_step,
                           int(round(avg * ADC_MV_PER_COUNT)), "UNKNOWN",
                           classify(min(values), max(values), mean_step, len(values)), "live stream"))
    return rows


def rows_from_reports(reports: Sequence[AdcChannelReport]) -> list[AdcRow]:
    return [AdcRow(r.finger, r.gpio, r.samples, r.minimum, r.maximum, float(r.average), float(r.mean_step),
                   r.millivolts, "ATTACHED" if r.driver_attached else "NOT ATTACHED",
                   classify(r.minimum, r.maximum, r.mean_step, r.samples), "firmware ADC test")
            for r in reports]


def all_zero(rows: Sequence[AdcRow]) -> bool:
    return len(rows) == len(FINGER_NAMES) and all(r.status is AdcClass.ZERO for r in rows)


class RollingWindow:
    """Recent values per channel (the diagnostics' view of the one live stream)."""

    def __init__(self, channels: int, size: int = 100) -> None:
        self._data = [deque(maxlen=size) for _ in range(channels)]

    def add(self, values: Sequence[float]) -> None:
        for d, v in zip(self._data, values):
            d.append(v)

    def clear(self) -> None:
        for d in self._data:
            d.clear()

    def values(self) -> list[list[float]]:
        return [list(d) for d in self._data]

    def spans(self) -> list[float]:
        return [(max(d) - min(d)) if d else 0.0 for d in self._data]

    def __len__(self) -> int:
        return len(self._data[0])


ACCEL_MOVE_G = 0.05       # accelerometer span that counts as "changed when moved"
GYRO_MOVE_DPS = 5.0


@dataclass(frozen=True)
class MotionReport:
    accel_changing: bool
    gyro_changing: bool
    accel_span: float
    gyro_span: float
    samples: int
    all_zero: bool           # every value exactly 0: MPU not delivering data

    @property
    def text(self) -> str:
        if self.samples == 0:
            return "NO MPU DATA"
        if self.all_zero:
            return "MPU6050 ERROR — all accel/gyro values are 0"
        a = "CHANGING" if self.accel_changing else "steady"
        g = "CHANGING" if self.gyro_changing else "steady"
        return f"accel {a} (span {self.accel_span:.3f} g), gyro {g} (span {self.gyro_span:.1f} °/s)"


def motion_report(accel: RollingWindow, gyro: RollingWindow) -> MotionReport:
    n = len(accel)
    if n == 0:
        return MotionReport(False, False, 0.0, 0.0, 0, False)
    a_span, g_span = max(accel.spans()), max(gyro.spans())
    zero = all(v == 0 for ch in accel.values() + gyro.values() for v in ch)
    return MotionReport(a_span >= ACCEL_MOVE_G, g_span >= GYRO_MOVE_DPS, a_span, g_span, n, zero)


def flex_health(rows: Sequence[AdcRow], calibrated_ranges: Sequence[int] | None) -> list[str]:
    """One line per finger for FLEX SENSOR HEALTH."""
    out = []
    for i, r in enumerate(rows):
        if r.status is AdcClass.NO_DATA:
            out.append(f"{r.finger}: NO DATA")
        elif r.status is AdcClass.ZERO:
            out.append(f"{r.finger}: ADC = 0 — CHECK FLEX WIRING")
        elif r.minimum is not None and r.minimum >= 4090:
            out.append(f"{r.finger}: ADC stuck at 4095 — CHECK FLEX WIRING")
        elif r.status is AdcClass.NOISY:
            out.append(f"{r.finger}: NOISY — loose contact or floating input?")
        else:
            cal = f", calibrated range {calibrated_ranges[i]}" if calibrated_ranges else ""
            out.append(f"{r.finger}: signal present ({r.status.value}{cal})")
    return out

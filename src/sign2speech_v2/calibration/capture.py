"""Calibration from the real live stream (pure analysis + a capture session).

CAPTURE OPEN   - hand relaxed and still for ~2 s. Per finger: median = open
                 reference, standard deviation = noise.
CAPTURE CLOSED - bend all fingers and HOLD for ~3 s (a fist, or each finger
                 in turn). Per finger the reach is the 95th percentile of the
                 distance from open in the dominant direction; the frames at
                 >= 70 % of the reach are the held bend, their median is the
                 closed reference. Works for sensors whose reading rises or
                 falls when bent (inverted).

Only the measured open/closed values are stored; the OPEN/BENT threshold is a
fraction of each finger's own open->closed span (model.FingerCalibration), so a
glove whose readings run 0-200 calibrates as well as one running 200-2000. There
is no absolute ADC threshold anywhere.

Per-finger QUALITY (assess):
  DEAD / INVALID - stuck on a rail (0 or 4095) open AND bent, the bend was not
                   held, or range < MIN_SNR x measured noise (signal lost in noise)
  WEAK SENSOR    - clean but range < min_range counts (configurable)
  GOOD           - otherwise
A reading of 0 while the hand is OPEN is not a fault by itself (this glove's
dividers sit near 0 when straight); it is only stuck if it stays there when bent.

A problem on a PRIMARY finger (Thumb, Index, Middle, Little) blocks saving. Ring
(OPTIONAL_FINGERS) is reported just as visibly but never blocks: the built-in
gestures use Ring = X. Values are never altered to make a calibration pass.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sign2speech_v2.calibration.model import Calibration, FingerCalibration
from sign2speech_v2.config.pins import FINGER_NAMES, FLEX_PINS
from sign2speech_v2.core.frame import ADC_MAX

MIN_FRAMES = 20
MIN_RANGE = 20            # default for Settings.calibration_min_range
MIN_SNR = 5.0
NOISE_FLOOR = 2.0
MIN_HELD_FRAMES = 10
RAIL_MARGIN = 5
CLOSED_PERCENTILE = 0.95
HOLD_FRACTION = 0.7
OPEN_MAX_NOISE = 60.0
OPEN_SECONDS = 2.0
CLOSED_SECONDS = 3.0
# fingers whose calibration problems are warnings, not errors (not used to tell built-in gestures apart)
OPTIONAL_FINGERS = frozenset({"Ring"})

GOOD, WEAK, DEAD = "GOOD", "WEAK SENSOR", "DEAD / INVALID"

FlexRows = Sequence[Sequence[int]]


def _where(finger: int) -> str:
    return f"{FINGER_NAMES[finger]} (GPIO{FLEX_PINS[finger]})"


def rail(values: Sequence[int]) -> int | None:
    """0 or ADC_MAX when every value sits on that rail, else None."""
    if values and all(v <= RAIL_MARGIN for v in values):
        return 0
    if values and all(v >= ADC_MAX - RAIL_MARGIN for v in values):
        return ADC_MAX
    return None


def rail_error(values: Sequence[int], finger: int) -> str | None:
    r = rail(values)
    if r == 0:
        return f"{_where(finger)}: stuck at ADC 0 — CHECK FLEX WIRING (3.3V feed, sensor, resistor, signal wire)."
    if r == ADC_MAX:
        return f"{_where(finger)}: stuck at ADC 4095 — CHECK FLEX WIRING (resistor to GND missing? short to 3.3V?)."
    return None


@dataclass(frozen=True)
class FingerQuality:
    finger: str
    status: str               # GOOD | WEAK SENSOR | DEAD / INVALID
    reason: str
    snr: float                # range / noise

    @property
    def optional(self) -> bool:
        return self.finger in OPTIONAL_FINGERS

    @property
    def blocking(self) -> bool:
        return self.status != GOOD and not self.optional


def assess(cal: FingerCalibration, min_range: int = MIN_RANGE, stuck_error: str | None = None,
           held_frames: int | None = None) -> FingerQuality:
    """Signal quality of one finger from its measured range and noise (and capture facts when known)."""
    noise = max(cal.noise, NOISE_FLOOR)
    snr = cal.range / noise
    if stuck_error:
        return FingerQuality(cal.finger, DEAD, stuck_error, snr)
    if cal.range == 0:
        return FingerQuality(cal.finger, DEAD, f"{cal.finger}: the reading did not change when bent.", snr)
    if cal.range < min_range:
        return FingerQuality(cal.finger, WEAK, f"{cal.finger}: WEAK SENSOR — {cal.finger.upper()} RANGE IS VERY SMALL "
                             f"({cal.range} counts, need ≥ {min_range}; range/noise {snr:.1f}).", snr)
    if held_frames is not None and held_frames < MIN_HELD_FRAMES:
        return FingerQuality(cal.finger, DEAD, f"{cal.finger}: bend held for only {held_frames} frames "
                             f"(need {MIN_HELD_FRAMES}). Hold it still.", snr)
    if snr < MIN_SNR:
        return FingerQuality(cal.finger, DEAD, f"{cal.finger}: range {cal.range} is not separable from noise "
                             f"±{noise:.0f} (range/noise {snr:.1f}, need ≥ {MIN_SNR:.0f}). "
                             "Check for a loose connection.", snr)
    return FingerQuality(cal.finger, GOOD, "", snr)


@dataclass(frozen=True)
class OpenResult:
    open: tuple[int, ...]
    noise: tuple[float, ...]
    frames: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ClosedResult:
    fingers: tuple[FingerCalibration, ...]
    frames: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    quality: tuple[FingerQuality, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and len(self.fingers) == len(FINGER_NAMES)

    def to_calibration(self, bend_threshold: float = 0.5, hysteresis: float = 0.08) -> Calibration:
        if not self.ok:
            raise ValueError("calibration has errors: " + " ".join(self.errors))
        return Calibration(self.fingers, bend_threshold, hysteresis,
                           datetime.now().isoformat(timespec="seconds"))


def analyze_open(rows: FlexRows) -> OpenResult:
    if len(rows) < MIN_FRAMES:
        return OpenResult((), (), len(rows), (f"Only {len(rows)} live frames captured (need {MIN_FRAMES}). "
                                             "Is the glove streaming?",))
    opens, noises, warnings = [], [], []
    for i in range(len(FINGER_NAMES)):
        values = [int(r[i]) for r in rows]
        opens.append(int(round(statistics.median(values))))
        noise = statistics.pstdev(values)
        noises.append(noise)
        if (r := rail(values)) is not None:
            # a straight finger may read 0 on this glove; stuck is decided when CAPTURE CLOSED shows no movement
            warnings.append(f"{_where(i)}: reads ADC {r} while open - fine if it changes when bent "
                            "(checked in CAPTURE CLOSED).")
        elif noise > OPEN_MAX_NOISE:
            warnings.append(f"{FINGER_NAMES[i]}: reading moved by ±{noise:.0f} while the hand should be still.")
    if all(o <= RAIL_MARGIN for o in opens):
        warnings.append("ALL ADC = 0 while open - if no finger changes when bent, CHECK FLEX WIRING "
                        "(common 3.3V/GND feed to the dividers?).")
    return OpenResult(tuple(opens), tuple(noises), len(rows), (), tuple(warnings))


def analyze_closed(open_result: OpenResult, rows: FlexRows, min_range: int = MIN_RANGE) -> ClosedResult:
    if not open_result.ok:
        return ClosedResult((), len(rows), ("Capture OPEN successfully first.",))
    if len(rows) < MIN_FRAMES:
        return ClosedResult((), len(rows), (f"Only {len(rows)} live frames captured (need {MIN_FRAMES}). "
                                            "Is the glove streaming?",))
    fingers, quality, errors = [], [], []
    warnings = [w for w in open_result.warnings if "while open" not in w]   # rail notes are resolved below
    for i, name in enumerate(FINGER_NAMES):
        values = [int(r[i]) for r in rows]
        base = open_result.open[i]
        dev = [v - base for v in values]
        mags = sorted(abs(d) for d in dev)
        reach = mags[min(len(mags) - 1, int(CLOSED_PERCENTILE * (len(mags) - 1)))]
        big = [d for d in dev if abs(d) >= reach * 0.5] or dev
        direction = 1 if sum(d > 0 for d in big) >= sum(d < 0 for d in big) else -1
        held = [v for v, d in zip(values, dev) if direction * d >= HOLD_FRACTION * reach] or values
        closed = max(0, min(ADC_MAX, int(round(statistics.median(held)))))
        closed_noise = statistics.pstdev(held) if len(held) > 1 else 0.0
        noise = max(open_result.noise[i], closed_noise, NOISE_FLOOR)
        cal = FingerCalibration(name, base, closed, round(noise, 2))
        # stuck = on the same rail while open AND while bent
        stuck = rail_error(values, i) if rail(values) is not None and rail(values) == rail([base]) else None
        q = assess(cal, min_range, stuck, len(held))
        if q.blocking:
            errors.append(q.reason + ("" if q.status == DEAD else " Bend this finger fully, or check its sensor."))
        elif q.status != GOOD:
            warnings.append(f"{q.reason} Saved anyway: the built-in gestures use {name} = X (ANY); "
                            f"gestures that require {name} OPEN/BENT will be unreliable.")
        fingers.append(cal)
        quality.append(q)
    if len({f.inverted for f, q in zip(fingers, quality) if q.status == GOOD}) > 1:
        warnings.append("Not all fingers bend in the same ADC direction (supported: per-finger inversion).")
    return ClosedResult(tuple(fingers), len(rows), tuple(errors), tuple(warnings), tuple(quality))


class CaptureSession:
    """Collects raw flex rows from the live stream for a fixed time."""

    def __init__(self, kind: str, started_at: float, seconds: float) -> None:
        self.kind = kind            # "open" | "closed"
        self.started_at = started_at
        self.seconds = seconds
        self.rows: list[tuple[int, ...]] = []

    def add(self, flex: Sequence[int]) -> None:
        self.rows.append(tuple(flex))

    def progress(self, now: float) -> float:
        return min(1.0, (now - self.started_at) / self.seconds)

    def done(self, now: float) -> bool:
        return now - self.started_at >= self.seconds

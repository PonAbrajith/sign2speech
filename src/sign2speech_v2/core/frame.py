"""Sensor frame and the ESP32 -> Studio line protocol.

One data line per sample (~50 Hz), 11 comma separated values:

    Flex1,Flex2,Flex3,Flex4,Flex5,AccelX,AccelY,AccelZ,GyroX,GyroY,GyroZ

Flex1..Flex5 = Thumb, Index, Middle, Ring, Little (GPIO 32, 35, 34, 39, 36),
raw 12-bit ADC counts 0..4095. Accel in g, gyro in deg/s.
Lines starting with '#' are status/diagnostic lines (core.status_lines).
The timestamp is assigned by the host on reception.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

ADC_MAX = 4095
FIELD_COUNT = 11


class Source(str, Enum):
    REAL = "REAL ESP32"
    SIMULATION = "SIMULATION"


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SensorFrame:
    flex: tuple[int, int, int, int, int]
    accel: tuple[float, float, float]
    gyro: tuple[float, float, float]
    timestamp: float
    source: Source = Source.REAL
    raw_line: str = field(default="", compare=False, repr=False)   # the serial line exactly as received

    def to_line(self) -> str:
        return (",".join(str(v) for v in self.flex) + "," + ",".join(f"{v:.3f}" for v in self.accel)
                + "," + ",".join(f"{v:.2f}" for v in self.gyro))


def parse_data_line(line: str, timestamp: float, source: Source = Source.REAL) -> SensorFrame:
    """Parse one data line; raise ProtocolError for anything malformed."""
    text = line.strip()
    if not text:
        raise ProtocolError("empty line")
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != FIELD_COUNT:
        raise ProtocolError(f"expected {FIELD_COUNT} values, got {len(parts)}: {text[:60]!r}")
    try:
        values = [float(p) for p in parts]
    except ValueError as exc:
        raise ProtocolError(f"non-numeric value in {text[:60]!r}") from exc
    if not all(math.isfinite(v) for v in values):
        raise ProtocolError(f"non-finite value in {text[:60]!r}")
    flex = values[:5]
    for v in flex:
        if not v.is_integer() or not 0 <= v <= ADC_MAX:
            raise ProtocolError(f"flex value {v:g} is not an ADC count 0..{ADC_MAX}")
    return SensorFrame(tuple(int(v) for v in flex), tuple(values[5:8]), tuple(values[8:11]),  # type: ignore[arg-type]
                       timestamp, source, text)

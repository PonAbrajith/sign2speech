"""Firmware diagnostic lines (every line starting with '#').

The V2 firmware prints at start-up (and on the STATUS command):

    # FIRMWARE: Sign2Speech V2 <fingerprint>
    # ESP32: OK chip=ESP32-D0WD-V3 rev=3 cpu=240MHz
    # PINS: FLEX=32,35,34,39,36 OLED_SDA=18 OLED_SCL=19 MPU_SDA=22 MPU_SCL=21
    # FLEX: OK raw=812,640,700,655,590      or   # FLEX: ERROR ALL ADC = 0 - CHECK FLEX WIRING raw=0,0,0,0,0
    # MPU6050: OK addr=0x68 WHO_AM_I=0x70  or   # MPU6050: ERROR <reason>
    # OLED: OK addr=0x3C                   or   # OLED: ERROR not detected ...
    # GESTURES: 5
    # RECOGNITION: FINGER_PATTERN          or   # RECOGNITION: DISABLED not calibrated
    # STREAM: 50 Hz accel=g gyro=deg/s
    # READY

Commands answer with:

    # I2C OLED: SDA=18 SCL=19 found 0x3C
    # I2C MPU: SDA=22 SCL=21 found 0x68
    # ADC TEST: samples=32 interval_us=500
    # ADC Thumb GPIO32 samples=32 min=801 max=815 avg=808 step=3 mv=690 driver=ATTACHED
    # ADC END
    # PONG

On-device recognition prints one line per accepted gesture:

    # GESTURE: HELLO

and every 5 s a heartbeat:  # STATUS MPU6050=OK OLED=OK UPTIME=12
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class LineKind(str, Enum):
    DEVICE = "device"            # ESP32 / FLEX / MPU6050 / OLED : OK|ERROR
    FIRMWARE = "firmware"
    LEGACY_FIRMWARE = "legacy_firmware"   # V1 firmware id line
    PINS = "pins"
    I2C_SCAN = "i2c"
    GESTURE = "gesture"
    GESTURE_COUNT = "gestures"
    RECOGNITION = "recognition"
    STREAM = "stream"
    READY = "ready"
    HEARTBEAT = "heartbeat"
    ADC_BEGIN = "adc_begin"
    ADC_CHANNEL = "adc_channel"
    ADC_END = "adc_end"
    PONG = "pong"
    ERROR = "error"
    INFO = "info"


@dataclass(frozen=True)
class AdcChannelReport:
    finger: str
    gpio: int
    samples: int
    minimum: int
    maximum: int
    average: int
    mean_step: int
    millivolts: int
    driver_attached: bool


@dataclass(frozen=True)
class StatusLine:
    text: str
    kind: LineKind
    device: str | None = None             # ESP32 / FLEX / MPU6050 / OLED
    ok: bool | None = None
    detail: str = ""
    values: dict[str, str] = field(default_factory=dict)
    gesture: str | None = None
    count: int | None = None
    bus: str | None = None                # "OLED" / "MPU" for I2C scans
    found: tuple[int, ...] = ()
    adc: AdcChannelReport | None = None


_DEVICE = re.compile(r"^(ESP32|FLEX|MPU6050|OLED)\s*:\s*(OK|ERROR)\b\s*(.*)$", re.I)
_KV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)")
_LEGACY_ID = re.compile(r"^Sign2Speech ESP32 firmware\s+(\S+)", re.I)   # V1: '# Sign2Speech ESP32 firmware <fp>'
_I2C = re.compile(r"^I2C\s+(OLED|MPU)\s*:\s*(.*)$", re.I)
_ADC_CH = re.compile(
    r"^ADC\s+(Thumb|Index|Middle|Ring|Little)\s+GPIO(\d+)\s+samples=(\d+)\s+min=(\d+)\s+max=(\d+)\s+avg=(\d+)"
    r"\s+step=(\d+)\s+mv=(\d+)\s+driver=(ATTACHED|NOT_ATTACHED)", re.I)


def is_status_line(line: str) -> bool:
    return line.lstrip().startswith("#")


def parse_status_line(line: str) -> StatusLine:
    text = line.strip().lstrip("#").strip()
    upper = text.upper()
    if m := _DEVICE.match(text):
        detail = m.group(3).strip()
        return StatusLine(text, LineKind.DEVICE, device=m.group(1).upper(), ok=m.group(2).upper() == "OK",
                          detail=detail, values=dict(_KV.findall(detail)))
    if m := _LEGACY_ID.match(text):
        return StatusLine(text, LineKind.LEGACY_FIRMWARE, detail=m.group(1))
    if upper.startswith("FIRMWARE:"):
        return StatusLine(text, LineKind.FIRMWARE, detail=text.split(":", 1)[1].strip())
    if upper.startswith("PINS:"):
        return StatusLine(text, LineKind.PINS, values=dict(_KV.findall(text)))
    if m := _I2C.match(text):
        found = tuple(int(a, 16) for a in re.findall(r"0x([0-9a-fA-F]{2})", m.group(2).split("found", 1)[-1]))
        return StatusLine(text, LineKind.I2C_SCAN, bus=m.group(1).upper(), values=dict(_KV.findall(m.group(2))),
                          found=found if "found" in m.group(2) else ())
    if upper.startswith("GESTURE:"):
        return StatusLine(text, LineKind.GESTURE, gesture=text.split(":", 1)[1].strip())
    if upper.startswith("GESTURES:"):
        value = text.split(":", 1)[1].strip()
        return StatusLine(text, LineKind.GESTURE_COUNT, count=int(value) if value.isdigit() else None)
    if upper.startswith("RECOGNITION:"):
        detail = text.split(":", 1)[1].strip()
        return StatusLine(text, LineKind.RECOGNITION, detail=detail, ok=not detail.upper().startswith("DISABLED"))
    if upper.startswith("STREAM:"):
        return StatusLine(text, LineKind.STREAM, detail=text.split(":", 1)[1].strip(), values=dict(_KV.findall(text)))
    if upper == "READY":
        return StatusLine(text, LineKind.READY)
    if upper.startswith("STATUS"):
        return StatusLine(text, LineKind.HEARTBEAT, values=dict(_KV.findall(text)))
    if upper.startswith("ADC TEST"):
        return StatusLine(text, LineKind.ADC_BEGIN, values=dict(_KV.findall(text)))
    if upper.startswith("ADC END"):
        return StatusLine(text, LineKind.ADC_END)
    if m := _ADC_CH.match(text):
        g = m.groups()
        report = AdcChannelReport(g[0].capitalize(), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]),
                                  int(g[6]), int(g[7]), g[8].upper() == "ATTACHED")
        return StatusLine(text, LineKind.ADC_CHANNEL, adc=report)
    if upper == "PONG":
        return StatusLine(text, LineKind.PONG)
    if upper.startswith("ERR"):
        return StatusLine(text, LineKind.ERROR, detail=text)
    return StatusLine(text, LineKind.INFO)

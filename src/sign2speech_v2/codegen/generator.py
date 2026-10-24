"""ESP32 firmware generator.

Builds an Arduino sketch folder from the locked pin map (config.pins), the
saved calibration and the ENABLED stored gestures:

    Sign2Speech_V2/
        Sign2Speech_V2.ino   main sketch (sensors, OLED, MPU, matcher, debounce, serial)
        config.h             pins, calibration, thresholds, hold/release
        gestures.h           enabled gesture table (name, display text, O/B/X pattern)
        hardware_lock.h      static compile-time pin/bus checks (copied verbatim)

The generator never changes a pin: it refuses to run if config.pins differs
from the locked wiring, and hardware_lock.h re-checks every pin at compile
time. It only writes files - it never uploads or flashes anything.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Mapping, Sequence

from sign2speech_v2.calibration.model import Calibration
from sign2speech_v2.config import pins
from sign2speech_v2.config.settings import Settings
from sign2speech_v2.gestures.repository import Gesture

SKETCH_NAME = "Sign2Speech_V2"
TEMPLATE_NAMES = ("Sign2Speech_V2.ino.tmpl", "config.h.tmpl", "gestures.h.tmpl", "hardware_lock.h")


@dataclass(frozen=True)
class GeneratedFirmware:
    files: dict[str, str]
    fingerprint: str
    gesture_names: tuple[str, ...]
    calibrated: bool
    warnings: tuple[str, ...]

    def write(self, parent: Path) -> Path:
        out = parent / SKETCH_NAME
        out.mkdir(parents=True, exist_ok=True)
        for name, text in self.files.items():
            (out / name).write_text(text, encoding="utf-8", newline="\n")
        return out


def _template(name: str) -> str:
    return resources.files("sign2speech_v2.codegen").joinpath("templates", name).read_text(encoding="utf-8")


def c_string(text: str) -> str:
    """Escape for a C string literal (ASCII only; others become '?')."""
    out = []
    for ch in text:
        if ch in '"\\':
            out.append("\\" + ch)
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append("?")
    return '"' + "".join(out) + '"'


def _fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace(f"@@{key}@@", value)
    if "@@" in template:
        raise ValueError("unfilled template placeholder: " + template[template.index("@@"):][:40])
    return template


def generate(gestures: Sequence[Gesture], calibration: Calibration | None, settings: Settings,
             now: datetime | None = None, sentences: Mapping[str, str] | None = None) -> GeneratedFirmware:
    """`sentences` maps gesture name -> mapped sentence text; the firmware shows only that sentence on the
    OLED (the same text the laptop speaks). Gestures without a sentence use their display text."""
    sentences = sentences or {}
    pins.verify_wiring()  # never generate firmware for a different pin map
    enabled = [g for g in gestures if g.enabled]
    warnings = []
    if calibration is None:
        warnings.append("NOT CALIBRATED: on-device recognition is disabled in this firmware (the stream, OLED, "
                        "MPU and Studio-driven OLED still work). Calibrate, then regenerate.")
    if not enabled:
        warnings.append("No enabled gestures: the firmware gesture table is empty.")
    # X matches either state, so two different patterns can still accept the same hand
    for i, a in enumerate(enabled):
        for b in enabled[i + 1:]:
            if a.finger_pattern.overlaps(b.finger_pattern):
                warnings.append(f"{a.name} ({a.finger_pattern}) and {b.name} ({b.finger_pattern}) can match the "
                                "same hand shape: AMBIGUOUS.")

    rows = [f'  {{{c_string(g.name)}, {c_string(sentences.get(g.name) or g.display_text)}, '
            f'"{g.finger_pattern.compact()}"}},'
            for g in enabled] or ['  {"", "", "XXXXX"},  // placeholder: no enabled gestures']
    cal_open = ", ".join(str(f.open) for f in calibration.fingers) if calibration else "0, 0, 0, 0, 0"
    cal_closed = ", ".join(str(f.closed) for f in calibration.fingers) if calibration else "0, 0, 0, 0, 0"
    threshold = calibration.bend_threshold if calibration else settings.bend_threshold
    hysteresis = calibration.hysteresis if calibration else settings.hysteresis

    values = {
        "FLEX_THUMB": str(pins.FLEX_PINS[0]), "FLEX_INDEX": str(pins.FLEX_PINS[1]),
        "FLEX_MIDDLE": str(pins.FLEX_PINS[2]), "FLEX_RING": str(pins.FLEX_PINS[3]),
        "FLEX_LITTLE": str(pins.FLEX_PINS[4]),
        "OLED_SDA": str(pins.OLED_SDA), "OLED_SCL": str(pins.OLED_SCL), "OLED_PORT": str(pins.OLED_I2C_PORT),
        "MPU_SDA": str(pins.MPU_SDA), "MPU_SCL": str(pins.MPU_SCL), "MPU_PORT": str(pins.MPU_I2C_PORT),
        "BAUD": str(settings.baud), "FILTER_WINDOW": str(settings.filter_window),
        "CALIBRATED": "1" if calibration else "0", "CAL_OPEN": cal_open, "CAL_CLOSED": cal_closed,
        "BEND_THRESHOLD": f"{threshold:.3f}", "HYSTERESIS": f"{hysteresis:.3f}",
        "HOLD_SAMPLES": str(settings.hold_samples), "RELEASE_SAMPLES": str(settings.release_samples),
        "GESTURE_COUNT": str(len(enabled)), "GESTURE_ROWS": "\n".join(rows),
    }
    # the fingerprint identifies the configuration AND the firmware code, so a board running an
    # older template build is reported as a different build, not as the expected one
    templates = [_template(n) for n in TEMPLATE_NAMES]
    digest = hashlib.sha256(repr((sorted(values.items()), templates)).encode()).hexdigest()[:12]
    values["FINGERPRINT"] = digest
    values["GENERATED_AT"] = (now or datetime.now()).isoformat(timespec="seconds")

    files = {
        f"{SKETCH_NAME}.ino": _fill(_template("Sign2Speech_V2.ino.tmpl"), values),
        "config.h": _fill(_template("config.h.tmpl"), values),
        "gestures.h": _fill(_template("gestures.h.tmpl"), values),
        "hardware_lock.h": _template("hardware_lock.h"),
    }
    return GeneratedFirmware(files, digest, tuple(g.name for g in enabled), calibration is not None,
                             tuple(warnings))

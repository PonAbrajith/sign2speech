"""The glove wiring - the single source of truth for every pin in V2.

Nothing else in the code base may define a GPIO number. The firmware
generator copies these values into config.h, and the static (non-generated)
firmware header hardware_lock.h independently static_asserts the same
numbers, so the pin map can only change through an explicit edit of BOTH
this file and hardware_lock.h - never through a project setting.

Finger order is fixed everywhere: 0 Thumb, 1 Index, 2 Middle, 3 Ring, 4 Little.
"""

from __future__ import annotations

from dataclasses import dataclass

FINGER_NAMES: tuple[str, ...] = ("Thumb", "Index", "Middle", "Ring", "Little")

# Flex sensors: one independent voltage divider per finger, one ADC1 channel each.
FLEX_PINS: tuple[int, int, int, int, int] = (32, 35, 34, 39, 36)
FLEX_BOARD_LABELS: tuple[str, ...] = ("D32", "D35", "D34", "VN", "VP")

# OLED (SSD1306) on its own I2C bus (TwoWire port 1).
OLED_SDA = 18
OLED_SCL = 19
OLED_I2C_PORT = 1
OLED_ADDRESSES: tuple[int, int] = (0x3C, 0x3D)

# MPU6050 on a separate I2C bus (TwoWire port 0).
MPU_SDA = 22
MPU_SCL = 21
MPU_I2C_PORT = 0
MPU_ADDRESS = 0x68
# WHO_AM_I values accepted when the data registers are readable. 0x68 is the
# genuine MPU6050; 0x70 was observed on this glove's module (compatible clone).
MPU_KNOWN_WHO_AM_I: tuple[int, ...] = (0x68, 0x70, 0x72, 0x98)

# Pins that must never be used on this glove (V1 audio output is gone).
FORBIDDEN_PINS: tuple[int, ...] = (25,)

# The locked values, restated literally so a change to one constant above is
# caught by verify_wiring() instead of silently propagating.
_LOCK = {
    "FLEX_PINS": (32, 35, 34, 39, 36),
    "OLED_SDA": 18, "OLED_SCL": 19,
    "MPU_SDA": 22, "MPU_SCL": 21,
}


@dataclass(frozen=True)
class Wiring:
    flex_pins: tuple[int, ...] = FLEX_PINS
    oled_sda: int = OLED_SDA
    oled_scl: int = OLED_SCL
    oled_port: int = OLED_I2C_PORT
    mpu_sda: int = MPU_SDA
    mpu_scl: int = MPU_SCL
    mpu_port: int = MPU_I2C_PORT


WIRING = Wiring()


class WiringError(ValueError):
    """The pin map differs from the locked glove wiring."""


def current_wiring() -> Wiring:
    """The pin map as currently defined by this module's constants (read at call time)."""
    return Wiring(tuple(FLEX_PINS), OLED_SDA, OLED_SCL, OLED_I2C_PORT, MPU_SDA, MPU_SCL, MPU_I2C_PORT)


def verify_wiring(w: Wiring | None = None) -> None:
    """Raise WiringError unless `w` (default: the current constants) is exactly the locked glove wiring."""
    w = w if w is not None else current_wiring()
    problems = []
    if tuple(w.flex_pins) != _LOCK["FLEX_PINS"]:
        problems.append(f"flex pins {tuple(w.flex_pins)} != {_LOCK['FLEX_PINS']} (Thumb, Index, Middle, Ring, Little)")
    for name, value in (("OLED_SDA", w.oled_sda), ("OLED_SCL", w.oled_scl),
                        ("MPU_SDA", w.mpu_sda), ("MPU_SCL", w.mpu_scl)):
        if value != _LOCK[name]:
            problems.append(f"{name}={value}, locked value is {_LOCK[name]}")
    if {w.oled_sda, w.oled_scl} & {w.mpu_sda, w.mpu_scl}:
        problems.append("OLED and MPU6050 must be on separate I2C buses (shared SDA/SCL pin)")
    if w.oled_port == w.mpu_port:
        problems.append("OLED and MPU6050 must use separate TwoWire instances")
    used = set(w.flex_pins) | {w.oled_sda, w.oled_scl, w.mpu_sda, w.mpu_scl}
    if len(used) != len(w.flex_pins) + 4:
        problems.append("a GPIO is assigned twice")
    if used & set(FORBIDDEN_PINS):
        problems.append(f"forbidden pin used: {sorted(used & set(FORBIDDEN_PINS))} (no GPIO25 audio in V2)")
    if problems:
        raise WiringError("; ".join(problems))


def finger_pin_label(finger: int) -> str:
    """'Thumb (D32 / GPIO32)'."""
    return f"{FINGER_NAMES[finger]} ({FLEX_BOARD_LABELS[finger]} / GPIO{FLEX_PINS[finger]})"


verify_wiring()

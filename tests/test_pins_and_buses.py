"""Exact GPIO mapping, separate I2C buses, and that nothing else defines pins."""

import ast
from pathlib import Path

import pytest

from sign2speech_v2.config import pins
from sign2speech_v2.config.pins import Wiring, WiringError, verify_wiring

SRC = Path(pins.__file__).resolve().parents[1]


def test_flex_gpio_order_is_exact():
    assert pins.FLEX_PINS == (32, 35, 34, 39, 36)
    assert pins.FINGER_NAMES == ("Thumb", "Index", "Middle", "Ring", "Little")
    assert dict(zip(pins.FINGER_NAMES, pins.FLEX_PINS)) == {
        "Thumb": 32, "Index": 35, "Middle": 34, "Ring": 39, "Little": 36}
    assert pins.FLEX_BOARD_LABELS == ("D32", "D35", "D34", "VN", "VP")


def test_oled_pins_exact():
    assert (pins.OLED_SDA, pins.OLED_SCL) == (18, 19)
    assert pins.OLED_ADDRESSES == (0x3C, 0x3D)


def test_mpu_pins_exact():
    assert (pins.MPU_SDA, pins.MPU_SCL) == (22, 21)
    assert pins.MPU_ADDRESS == 0x68
    assert 0x70 in pins.MPU_KNOWN_WHO_AM_I


def test_oled_and_mpu_on_separate_buses():
    assert {pins.OLED_SDA, pins.OLED_SCL}.isdisjoint({pins.MPU_SDA, pins.MPU_SCL})
    assert pins.OLED_I2C_PORT != pins.MPU_I2C_PORT
    assert 21 not in (pins.OLED_SDA, pins.OLED_SCL) and 22 not in (pins.OLED_SDA, pins.OLED_SCL)


def test_locked_wiring_verifies():
    verify_wiring()


@pytest.mark.parametrize("change", [
    {"flex_pins": (35, 32, 34, 39, 36)},          # swapped order
    {"flex_pins": (32, 35, 34, 39, 33)},
    {"oled_sda": 22}, {"oled_scl": 21},            # OLED moved onto the MPU bus
    {"mpu_sda": 18},
    {"oled_port": 0},                              # same TwoWire instance
    {"flex_pins": (32, 35, 34, 39, 25)},           # GPIO25 (old audio) forbidden
])
def test_any_pin_change_is_rejected(change):
    with pytest.raises(WiringError):
        verify_wiring(Wiring(**change))


def test_no_other_module_defines_the_flex_pin_map():
    """No tuple/list literal of the flex pins outside config/pins.py (docs may mention them)."""
    offenders = []
    for py in SRC.rglob("*.py"):
        if py.name == "pins.py":
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Tuple, ast.List)):
                vals = [e.value for e in node.elts if isinstance(e, ast.Constant)]
                if vals == [32, 35, 34, 39, 36]:
                    offenders.append(py.name)
    assert offenders == []

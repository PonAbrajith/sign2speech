"""Firmware generator: content, host execution of the generated firmware, and
real ESP32 compilation with arduino-cli (compile only - never uploads)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sign2speech_v2.codegen import generator
from sign2speech_v2.codegen.arduino_cli import compile_sketch, find_arduino_cli
from sign2speech_v2.codegen.generator import c_string, generate
from sign2speech_v2.config import pins
from sign2speech_v2.config.settings import Settings
from sign2speech_v2.core.frame import parse_data_line
from sign2speech_v2.core.patterns import GesturePattern
from sign2speech_v2.gestures.repository import Gesture

from conftest import make_calibration

HOST = Path(__file__).parent / "host"
GESTURES = [
    Gesture(name="HELLO", display_text="Hello", finger_pattern=GesturePattern.parse("OBOBO")),
    Gesture(name="WATER", display_text='Water "cold"', finger_pattern=GesturePattern.parse("BOOOX")),
    Gesture(name="OFF", display_text="Off", finger_pattern=GesturePattern.parse("BBBBB"), enabled=False),
]


@pytest.fixture(scope="module")
def fw():
    return generate(GESTURES, make_calibration(700, 2600), Settings())


# ------------------------------------------------------------------ content
def test_generated_files(fw):
    assert set(fw.files) == {"Sign2Speech_V2.ino", "config.h", "gestures.h", "hardware_lock.h"}
    assert fw.calibrated and fw.gesture_names == ("HELLO", "WATER") and not fw.warnings


def test_pins_and_separate_twowire_instances(fw):
    cfg, ino = fw.files["config.h"], fw.files["Sign2Speech_V2.ino"]
    for name, value in (("FLEX_PIN_THUMB", 32), ("FLEX_PIN_INDEX", 35), ("FLEX_PIN_MIDDLE", 34),
                        ("FLEX_PIN_RING", 39), ("FLEX_PIN_LITTLE", 36), ("OLED_SDA_PIN", 18), ("OLED_SCL_PIN", 19),
                        ("MPU_SDA_PIN", 22), ("MPU_SCL_PIN", 21), ("OLED_I2C_PORT", 1), ("MPU_I2C_PORT", 0)):
        assert re.search(rf"#define {name}\s+{value}\b", cfg), name
    assert "FLEX_PINS[FLEX_COUNT] = {FLEX_PIN_THUMB, FLEX_PIN_INDEX, FLEX_PIN_MIDDLE, FLEX_PIN_RING," in cfg
    assert "TwoWire& mpuWire = Wire;" in ino and "TwoWire& oledWire = Wire1;" in ino
    assert "Adafruit_SSD1306 display(OLED_WIDTH, OLED_HEIGHT, &oledWire, -1);" in ino
    assert "mpuWire.begin(MPU_SDA_PIN, MPU_SCL_PIN" in ino and "oledWire.begin(OLED_SDA_PIN, OLED_SCL_PIN" in ino
    assert "Wire.begin(" not in ino.replace("mpuWire.begin(", "").replace("oledWire.begin(", "")


def test_compile_time_pin_checks_present(fw):
    lock = fw.files["hardware_lock.h"]
    for n in (32, 35, 34, 39, 36, 18, 19, 22, 21):
        assert re.search(rf"== {n}\b", lock), n
    assert "OLED_I2C_PORT != MPU_I2C_PORT" in lock
    assert '#include "hardware_lock.h"' in fw.files["Sign2Speech_V2.ino"]


def test_gesture_table_calibration_and_debounce(fw):
    g, cfg = fw.files["gestures.h"], fw.files["config.h"]
    assert "#define GESTURE_COUNT 2" in g
    assert '{"HELLO", "Hello", "OBOBO"},' in g and '{"WATER", "Water \\"cold\\"", "BOOOX"},' in g
    assert "OFF" not in g                                   # disabled gestures are not included
    assert "CAL_OPEN[FLEX_COUNT] = {700, 700, 700, 700, 700}" in cfg
    assert "CAL_CLOSED[FLEX_COUNT] = {2600, 2600, 2600, 2600, 2600}" in cfg
    assert "#define CALIBRATED 1" in cfg and "#define HOLD_SAMPLES 10" in cfg


def test_no_audio_in_firmware(fw):
    text = "\n".join(v for k, v in fw.files.items() if k != "hardware_lock.h").upper()
    for word in ("DFPLAYER", "PAM8403", "CA-8403", "DACWRITE", "LEDCWRITE", "GPIO25", "I2S"):
        assert word not in text, word
    assert not re.search(r"_PIN\s+25\b", fw.files["config.h"])     # no pin on GPIO25 (timestamp may contain 25)


def test_uncalibrated_and_empty_generation():
    fw = generate([], None, Settings())
    assert "#define CALIBRATED 0" in fw.files["config.h"] and "#define GESTURE_COUNT 0" in fw.files["gestures.h"]
    assert len(fw.warnings) == 2


def test_duplicate_patterns_warned():
    dup = [GESTURES[0], Gesture(name="HI", display_text="Hi", finger_pattern=GesturePattern.parse("OBOBO"))]
    assert any("AMBIGUOUS" in w for w in generate(dup, make_calibration(), Settings()).warnings)


@pytest.mark.parametrize("attr,value", [("FLEX_PINS", (32, 35, 34, 39, 33)), ("FLEX_PINS", (35, 32, 34, 39, 36)),
                                        ("OLED_SDA", 22), ("MPU_SCL", 19), ("OLED_I2C_PORT", 0)])
def test_generator_refuses_changed_pins(monkeypatch, attr, value):
    """A changed pin constant is never silently turned into firmware."""
    monkeypatch.setattr(pins, attr, value)
    with pytest.raises(pins.WiringError):
        generator.generate(GESTURES, None, Settings())


def test_c_string_escaping():
    assert c_string('say "hi"\\') == '"say \\"hi\\"\\\\"'
    assert c_string("नमस्ते") == '"??????"'


def test_write_creates_sketch_folder(fw, tmp_path):
    out = fw.write(tmp_path)
    assert out.name == "Sign2Speech_V2" and (out / "Sign2Speech_V2.ino").exists()


# ------------------------------------------------- run the firmware on the PC
GXX = shutil.which("g++")


@pytest.fixture(scope="module")
def harness(fw, tmp_path_factory):
    if not GXX:
        pytest.skip("g++ not available for host firmware tests")
    d = tmp_path_factory.mktemp("host")
    sketch = fw.write(d)
    exe = d / "harness.exe"
    r = subprocess.run([GXX, "-std=gnu++14", "-Wall", "-I", str(HOST / "stub"), "-I", str(sketch),
                        str(HOST / "harness.cpp"), "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]

    def run(script: str, *args: str) -> list[str]:
        p = subprocess.run([str(exe), *args], input=script, capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, p.stderr
        return p.stdout.splitlines()
    return run


def data_lines(out):
    return [l for l in out if l[:1].isdigit()]


def test_host_buses_are_separate_and_on_the_right_pins(harness):
    out = harness("SETUP\n")
    assert "@WIRE port=0 sda=22 scl=21 freq=400000" in out     # MPU6050 bus
    assert "@WIRE port=1 sda=18 scl=19 freq=400000" in out     # OLED bus
    assert "@OLED_BEGIN port=1 sda=18 scl=19 addr=0x3C" in out
    assert not any("WITHOUT PINS" in l for l in out)            # OLED library never re-inits the bus


def test_host_boot_diagnostics(harness):
    out = harness("ADC 800 810 820 830 840\nSETUP\n")
    for expected in ("# ESP32: OK chip=ESP32-D0WD-V3 rev=3 cpu=240MHz",
                     "# PINS: FLEX=32,35,34,39,36 OLED_SDA=18 OLED_SCL=19 MPU_SDA=22 MPU_SCL=21",
                     "# FLEX: OK raw=800,810,820,830,840",
                     "# MPU6050: OK addr=0x68 WHO_AM_I=0x70 compatible",   # 0x70 accepted
                     "# OLED: OK addr=0x3C", "# GESTURES: 2", "# RECOGNITION: FINGER_PATTERN", "# READY"):
        assert expected in out, expected


def test_host_stream_keeps_finger_order(harness):
    out = harness("SETUP\nADC 101 202 303 404 505\nRUN 3\n")
    frames = [parse_data_line(l, 0) for l in data_lines(out)]
    assert len(frames) == 3
    assert all(f.flex == (101, 202, 303, 404, 505) for f in frames)   # Thumb..Little = GPIO 32,35,34,39,36
    assert frames[0].accel == pytest.approx((0.001, 0.002, 1.0), abs=0.001)


def test_host_gesture_is_recognized_once_and_shown_on_oled(harness):
    script = "SETUP\nADC 700 700 700 700 700\nRUN 20\nMARK sign\nADC 700 2600 700 2600 700\nRUN 300\n"
    out = harness(script)
    after = out[out.index("@MARK sign"):]
    assert after.count("# GESTURE: HELLO") == 1                        # held 6 s: printed once
    assert "@OLED Hello" in after                                     # the text only, no name/label
    n_before = data_lines(after[:after.index("# GESTURE: HELLO")])
    assert 10 <= len(n_before) <= 14                                   # hold ~10 samples (+ filter delay)


def test_host_repeat_needs_release(harness):
    s = ("SETUP\nADC 700 2600 700 2600 700\nRUN 30\nADC 700 700 700 700 700\nRUN 30\n"
         "ADC 700 2600 700 2600 700\nRUN 30\n")
    out = harness(s)
    assert out.count("# GESTURE: HELLO") == 2
    first, second = [i for i, l in enumerate(out) if l == "# GESTURE: HELLO"]
    assert "@OLED Unknown" in out[first:second]                    # left the sign: Unknown


def test_host_x_pattern_and_unknown(harness):
    out = harness("SETUP\nADC 2600 700 700 700 2600\nRUN 30\nADC 2600 2600 2600 2600 700\nRUN 30\n")
    assert out.count("# GESTURE: WATER") == 1                           # BOOOX with Little bent
    assert "# GESTURE: HELLO" not in out


def test_host_studio_drives_oled(harness):
    out = harness("SETUP\nCMD SHOW WATER|Please give me some water.\nADC 700 2600 700 2600 700\nRUN 30\n"
                  "CMD CLEAR\n")
    i = out.index("@OLED Please give me|some water.")                   # the sentence only
    assert "# GESTURE: HELLO" in out[i:]                                 # still reported on serial
    assert "@OLED Hello" not in out[i:]                                 # but OLED follows the Studio
    assert out[-1] == "@OLED Waiting..."                                 # CLEAR from the Studio


def test_host_studio_show_without_sentence_falls_back_to_name(harness):
    assert "@OLED WATER" in harness("SETUP\nCMD SHOW WATER\n")


@pytest.mark.parametrize("name, sentence, lines", [
    ("WATER", "Please give me some water.", ["Please give me", "some water."]),
    ("HUNGRY", "I am feeling hungry.", ["I am feeling", "hungry."]),
    ("HAPPY", "I am very happy to see you.", ["I am very happy", "to see you."]),
    ("SCARED", "I am scared, please help me.", ["I am scared,", "please help me."]),
])
def test_host_oled_shows_only_the_sentence(harness, name, sentence, lines):
    oled = [l for l in harness(f"SETUP\nCMD SHOW {name}|{sentence}\n") if l.startswith("@OLED ")]
    shown = oled[-1][len("@OLED "):]
    assert shown.split("|") == lines                                     # word-wrapped, whole words
    assert " ".join(shown.split("|")) == sentence                        # exactly the sentence, nothing else
    assert name not in shown and "Gesture" not in shown and ":" not in shown


def test_host_oled_long_sentence_wraps_without_splitting_words(harness):
    sentence = "Could you please call my family and tell them that I will be home late tonight."
    shown = [l for l in harness(f"SETUP\nCMD SHOW FAMILY|{sentence}\n") if l.startswith("@OLED ")][-1][6:]
    lines = shown.split("|")
    assert 3 <= len(lines) <= 8                                          # several lines, fits 128x64
    assert all(len(l) <= 21 for l in lines)                             # 21 chars at text size 1
    assert " ".join(lines) == sentence                                   # every word intact, none cut
    assert "FAMILY" not in shown


def test_host_oled_word_longer_than_preferred_width_is_not_split(harness):
    sentence = "Hello unbelievablyamazing world"                             # 19-letter word
    shown = [l for l in harness(f"SETUP\nCMD SHOW X|{sentence}\n") if l.startswith("@OLED ")][-1][6:]
    assert "unbelievablyamazing" in shown.split("|")


def test_host_oled_states_have_no_label(harness):
    out = harness("SETUP\nCMD UNKNOWN\nCMD CLEAR\n")
    assert [l for l in out if l.startswith("@OLED ")][-2:] == ["@OLED Unknown", "@OLED Waiting..."]
    assert not any("Gesture:" in l for l in out if l.startswith("@OLED"))


def test_host_oled_at_0x3d(harness):
    out = harness("SETUP\n", "oled=3D")
    assert "# OLED: OK addr=0x3D" in out


def test_host_oled_missing_does_not_stop_firmware(harness):
    out = harness("SETUP\nADC 700 2600 700 2600 700\nRUN 30\n", "oled=none")
    assert any(l.startswith("# OLED: ERROR not detected") for l in out)
    assert "# GESTURE: HELLO" in out and len(data_lines(out)) == 30


def test_host_mpu_missing_reports_error(harness):
    out = harness("SETUP\n", "mpu=no")
    assert any(l.startswith("# MPU6050: ERROR no answer at 0x68") for l in out)


def test_host_all_adc_zero(harness):
    out = harness("SETUP\n")
    assert any("# FLEX: ERROR ALL ADC = 0 - CHECK FLEX WIRING" in l for l in out)


def test_host_i2c_scan_sees_each_device_only_on_its_bus(harness):
    out = harness("SETUP\nCMD I2C\n")
    assert "# I2C OLED: SDA=18 SCL=19 found 0x3C" in out
    assert "# I2C MPU: SDA=22 SCL=21 found 0x68" in out


def test_host_adc_test_command(harness):
    out = harness("ADC 0 800 800 800 800\nSETUP\nCMD ADC\n")
    assert "# ADC Thumb GPIO32 samples=32 min=0 max=0 avg=0 step=0 mv=0 driver=ATTACHED" in out
    assert any(l.startswith("# ADC Little GPIO36 samples=32 min=800 max=800") for l in out)
    assert "# ADC END" in out


def test_host_unknown_command(harness):
    assert "# ERR unknown command 'FLASH'" in harness("SETUP\nCMD FLASH\n")


# --------------------------------------------------- real ESP32 compilation
CLI = find_arduino_cli()


@pytest.mark.firmware_compile
@pytest.mark.skipif(CLI is None, reason="arduino-cli not installed")
def test_arduino_cli_compiles_generated_firmware(fw, tmp_path):
    r = compile_sketch(fw.write(tmp_path))
    assert r.ok, r.output[-3000:]
    assert "Sketch uses" in r.output


@pytest.mark.firmware_compile
@pytest.mark.skipif(CLI is None, reason="arduino-cli not installed")
def test_changed_pins_fail_to_compile(fw, tmp_path):
    """hardware_lock.h stops a build whose config.h pins differ from the glove."""
    sketch = fw.write(tmp_path)
    cfg = (sketch / "config.h").read_text()
    cfg = cfg.replace("#define FLEX_PIN_THUMB  32", "#define FLEX_PIN_THUMB  33")
    cfg = cfg.replace("#define OLED_I2C_PORT 1", "#define OLED_I2C_PORT 0")
    (sketch / "config.h").write_text(cfg)
    r = compile_sketch(sketch)
    assert not r.ok
    assert "Thumb flex must be D32 / GPIO32" in r.output
    assert "OLED and MPU6050 must use separate TwoWire instances" in r.output


# ------------------------------------------------------ command-line generation
def test_generate_firmware_command_uses_workspace_gestures_and_calibration(tmp_path, capsys):
    from sign2speech_v2.calibration.model import CalibrationStore
    from sign2speech_v2.config.settings import Workspace
    from sign2speech_v2.gestures.repository import GestureRepository
    from sign2speech_v2.ui.app import main

    ws = Workspace(tmp_path / "ws").ensure()
    repo = GestureRepository(ws.db_path)
    repo.save(Gesture(name="HELLO", display_text="Hello", finger_pattern=GesturePattern.parse("OOOBB")))
    repo.save(Gesture(name="WATER", display_text="Water", finger_pattern=GesturePattern.parse("BOBOB"),
                      enabled=False))
    repo.close()
    CalibrationStore(ws.calibration_path).save(make_calibration(181, 1090))
    assert main(["--generate-firmware", "--workspace", str(ws.root)]) == 0
    out = capsys.readouterr().out
    # the user's own WATER (disabled) and HELLO are kept; the other 9 prototype gestures are added
    assert "Kept your existing gesture WATER" in out and "Added PROTOTYPE gesture WATER" not in out
    assert "Added PROTOTYPE gesture HUNGRY" in out and "Calibrated: yes" in out and "Not uploaded" in out
    # HELLO (user, O O O B B) and TIRED (prototype, O O O X B - Ring ANY) accept the same hand: reported,
    # not silently resolved, although the two pattern strings differ
    assert "WARNING: HELLO (O O O B B) and TIRED (O O O X B) can match the same hand shape: AMBIGUOUS." in out
    sketch = ws.firmware_dir / "Sign2Speech_V2"
    g = (sketch / "gestures.h").read_text()
    assert '{"HELLO", "Hello", "OOOBB"},' in g and '{"HUNGRY", "I am feeling hungry.", "BBBXB"},' in g
    assert '"WATER"' not in g                                              # the user's WATER is disabled
    assert "CAL_OPEN[FLEX_COUNT] = {181, 181, 181, 181, 181}" in (sketch / "config.h").read_text()


def test_host_oled_shows_waiting_at_boot(harness):
    out = harness("SETUP\n")
    assert out[-1] == "@OLED Waiting..."


def test_fingerprint_changes_when_firmware_code_changes(monkeypatch):
    base = generate([], None, Settings()).fingerprint
    assert generate([], None, Settings()).fingerprint == base                 # deterministic
    real = generator._template
    monkeypatch.setattr(generator, "_template",
                        lambda n: real(n).replace("Waiting...", "Idle") if n.endswith(".ino.tmpl") else real(n))
    assert generate([], None, Settings()).fingerprint != base                 # same config, other code



def test_host_oled_unknown_once_for_stable_nonmatching_hand(harness):
    out = harness("SETUP\nADC 2600 2600 2600 2600 2600\nRUN 5\nMARK held\nRUN 200\n")
    assert out.count("@OLED Unknown") == 1                                    # drawn once, not per frame
    assert out.index("@OLED Unknown") > out.index("@MARK held")       # only after the hold time
    assert not any(line.startswith("# GESTURE:") for line in out)


def test_host_studio_unknown_and_clear_commands(harness):
    out = harness("SETUP\nCMD UNKNOWN\nCMD CLEAR\n")
    oled = [line for line in out if line.startswith("@OLED")]
    assert oled[-2:] == ["@OLED Unknown", "@OLED Waiting..."]


def test_generate_firmware_command_survives_a_cp1252_console(tmp_path):
    """Seeding messages contain emoji; a Windows cp1252 console must not crash the command."""
    import os

    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "S2S_SPEECH": "none"}
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(root / "run_studio.py"), "--generate-firmware", "--workspace",
                        str(tmp_path / "ws")], capture_output=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr.decode("cp1252", "replace")[-2000:]
    out = r.stdout.decode("cp1252", "replace")
    assert "Added PROTOTYPE gesture WATER" in out and "Firmware written" in out
    assert '"WATER"' in (tmp_path / "ws" / "firmware" / "Sign2Speech_V2" / "gestures.h").read_text()

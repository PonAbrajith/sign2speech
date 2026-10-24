"""Regression tests for the real-ESP32 "no flex data" failure.

Proven root cause (COM9, 2026-09-30): the board ran a standalone "SIGN2SPEECH OLED TEST" sketch
that prints 5 lines at boot and then transmits nothing - so no sensor frames exist. The Studio showed
"CONNECTED - NO DATA" without saying why. These tests pin down:
  * firmware identification (V2 / V2 other build / V1 / unknown sketch / bootloader / no response)
  * the exact V2 firmware byte stream (produced by the generated firmware itself) reaching the Live Glove UI
  * per-finger independence, zeros, bad packets, stale timeout, reconnect, source isolation
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from sign2speech_v2.codegen.generator import generate
from sign2speech_v2.config.settings import Settings
from sign2speech_v2.core.frame import Source
from sign2speech_v2.core.patterns import GesturePattern
from sign2speech_v2.gestures.repository import Gesture

from conftest import Rig, data_line, make_calibration

HOST = Path(__file__).parent / "host"
BOOT_ROM = ("ets Jul 29 2019 12:21:46\r\n\r\nrst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)\r\n"
            "configsip: 0, SPIWP:0xee\r\nmode:DIO, clock div:1\r\nload:0x3fff0030,len:4640\r\n"
            "entry 0x4008059c\r\n")
# exactly what the board on COM9 printed after an EN reset
OLED_TEST_SKETCH = ("SIGN2SPEECH OLED TEST\r\nSDA = D18\r\nSCL = D19\r\nOLED FOUND AT 0x3C\r\n"
                    "OLED DISPLAY TEST COMPLETE\r\n")


def tick_for(rig, seconds, dt=0.1):
    for _ in range(int(seconds / dt)):
        rig.clock.advance(dt)
        rig.c.tick()


# ------------------------------------------------------------- the actual failure mode
def test_silent_board_is_reported_as_no_response(rig):
    rig.connect()
    assert rig.written()[0] == "STATUS"                     # the Studio asks the firmware to identify itself
    tick_for(rig, 1.0)
    assert rig.c.hardware.firmware_kind == "CHECKING"
    tick_for(rig, 2.5)
    hw = rig.c.hardware
    assert hw.firmware_kind == "NO RESPONSE" and hw.bytes_received == 0
    assert rig.c.live_sensor.link == "CONNECTED — NO DATA"
    assert rig.c.live_sensor.raw is None and rig.c.acquisition.source is Source.REAL   # never simulates


def test_oled_test_sketch_is_identified_after_reset(rig):
    rig.connect()
    tick_for(rig, 4)
    assert rig.c.reset_board().ok
    assert rig.esp.resets == 1                                # EN pulse only (no flashing API exists)
    rig.send(BOOT_ROM + OLED_TEST_SKETCH)
    tick_for(rig, 6)
    hw = rig.c.hardware
    assert hw.firmware_kind == "UNKNOWN"
    assert "OLED DISPLAY TEST COMPLETE" in hw.firmware_detail or "SIGN2SPEECH OLED TEST" in hw.firmware_detail
    assert "Flash the V2 firmware" in hw.firmware_detail
    assert hw.frames == 0 and rig.c.live_sensor.raw is None


def test_rom_boot_lines_are_not_mistaken_for_firmware(rig):
    rig.connect()
    rig.send(BOOT_ROM)
    tick_for(rig, 4)
    assert rig.c.hardware.last_text_line == ""
    assert rig.c.hardware.firmware_kind == "UNKNOWN"          # bytes but no firmware output
    assert "no readable lines" not in rig.c.hardware.firmware_detail or rig.c.hardware.bytes_received


def test_bootloader_is_identified(rig):
    rig.connect()
    rig.send("rst:0x1 (POWERON_RESET),boot:0x3 (DOWNLOAD_BOOT(UART0/UART1/SDIO_REI_REO_V2))\r\n"
             "waiting for download\r\n")
    assert rig.c.hardware.firmware_kind == "BOOTLOADER (download mode)"


def test_old_v1_firmware_is_identified(rig):
    rig.connect()
    rig.send("# Sign2Speech ESP32 firmware 3fa9c1d2e4b5\n")
    assert rig.c.hardware.firmware_kind == "OLD FIRMWARE"


def test_v2_firmware_matching_generated_build(rig):
    fw = generate([], None, Settings())
    fw.write(rig.ws.firmware_dir)
    rig.connect()
    rig.send(f"# FIRMWARE: Sign2Speech V2 {fw.fingerprint}\n")
    assert rig.c.hardware.firmware_kind == "EXPECTED V2"
    rig.c.reset_board()
    rig.send("# FIRMWARE: Sign2Speech V2 0123456789ab\n")
    assert rig.c.hardware.firmware_kind == "V2 DIFFERENT BUILD"


def test_reset_requires_real_connection(rig):
    assert not rig.c.reset_board().ok
    rig.c.start_simulation()
    assert not rig.c.reset_board().ok


# ------------------------------------------------------------- exact V2 firmware stream end-to-end
GXX = shutil.which("g++")


@pytest.fixture(scope="module")
def firmware_stream(tmp_path_factory):
    """Bytes produced by the GENERATED V2 firmware itself (run on the PC)."""
    if not GXX:
        pytest.skip("g++ not available")
    d = tmp_path_factory.mktemp("fwstream")
    gestures = [Gesture(name="HELLO", display_text="Hello", finger_pattern=GesturePattern.parse("OBOBO"))]
    fw = generate(gestures, make_calibration(), Settings())
    sketch = fw.write(d)
    exe = d / "harness.exe"
    r = subprocess.run([GXX, "-std=gnu++14", "-I", str(HOST / "stub"), "-I", str(sketch),
                        str(HOST / "harness.cpp"), "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    script = ("ADC 812 640 700 655 590\nSETUP\nRUN 5\n"
              "ADC 2600 640 700 655 590\nRUN 5\n"          # bend Thumb only
              "ADC 812 640 700 655 590\nRUN 5\n"           # release
              "ADC 812 2500 700 655 590\nRUN 5\n")         # bend Index only
    out = subprocess.run([str(exe)], input=script, capture_output=True, text=True, timeout=60).stdout
    lines = [l for l in out.splitlines() if not l.startswith("@")]      # drop harness markers
    return fw, lines


def test_exact_v2_firmware_stream_reaches_live_glove_ui(firmware_stream, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    fw, lines = firmware_stream
    QApplication.instance() or QApplication([])
    rig = Rig(tmp_path)
    fw.write(rig.ws.firmware_dir)
    win = MainWindow(rig.c, start_timers=False)
    rig.connect()
    boot = [l for l in lines if l.startswith("#")]
    data = [l for l in lines if l[:1].isdigit()]
    assert len(data) == 20
    for l in boot:
        rig.esp.feed(l + "\r\n")
    rig.c.tick()
    assert rig.c.hardware.firmware_kind == "EXPECTED V2"

    live = win.pages["Live Glove"]
    win.nav.setCurrentRow(PAGE_ORDER.index("Live Glove"))
    seen = []
    for l in data:
        rig.send(l + "\r\n")
        win.refresh()
        seen.append(tuple(int(x.text().split()[1]) for x in live.meters.raw))
    assert live.banner.text() == "REAL ESP32 — CONNECTED — DATA RECEIVING"
    assert rig.c.hardware.frames == 20
    base, thumb, index = (812, 640, 700, 655, 590), (2600, 640, 700, 655, 590), (812, 2500, 700, 655, 590)
    assert seen[:5] == [base] * 5
    assert seen[5:10] == [thumb] * 5          # Thumb changed, nothing else
    assert seen[10:15] == [base] * 5          # released
    assert seen[15:20] == [index] * 5         # Index changed, nothing else
    diag = win.pages["Hardware Diagnostics"]
    win.nav.setCurrentRow(PAGE_ORDER.index("Hardware Diagnostics"))
    win.refresh()
    text = diag.real_text.text()
    assert "Serial:            RECEIVING" in text and "Flex:              DATA RECEIVING" in text
    assert "Firmware:          EXPECTED V2" in text and "Valid frames:      20" in text
    assert "Invalid frames:    0" in text and "Mode:                     REAL HARDWARE" in text
    # the five numbers shown are exactly the last packet's five flex fields
    last = data[-1].split(",")[:5]
    rows = diag.real_fingers.text().splitlines()[1:]
    assert [r.split()[1] for r in rows] == last
    assert [r.split()[0] for r in rows] == ["Thumb:", "Index:", "Middle:", "Ring:", "Little:"]
    win.close()


# ------------------------------------------------------------- packet-level robustness
V2_LINE = "812,640,700,655,590,0.001,0.002,1.000,-0.95,0.00,0.00\r\n"   # as printed by the V2 firmware


def test_v2_packet_accepted_in_finger_order(rig):
    rig.connect()
    rig.send(V2_LINE)
    assert rig.c.live_sensor.raw == (812, 640, 700, 655, 590)
    assert rig.c.live_sensor.link == "LIVE"


@pytest.mark.parametrize("finger", range(5))
def test_changing_one_finger_changes_only_that_finger(calibrated_rig, finger):
    rig = calibrated_rig
    base = [700] * 5
    rig.frames(base, 6)
    before = rig.c.live_sensor.bending
    moved = list(base)
    moved[finger] = 2600
    rig.frames(moved, 6)
    ls = rig.c.live_sensor
    assert [i for i in range(5) if ls.raw[i] != 700] == [finger]
    assert [i for i in range(5) if ls.bending[i] != before[i]] == [finger]
    assert ls.finger_states[finger].word == "BENT"


def test_all_zero_packet_is_valid_and_flagged(rig):
    rig.connect()
    rig.send("0,0,0,0,0,0.000,0.000,0.000,0.00,0.00,0.00\n")
    assert rig.c.live_sensor.raw == (0, 0, 0, 0, 0) and rig.c.live_sensor.live


@pytest.mark.parametrize("bad", ["812,640,700,655,0.001,0.002,1.000,-0.95,0.00,0.00\n",          # missing field
                                 "812,640,700,655,590,1,0.001,0.002,1.000,-0.95,0.00,0.00\n",    # extra field
                                 "812,640,x,655,590,0.001,0.002,1.000,-0.95,0.00,0.00\n",        # malformed
                                 "812,640,700,655,5000,0.001,0.002,1.000,-0.95,0.00,0.00\n"])   # out of range
def test_invalid_packets_rejected_without_touching_live_values(rig, bad):
    rig.connect()
    rig.send(V2_LINE)
    rig.send(bad)
    assert rig.c.live_sensor.raw == (812, 640, 700, 655, 590)
    assert rig.c.hardware.decode_errors == 1 and rig.c.hardware.frames == 1


def test_stream_stop_then_resume_and_reconnect(rig):
    rig.connect()
    rig.send(V2_LINE)
    tick_for(rig, 1.5)
    assert rig.c.live_sensor.link == "WAITING FOR LIVE DATA" and rig.c.live_sensor.raw is None
    rig.send(V2_LINE)
    assert rig.c.live_sensor.live
    rig.esp.fail_reads = True                                   # cable pulled
    rig.c.tick()
    assert rig.c.live_sensor.link == "WAITING FOR GLOVE"
    rig.connect()                                               # plugged back in
    rig.send(V2_LINE.replace("812", "900"))
    assert rig.c.live_sensor.raw[0] == 900 and rig.c.live_sensor.source is Source.REAL


def test_real_connection_never_switches_to_simulation(rig):
    rig.connect()
    tick_for(rig, 10)                                           # silent board for 10 s
    assert rig.c.acquisition.source is Source.REAL and rig.c.acquisition.simulator is None
    assert not rig.c.start_simulation().ok


def test_stored_gestures_never_replace_live_values(calibrated_rig):
    rig = calibrated_rig
    rig.hold("OBOBO", 6)
    rig.c.capture_from_live()
    rig.c.editor.name, rig.c.editor.display_text = "HELLO", "Hello"
    rig.c.save_editor()
    rig.c.edit_gesture(rig.c.repo.by_name("HELLO").id)
    rig.frames((650, 660, 670, 680, 690), 6)
    assert rig.c.live_sensor.raw == (650, 660, 670, 680, 690)
    assert str(rig.c.detection.live_pattern) == "O O O O O"


def test_serial_monitor_shows_unknown_text_lines(rig):
    rig.connect()
    rig.send(OLED_TEST_SKETCH + V2_LINE)
    lines = [text for data, text in rig.c.serial_lines if not data]
    assert "SIGN2SPEECH OLED TEST" in lines
    assert [text for data, text in rig.c.serial_lines if data] == [V2_LINE.strip()]


def test_diagnostics_reports_mpu_and_oled_only_as_the_firmware_reported(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow

    QApplication.instance() or QApplication([])
    rig = Rig(tmp_path)
    win = MainWindow(rig.c, start_timers=False)
    rig.connect()
    win.nav.setCurrentRow(PAGE_ORDER.index("Hardware Diagnostics"))
    win.refresh()
    text = win.pages["Hardware Diagnostics"].real_text.text()
    assert "MPU6050:           NO REPORT" in text and "OLED:              NO REPORT" in text
    assert "ESP32:             CONNECTED" in text
    rig.send("# MPU6050: OK addr=0x68 WHO_AM_I=0x70 compatible\n# OLED: ERROR not detected\n")
    win.refresh()
    text = win.pages["Hardware Diagnostics"].real_text.text()
    assert "MPU6050:           OK WHO_AM_I=0x70" in text and "OLED:              OLED NOT DETECTED" in text
    win.close()
    rig.c.close()

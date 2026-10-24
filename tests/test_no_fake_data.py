"""Production code never shows sensor values that did not come from the serial stream.

The only two SensorFrame producers are parse_data_line() (bytes from the serial port) and the
SimulatedGlove (explicit Start Simulation only). These tests pin that down at state and UI level.
"""

from __future__ import annotations

import csv
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sign2speech_v2.calibration.model import Calibration, FingerCalibration  # noqa: E402
from sign2speech_v2.config.pins import FINGER_NAMES  # noqa: E402
from sign2speech_v2.core.frame import Source  # noqa: E402
from sign2speech_v2.core.patterns import GesturePattern  # noqa: E402
from sign2speech_v2.gestures.repository import Gesture  # noqa: E402
from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow  # noqa: E402

from conftest import Rig, data_line, make_calibration  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"
DASH = "—"


@pytest.fixture
def ui(tmp_path):
    QApplication.instance() or QApplication([])
    rig = Rig(tmp_path)
    rig.win = MainWindow(rig.c, start_timers=False)
    yield rig
    rig.win.close()


def page(rig, name):
    rig.win.nav.setCurrentRow(PAGE_ORDER.index(name))
    rig.win.refresh()
    return rig.win.pages[name]


def meter_texts(p):
    m = p.meters
    return [(m.raw[i].text(), m.filt[i].text(), m.pct[i].text(), m.state[i].text()) for i in range(5)]


def assert_no_sensor_values(rig):
    ls = rig.c.live_sensor
    assert (ls.raw, ls.filtered, ls.bending, ls.finger_states, ls.accel, ls.gyro) == (None,) * 6
    live = page(rig, "Live Glove")
    assert meter_texts(live) == [(DASH, DASH, DASH, DASH)] * 5
    assert live.live_pattern.text() == DASH
    assert live.live_detection.text() == "WAITING FOR DATA"
    assert all(v.text() == DASH for v in live.imu)
    diag = page(rig, "Hardware Diagnostics")
    assert diag.real_fingers.text() == "Last RAW FLEX:\nNO DATA"
    assert page(rig, "Dashboard").detection.text() == "WAITING FOR DATA"


# 1
def test_startup_without_esp32_shows_no_values(ui):
    assert ui.c.live_sensor == type(ui.c.live_sensor)()          # empty state at start-up
    assert_no_sensor_values(ui)
    assert ui.win.source_badge.text().strip() == "REAL HARDWARE"
    assert page(ui, "Live Glove").banner.text() == "WAITING FOR GLOVE"


# 2
def test_connected_but_silent_shows_no_values(ui):
    ui.connect()
    for _ in range(100):
        ui.clock.advance(0.1)
        ui.c.tick()
    assert_no_sensor_values(ui)
    assert "Serial:            NO DATA" in page(ui, "Hardware Diagnostics").real_text.text()
    assert "CONNECTED — NO DATA" in page(ui, "Live Glove").banner.text()


# 3
def test_ui_shows_exactly_the_packet_values(ui):
    ui.connect()
    ui.frames((120, 450, 800, 50, 300))
    live = page(ui, "Live Glove")
    assert [t[0] for t in meter_texts(live)] == ["ADC 120", "ADC 450", "ADC 800", "ADC 50", "ADC 300"]
    assert [t[1] for t in meter_texts(live)] == ["WAITING"] * 5          # 1 sample: no filtered value yet
    ui.frames((120, 450, 800, 50, 300), 4)
    live = page(ui, "Live Glove")
    assert [t[1] for t in meter_texts(live)] == ["120", "450", "800", "50", "300"]


# 4, 5
@pytest.mark.parametrize("finger", [0, 1])
def test_changing_one_packet_field_changes_only_that_row(ui, finger):
    ui.connect()
    base = [700, 710, 720, 730, 740]
    ui.frames(base, 5)
    before = [t[0] for t in meter_texts(page(ui, "Live Glove"))]
    moved = list(base)
    moved[finger] = 2222
    ui.frames(moved)
    after = [t[0] for t in meter_texts(page(ui, "Live Glove"))]
    assert [i for i in range(5) if before[i] != after[i]] == [finger]
    assert after[finger] == "ADC 2222"


# 6
def test_stale_values_disappear(ui):
    ui.connect()
    ui.frames((500,) * 5, 6)
    ui.clock.advance(1.5)
    ui.c.tick()
    assert_no_sensor_values(ui)
    live = page(ui, "Live Glove")
    assert "WAITING FOR LIVE DATA" in live.banner.text()


# 7, 8, 15
def test_production_app_never_loads_the_simulator(tmp_path):
    """Launch the real app wiring (real serial opener, real port list) in a fresh interpreter."""
    code = f"""
import sys
sys.path.insert(0, {str(SRC)!r})
from pathlib import Path
from PySide6.QtWidgets import QApplication
from sign2speech_v2.config.settings import Settings, Workspace
from sign2speech_v2.controller import StudioController
from sign2speech_v2.ui.main_window import MainWindow
app = QApplication([])
c = StudioController(Workspace(Path({str(tmp_path)!r})), Settings(preferred_port="COM_DOES_NOT_EXIST_S2S",
                     auto_connect=True), port_lister=lambda: [])
w = MainWindow(c, start_timers=False)
c.startup()
for _ in range(50):
    c.tick()
info = c.runtime_info()
print("SIMMOD", "sign2speech_v2.acquisition.simulator" in sys.modules)
print("SOURCE", c.acquisition.source)
print("RAW", c.live_sensor.raw)
print("PKG", info["Package path"])
print("MODE", info["Mode"])
w.close(); c.close()
"""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "S2S_SPEECH": "none"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
    assert lines["SIMMOD"] == "False"
    assert lines["SOURCE"] == "None" and lines["RAW"] == "None"
    assert lines["MODE"] == "REAL HARDWARE"
    assert Path(lines["PKG"]) == SRC / "sign2speech_v2"


def test_simulation_starts_only_on_request_and_is_labelled(ui):
    ui.connect()
    assert not ui.c.start_simulation().ok and ui.c.acquisition.source is Source.REAL
    ui.c.disconnect()
    assert ui.c.acquisition.source is None
    ui.win.btn_sim.click()
    assert ui.c.acquisition.source is Source.SIMULATION
    assert ui.c.runtime_info()["Mode"] == "SIMULATION"


# 9, 11
def test_gestures_and_selection_never_touch_live_state(ui):
    ui.c.set_calibration(make_calibration())
    g = ui.c.repo.save(Gesture(name="HELLO", display_text="Hello", finger_pattern=GesturePattern.parse("OBOOB")))
    ui.c.reload_gestures()
    before = dataclasses.asdict(ui.c.live_sensor)
    ui.c.select_gesture(g.id)
    ui.c.edit_gesture(g.id)
    ui.c.save_editor()
    assert dataclasses.asdict(ui.c.live_sensor) == before
    assert page(ui, "Live Glove").live_pattern.text() == DASH       # no data -> no pattern, not OBOOB
    ui.connect()
    ui.hold("OOOOO", 12)
    assert str(ui.c.detection.live_pattern) == "O O O O O"
    assert ui.c.live_sensor.raw == (700,) * 5


# 10
def test_calibration_numbers_never_appear_as_live_adc(ui):
    ui.c.set_calibration(Calibration(tuple(FingerCalibration(n, 181, 1090, 4.0) for n in FINGER_NAMES)))
    ui.connect()
    ui.c.tick()
    live = page(ui, "Live Glove")
    assert [t[0] for t in meter_texts(live)] == [DASH] * 5
    ui.frames((500,) * 5, 5)
    live = page(ui, "Live Glove")
    assert [t[0] for t in meter_texts(live)] == ["ADC 500"] * 5
    assert "181" not in " ".join(t[0] + t[1] for t in meter_texts(live))
    cal = page(ui, "Calibration")
    titles = [lab.text() for lab in cal.findChildren(type(cal.which))]
    assert any(t.startswith("CURRENT CALIBRATION") and "NOT live sensor readings" in t for t in titles)
    assert cal.table.item(0, 1).text() == "181" and cal.table.item(0, 2).text() == "1090"


# 12, 13
@pytest.mark.parametrize("bad", ["1,2,3\n", "1,2,3,4,5,6,7,8,9,10,11,12\n", "hello world\n", "\xff\xfe\n"])
def test_bad_serial_input_never_creates_values(ui, bad):
    ui.connect()
    ui.send(bad)
    assert ui.c.live_sensor.raw is None
    assert_no_sensor_values(ui)


# 14
def test_reconnect_resumes_real_values(ui):
    ui.connect()
    ui.frames((600,) * 5, 5)
    ui.esp.fail_reads = True
    ui.c.tick()
    assert_no_sensor_values(ui)
    ui.connect()
    ui.frames((650, 651, 652, 653, 654))
    assert ui.c.live_sensor.raw == (650, 651, 652, 653, 654)


def test_hardware_trace_records_the_exact_serial_line(ui):
    ui.c.set_calibration(make_calibration())
    ui.connect()
    assert ui.c.start_trace().ok
    line = "2600,700,2600,700,2600,0.012,-0.020,0.998,1.25,-0.50,0.75"
    for _ in range(6):
        ui.send(line + "\r\n")
    res = ui.c.stop_trace()
    path = Path(res.message.split(": ", 1)[1])
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 6
    assert rows[-1]["raw_line"] == line and rows[-1]["port"] == "COM9" and rows[-1]["source"] == "REAL"
    assert [rows[-1][n] for n in FINGER_NAMES] == ["2600", "700", "2600", "700", "2600"]
    assert rows[-1]["finger_states"] == "BOBOB" and rows[-1]["live_pattern"] == "B O B O B"
    assert rows[0]["finger_states"] == "" and rows[0]["detection"] == "WAITING FOR DATA"
    assert str(path).startswith(str(ui.ws.root))

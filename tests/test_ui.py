"""Headless UI tests (QT_QPA_PLATFORM=offscreen) against a fake ESP32."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sign2speech_v2.ui.main_window import PAGE_ORDER, MainWindow  # noqa: E402
from sign2speech_v2.ui.theme import STYLE  # noqa: E402

from conftest import Rig, make_calibration  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(STYLE)
    return a


@pytest.fixture
def ui(app, tmp_path):
    rig = Rig(tmp_path)
    rig.c.set_calibration(make_calibration())
    win = MainWindow(rig.c, start_timers=False)
    win.show()
    rig.win = win
    yield rig
    win.close()


def show(rig, page):
    rig.win.nav.setCurrentRow(PAGE_ORDER.index(page))
    rig.win.refresh()
    return rig.win.pages[page]


def test_all_eight_pages_exist_and_render(ui):
    assert list(ui.win.pages) == ["Dashboard", "Live Glove", "Calibration", "Gesture Builder", "Gesture Library",
                                  "Speech", "Hardware Diagnostics", "Code Generator"]
    for name in PAGE_ORDER:
        show(ui, name)
    assert ui.win.stack.currentWidget() is ui.win.pages["Code Generator"]


def test_live_glove_is_the_start_page_and_shows_waiting(ui):
    assert ui.win.stack.currentWidget() is ui.win.pages["Live Glove"]
    page = show(ui, "Live Glove")
    assert page.banner.text() == "WAITING FOR GLOVE"
    assert page.meters.raw[0].text() == "—"


def test_live_glove_shows_real_values_immediately(ui):
    ui.connect()
    ui.frames((700, 2600, 700, 2600, 700), 12)
    page = show(ui, "Live Glove")
    assert page.banner.text().startswith("REAL ESP32")
    assert [l.text() for l in page.meters.raw] == ["ADC 700", "ADC 2600", "ADC 700", "ADC 2600", "ADC 700"]
    assert [l.text() for l in page.meters.state] == ["OPEN", "BENT", "OPEN", "BENT", "OPEN"]
    assert page.meters.pct[1].text() == "100%"
    assert page.live_pattern.text() == "O B O B O"
    assert page.live_detection.text() == "UNKNOWN"
    assert ui.win.source_badge.text().strip() == "REAL ESP32"


def test_stale_stream_shows_waiting_for_live_data(ui):
    ui.connect()
    ui.frames((700,) * 5, 5)
    ui.clock.advance(2)
    ui.c.tick()
    page = show(ui, "Live Glove")
    assert page.banner.text().startswith("REAL ESP32 — WAITING FOR LIVE DATA")
    assert page.meters.raw[0].text() == "—"


def test_builder_new_gesture_and_capture_workflow(ui):
    ui.connect()
    b = show(ui, "Gesture Builder")
    b.btn_new.click()
    assert b.editor_title.text() == "New gesture (unsaved)"
    assert b.name.text() == "" and b.editor_pattern.text() == "(not captured)"
    ui.hold("OBOBO", 5)
    ui.win.refresh()
    assert b.live_pattern.text() == "O B O B O"
    b.btn_capture.click()
    assert b.editor_pattern.text() == "O B O B O"
    b.name.setText("HELLO")
    b.name.textEdited.emit("HELLO")
    b.display.setText("Hello")
    b.display.textEdited.emit("Hello")
    b.btn_save.click()
    assert ui.c.repo.by_name("HELLO").display_text == "Hello"
    assert b.editor_title.text() == "Editing HELLO"
    ui.hold("OOOOO", 30)  # neutral hand: arms gesture speech
    ui.hold("OBOBO", 12)
    ui.win.refresh()
    assert b.live_detection.text() == "HELLO"
    assert ui.engine.spoken == [("Hello", "Neutral")]
    # + NEW GESTURE clears the editor, live detection is unchanged
    b.btn_new.click()
    ui.win.refresh()
    assert b.name.text() == "" and b.display.text() == "" and b.editor_pattern.text() == "(not captured)"
    assert b.live_detection.text() == "HELLO" and b.live_pattern.text() == "O B O B O"


def test_editor_target_dropdown_does_not_change_live_detection(ui):
    ui.connect()
    ui.hold("OBOBO", 6)
    ui.c.capture_from_live()
    ui.c.editor.name, ui.c.editor.display_text = "HELLO", "Hello"
    ui.c.save_editor()
    ui.c.new_gesture()
    ui.hold("BOOOO", 6)
    ui.c.capture_from_live()
    ui.c.editor.name, ui.c.editor.display_text = "WATER", "Water"
    ui.c.save_editor()
    b = show(ui, "Gesture Builder")
    idx = b.target.findData(ui.c.repo.by_name("WATER").id)
    b.target.setCurrentIndex(idx)
    b.target.activated.emit(idx)
    ui.hold("OBOBO", 12)
    ui.win.refresh()
    assert b.editor_title.text() == "Editing WATER"
    assert b.live_detection.text() == "HELLO"
    live = show(ui, "Live Glove")
    assert live.live_detection.text() == "HELLO" and live.saved_pattern.text() == "O B O B O"


def test_library_actions(ui, tmp_path):
    ui.connect()
    ui.hold("OBOBO", 6)
    ui.c.capture_from_live()
    ui.c.editor.name, ui.c.editor.display_text = "HELLO", "Hello"
    ui.c.save_editor()
    lib = show(ui, "Gesture Library")
    assert lib.table.rowCount() == 1
    assert [lib.table.item(0, c).text() for c in range(7)] == ["HELLO", "O B O B O", "(Hello)", "", "Neutral", "yes", "User"]
    lib.table.selectRow(0)
    lib.buttons["Disable"].click()
    assert lib.table.item(0, 5).text() == "no" and ui.c.store.enabled_gestures == []
    lib.buttons["Enable"].click()
    lib._confirm = lambda text: True
    lib.table.selectRow(0)
    lib.buttons["Delete"].click()
    assert lib.table.rowCount() == 0 and ui.c.calibration.calibrated


def test_simulation_is_explicit_and_labelled(ui):
    assert ui.c.acquisition.source is None
    ui.win.btn_sim.click()
    assert ui.c.acquisition.source.value == "SIMULATION"
    assert not ui.win.btn_connect.isEnabled()
    for _ in range(5):
        ui.clock.advance(0.02)
        ui.c.tick()
    page = show(ui, "Live Glove")
    assert page.banner.text().startswith("SIMULATION")
    assert ui.win.source_badge.text().strip() == "SIMULATION"
    ui.win.btn_sim.click()
    assert ui.c.acquisition.source is None


def test_diagnostics_all_zero_banner(ui):
    ui.connect()
    ui.frames((0, 0, 0, 0, 0), 20)
    d = show(ui, "Hardware Diagnostics")
    assert d.all_zero.text() == "ALL ADC = 0 — CHECK FLEX WIRING"
    assert d.adc.item(0, 8).text() == "ZERO" and d.adc.item(0, 1).text() == "GPIO32"
    dash = show(ui, "Dashboard")
    assert dash.tiles["FLEX"].value.text() == "CHECK FLEX WIRING"


def test_code_generator_page_writes_firmware(ui):
    page = show(ui, "Code Generator")
    page._generate()
    out = ui.ws.firmware_dir / "Sign2Speech_V2"
    assert (out / "Sign2Speech_V2.ino").exists() and (out / "hardware_lock.h").exists()
    assert page.file_box.count() == 4
    assert "{32, 35, 34, 39, 36}" in page.summary.text()

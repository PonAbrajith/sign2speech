"""End-to-end live pipeline through StudioController with a fake ESP32.

Covers: automatic live values, calibration from the stream, live pattern,
+ New Gesture, capture, save, live detection, stability, speech once,
OLED commands, stale data, disconnection, simulation isolation, and that
the selected/edited gesture never drives live detection.
"""

import pytest

from sign2speech_v2.config.settings import Settings
from sign2speech_v2.core.frame import Source
from sign2speech_v2.core.patterns import GesturePattern, LivePattern, PatternToken

from conftest import CLOSED_ADC, OPEN_ADC, Rig, data_line, make_calibration, pattern_flex


def create_gesture(rig, compact, name, text=None):
    c = rig.c
    c.new_gesture()
    rig.hold(compact, 6)
    assert c.capture_from_live().ok
    c.editor.name = name
    c.editor.display_text = text or name.title()
    res = c.save_editor()
    assert res.ok, res.message
    return c.repo.by_name(name)


# ------------------------------------------------------------ live values
def test_live_values_appear_automatically_without_calibration(rig):
    rig.connect()
    rig.frames((812, 640, 700, 655, 590))
    ls = rig.c.live_sensor
    assert ls.live and ls.source is Source.REAL and ls.link == "LIVE"
    assert ls.raw == (812, 640, 700, 655, 590)                # RAW immediately, exactly as received
    assert ls.filtered is None                                  # filter window not full: no invented value
    assert rig.c.detection.display == "WAITING FOR DATA"
    rig.frames((812, 640, 700, 655, 590), 4)                    # 5 real samples
    assert ls.filtered == pytest.approx((812, 640, 700, 655, 590))
    assert ls.bending is None and ls.finger_states is None
    assert rig.c.detection.display == "NOT CALIBRATED"


def test_each_finger_value_changes_individually(calibrated_rig):
    rig = calibrated_rig
    base = [OPEN_ADC] * 5
    rig.frames(base, 5)
    for finger in range(5):
        moved = list(base)
        moved[finger] = CLOSED_ADC
        rig.frames(moved, 5)
        raw = rig.c.live_sensor.raw
        assert raw[finger] == CLOSED_ADC
        assert [v for i, v in enumerate(raw) if i != finger] == [OPEN_ADC] * 4
        rig.frames(base, 5)


def test_waiting_states(rig):
    assert rig.c.live_sensor.link == "WAITING FOR GLOVE"
    rig.connect()
    rig.c.tick()
    assert rig.c.live_sensor.link == "CONNECTED — NO DATA"


def test_stale_data_is_not_shown_as_live(calibrated_rig):
    rig = calibrated_rig
    rig.hold("OBOBO", 5)
    assert rig.c.live_sensor.live
    rig.clock.advance(1.5)                   # stream stops
    rig.c.tick()
    ls = rig.c.live_sensor
    assert not ls.live and ls.raw is None and ls.link == "WAITING FOR LIVE DATA"
    assert rig.c.detection.live_pattern is None
    rig.hold("OBOBO", 1)                     # data returns
    assert rig.c.live_sensor.live


def test_unplugged_glove(calibrated_rig):
    rig = calibrated_rig
    rig.hold("OBOBO", 5)
    rig.esp.fail_reads = True
    rig.clock.advance(0.02)
    rig.c.tick()
    assert rig.c.acquisition.source is None
    assert rig.c.live_sensor.link == "WAITING FOR GLOVE" and rig.c.live_sensor.raw is None


# ------------------------------------------------------------ calibration
def test_calibration_from_the_live_stream(rig):
    rig.connect()
    rig.frames(pattern_flex("OOOOO"), 2)
    assert rig.c.start_capture("open").ok
    for i in range(110):
        rig.frames([OPEN_ADC + (i % 3)] * 5)
    assert rig.c.calibration.open_result.ok
    assert rig.c.start_capture("closed").ok
    for i in range(160):
        rig.frames([CLOSED_ADC - (i % 3)] * 5)
    assert rig.c.calibration.closed_result.ok, rig.c.calibration.closed_result.errors
    assert rig.c.save_calibration().ok
    assert rig.ws.calibration_path.exists()
    cs = rig.c.calibration
    assert cs.open == pytest.approx((OPEN_ADC,) * 5, abs=2) and cs.range[0] > 1800
    # monitoring continues with OPEN/BENT, no recalibration needed
    rig.hold("BOBOB", 6)
    assert str(rig.c.detection.live_pattern) == "B O B O B"


def test_calibration_capture_needs_live_data(rig):
    assert not rig.c.start_capture("open").ok


def test_calibration_persists_across_restart(tmp_path):
    r1 = Rig(tmp_path)
    r1.c.set_calibration(make_calibration())
    r1.c.close()
    r2 = Rig(tmp_path)
    assert r2.c.calibration.calibrated and r2.c.classifier is not None
    r2.c.close()


# ------------------------------------------------------------ live pattern
def test_live_pattern_reflects_current_hand_and_never_x(calibrated_rig):
    rig = calibrated_rig
    rig.hold("OBOBO", 6)
    lp = rig.c.detection.live_pattern
    assert isinstance(lp, LivePattern) and str(lp) == "O B O B O"
    rig.hold("BBBBB", 6)
    assert str(rig.c.detection.live_pattern) == "B B B B B"
    assert "X" not in rig.c.detection.live_pattern.compact()


# ------------------------------------------------------------ gesture builder
def test_new_gesture_clears_editor_but_not_live_state(calibrated_rig):
    rig = calibrated_rig
    create_gesture(rig, "OBOBO", "HELLO")
    rig.c.edit_gesture(rig.c.repo.by_name("HELLO").id)
    rig.c.editor.description = "wave"
    rig.hold("OBOBO", 12)
    before = (rig.c.live_sensor.raw, rig.c.detection.live_pattern, rig.c.detection.display)
    rig.c.new_gesture()
    e = rig.c.editor
    assert e.is_new and e.title == "New gesture (unsaved)"
    assert (e.name, e.display_text, e.description, e.pattern, e.samples_recorded) == ("", "", "", None, 0)
    assert rig.c.store.selected_id is None
    assert (rig.c.live_sensor.raw, rig.c.detection.live_pattern, rig.c.detection.display) == before
    rig.hold("OBOBO", 2)
    assert rig.c.live_sensor.live                      # monitoring keeps running


def test_capture_uses_current_hand_not_previous_gesture(calibrated_rig):
    rig = calibrated_rig
    create_gesture(rig, "OBOBO", "HELLO")
    rig.c.new_gesture()
    rig.hold("BOOOO", 6)
    assert rig.c.capture_from_live().ok
    assert rig.c.editor.pattern == GesturePattern.parse("BOOOO")
    rig.hold("BBBBB", 6)                                # later movement does not change the capture
    assert rig.c.editor.pattern == GesturePattern.parse("BOOOO")


def test_capture_refused_without_live_data_or_calibration(rig):
    assert not rig.c.capture_from_live().ok
    rig.connect()
    rig.frames((800,) * 5, 3)
    assert "NOT CALIBRATED" in rig.c.capture_from_live().message


def test_save_multiple_gestures_and_edit_with_x(calibrated_rig):
    rig = calibrated_rig
    create_gesture(rig, "OBOBO", "HELLO")
    create_gesture(rig, "BOOOO", "WATER", "Water")
    g = rig.c.repo.by_name("WATER")
    rig.c.edit_gesture(g.id)
    rig.c.set_editor_token(4, PatternToken.ANY)
    assert rig.c.save_editor().ok
    assert str(rig.c.repo.by_name("WATER").finger_pattern) == "B O O O X"
    assert [x.name for x in rig.c.store.gestures] == ["HELLO", "WATER"]


# ------------------------------------------------------------ live detection
def test_full_acceptance_workflow(calibrated_rig):
    rig = calibrated_rig
    create_gesture(rig, "OBOBO", "HELLO", "Hello")
    rig.c.new_gesture()
    rig.hold("OOOOO", 30)  # neutral hand: arms gesture speech
    assert rig.c.detection.display == "UNKNOWN" and rig.c.speech_state.armed
    frames = 0
    while rig.c.detection.display != "HELLO":           # filter settles after a few frames
        rig.hold("OBOBO", 1)
        frames += 1
        assert frames < 6
    assert rig.c.detection.stability_count == 1        # first matching frame
    rig.hold("OBOBO", 8)                                # 9 matching frames: not yet
    assert rig.c.detection.display == "HELLO" and not rig.c.detection.accepted
    assert rig.engine.spoken == []
    rig.hold("OBOBO", 1)                                # 10th matching sample: accepted
    d = rig.c.detection
    assert d.accepted and d.accepted_gesture == "HELLO" and d.accept_events == 1
    assert str(d.live_pattern) == "O B O B O" and str(d.saved_pattern) == "O B O B O"
    assert rig.engine.spoken == [("Hello", "Neutral")]
    assert rig.c.speech_state.last_spoken_gesture == "HELLO"
    assert "SHOW HELLO" in rig.written()                # ESP32 OLED shows the recognized gesture
    # change hand shape
    rig.hold("BBBBB", 10)
    assert rig.c.detection.display == "UNKNOWN"
    after = lambda: rig.written()[rig.written().index("SHOW HELLO"):]  # noqa: E731
    rig.hold("BBBBB", 50)                               # release + filter + 10 stable frames
    assert after().count("UNKNOWN") == 1                # ESP32 OLED: Unknown, sent once
    rig.clock.advance(2)                                # stream stops
    rig.c.tick()
    assert "CLEAR" in after()[after().index("UNKNOWN"):]  # ESP32 OLED: Waiting...


def test_speech_once_while_holding_and_again_after_leaving(calibrated_rig):
    rig = calibrated_rig
    create_gesture(rig, "OBOBO", "HELLO", "Hello")
    rig.hold("OOOOO", 30)  # neutral hand: arms gesture speech
    rig.hold("OBOBO", 300)                              # 6 s holding
    assert rig.engine.spoken == [("Hello", "Neutral")]
    rig.hold("OOOOO", 15)                               # leave (filter delay + release frames)
    rig.hold("OBOBO", 15)                               # perform it again
    assert rig.engine.spoken == [("Hello", "Neutral")] * 2
    assert rig.written().count("SHOW HELLO") == 2


def test_x_pattern_detection_shows_x_only_on_saved_line(calibrated_rig):
    rig = calibrated_rig
    create_gesture(rig, "OBOBO", "HELLO")
    g = rig.c.repo.by_name("HELLO")
    rig.c.edit_gesture(g.id)
    rig.c.set_editor_token(0, PatternToken.ANY)
    rig.c.save_editor()
    rig.hold("BBOBO", 12)
    d = rig.c.detection
    assert d.display == "HELLO"
    assert str(d.live_pattern) == "B B O B O"           # real states
    assert str(d.saved_pattern) == "X B O B O"          # X only on the stored line


def test_ambiguous_detection(calibrated_rig):
    rig = calibrated_rig
    create_gesture(rig, "OBOBO", "A")
    g = rig.c.repo.by_name("A")
    create_gesture(rig, "OBOBO", "B_SIGN")
    rig.c.edit_gesture(rig.c.repo.by_name("B_SIGN").id)
    rig.c.set_editor_token(0, PatternToken.ANY)
    rig.c.save_editor()
    rig.hold("OBOBO", 20)
    assert rig.c.detection.display == "AMBIGUOUS"
    assert set(rig.c.detection.candidates) == {"A", "B_SIGN"}
    assert rig.engine.spoken == []
    assert g is not None


def test_no_saved_gestures_is_unknown(calibrated_rig):
    calibrated_rig.hold("OBOBO", 20)
    assert calibrated_rig.c.detection.display == "UNKNOWN"


def test_selected_gesture_never_overrides_live_detection(calibrated_rig):
    rig = calibrated_rig
    create_gesture(rig, "OBOBO", "HELLO")
    create_gesture(rig, "BOOOO", "WATER")
    water = rig.c.repo.by_name("WATER")
    rig.c.select_gesture(water.id)                      # library selection
    rig.c.edit_gesture(water.id)                        # editor target
    rig.hold("OOOOO", 30)  # neutral hand: arms gesture speech
    rig.hold("OBOBO", 12)
    assert rig.c.detection.display == "HELLO"
    assert rig.c.editor.name == "WATER"
    rig.c.new_gesture()                                 # editor empty
    rig.hold("OOOOO", 12)
    assert rig.c.detection.display == "UNKNOWN"
    assert rig.engine.spoken == [("Hello", "Neutral")]


def test_disabled_gesture_not_detected_and_delete_keeps_calibration(calibrated_rig):
    rig = calibrated_rig
    g = create_gesture(rig, "OBOBO", "HELLO")
    rig.c.set_gesture_enabled(g.id, False)
    rig.hold("OBOBO", 12)
    assert rig.c.detection.display == "UNKNOWN"
    rig.c.set_gesture_enabled(g.id, True)
    rig.hold("OBOBO", 12)
    assert rig.c.detection.display == "HELLO"
    cal_before = rig.c.calibration.calibration
    settings_before = rig.c.settings
    assert rig.c.delete_gesture(g.id).ok
    assert rig.c.calibration.calibration == cal_before and rig.ws.calibration_path.exists()
    assert rig.c.settings is settings_before
    assert rig.c.live_sensor.live
    rig.hold("OBOBO", 12)
    assert rig.c.detection.display == "UNKNOWN"


def test_speech_disabled_does_not_speak(calibrated_rig):
    rig = calibrated_rig
    rig.c.speech_state.enabled = False
    create_gesture(rig, "OBOBO", "HELLO")
    rig.hold("OBOBO", 20)
    assert rig.c.detection.accepted and rig.engine.spoken == []


def test_esp32_gesture_line_trigger_mode(tmp_path):
    rig = Rig(tmp_path, Settings(auto_connect=False, speech_trigger="esp32"))
    rig.c.set_calibration(make_calibration())
    rig.connect()
    create_gesture(rig, "OBOBO", "HELLO", "Hello")
    rig.hold("OOOOO", 30)  # neutral hand: arms gesture speech
    rig.hold("OBOBO", 20)                               # studio detection does not speak in this mode
    assert rig.engine.spoken == []
    rig.send("# GESTURE: HELLO\n")
    assert rig.engine.spoken == [("Hello", "Neutral")]
    assert rig.c.detection.esp32_gesture == "HELLO"
    rig.c.close()


# ------------------------------------------------------------ hardware status
def test_hardware_status_from_firmware_lines(rig):
    rig.connect()
    assert "STATUS" in rig.written()
    for line in ("# FIRMWARE: Sign2Speech V2 abc123", "# ESP32: OK chip=ESP32 rev=3 cpu=240MHz",
                 "# PINS: FLEX=32,35,34,39,36 OLED_SDA=18 OLED_SCL=19 MPU_SDA=22 MPU_SCL=21",
                 "# MPU6050: OK addr=0x68 WHO_AM_I=0x70 compatible", "# OLED: ERROR not detected",
                 "# I2C OLED: SDA=18 SCL=19 found none", "# I2C MPU: SDA=22 SCL=21 found 0x68"):
        rig.send(line + "\n")
    hw = rig.c.hardware
    assert hw.pins_verified is True and hw.esp32.ok
    assert hw.mpu.ok and hw.mpu_who_am_i == "0x70"
    assert hw.oled.text("OK", "OLED NOT DETECTED") == "OLED NOT DETECTED"
    assert hw.i2c_oled_found == () and hw.i2c_mpu_found == (0x68,)


def test_wrong_pins_reported_by_firmware_are_flagged(rig):
    rig.connect()
    rig.send("# PINS: FLEX=32,35,34,39,36 OLED_SDA=22 OLED_SCL=21 MPU_SDA=22 MPU_SCL=21\n")
    assert rig.c.hardware.pins_verified is False


def test_mpu_error_state(rig):
    rig.connect()
    rig.send("# MPU6050: ERROR no answer at 0x68\n")
    assert rig.c.hardware.mpu.text("OK", "MPU6050 ERROR") == "MPU6050 ERROR"


# ------------------------------------------------------------ connection / simulation
def test_startup_tries_com9_first(tmp_path):
    rig = Rig(tmp_path, Settings(auto_connect=True), ports=("COM3", "COM9"))
    assert rig.c.startup().ok
    assert rig.open_calls == ["COM9"] and rig.c.acquisition.port == "COM9"
    rig.c.close()


def test_startup_falls_back_to_other_esp32_port(tmp_path):
    rig = Rig(tmp_path, Settings(auto_connect=True), ports=("COM4",))
    assert rig.c.startup().ok
    assert rig.open_calls == ["COM9", "COM4"]
    rig.c.close()


def test_startup_without_glove_never_simulates(tmp_path):
    rig = Rig(tmp_path, Settings(auto_connect=True), ports=())
    res = rig.c.startup()
    assert not res.ok and rig.c.acquisition.source is None
    for _ in range(300):
        rig.clock.advance(0.02)
        rig.c.tick()
    assert rig.c.acquisition.source is None and rig.c.live_sensor.raw is None
    assert rig.c.live_sensor.link == "WAITING FOR GLOVE"
    rig.c.close()


def test_auto_reconnect_after_plugging_in(tmp_path):
    rig = Rig(tmp_path, Settings(auto_connect=True), ports=())
    rig.c.startup()
    rig.present.add("COM9")
    rig.clock.advance(3.1)
    rig.c.tick()
    assert rig.c.acquisition.source is Source.REAL
    rig.c.close()


def test_simulation_only_when_requested_and_never_mixed(calibrated_rig):
    rig = calibrated_rig
    assert rig.c.acquisition.source is Source.REAL
    res = rig.c.start_simulation()
    assert not res.ok and rig.c.acquisition.source is Source.REAL     # real data has priority
    rig.c.disconnect()
    assert rig.c.start_simulation().ok
    assert rig.c.acquisition.source is Source.SIMULATION
    assert not rig.c.connect("COM9").ok                               # no silent switch either way
    rig.c.set_sim_pattern("OBOBO")
    for _ in range(10):
        rig.clock.advance(0.02)
        rig.c.tick()
    assert rig.c.live_sensor.source is Source.SIMULATION
    assert rig.c.live_sensor.link == "SIMULATION"
    rig.c.stop_simulation()
    assert rig.c.live_sensor.raw is None and rig.c.acquisition.source is None


def test_user_disconnect_pauses_auto_reconnect(tmp_path):
    rig = Rig(tmp_path, Settings(auto_connect=True))
    rig.c.startup()
    rig.c.disconnect()
    rig.clock.advance(10)
    rig.c.tick()
    assert rig.c.acquisition.source is None
    rig.c.close()


def test_host_keepalive_sent_for_oled(calibrated_rig):
    rig = calibrated_rig
    rig.hold("OOOOO", 1)
    assert "HOST" in rig.written()


def test_sample_recording(calibrated_rig, tmp_path):
    rig = calibrated_rig
    rig.c.new_gesture()
    assert not rig.c.start_recording().ok              # needs a name
    rig.c.editor.name = "HELLO"
    rig.hold("OBOBO", 1)
    assert rig.c.start_recording().ok
    rig.hold("OBOBO", 25)
    res = rig.c.stop_recording()
    assert res.ok and rig.c.samples.count("HELLO") == 25
    assert (rig.ws.samples_dir / "HELLO.csv").exists()


def test_frame_line_with_real_esp32_format(calibrated_rig):
    rig = calibrated_rig
    rig.send(data_line((700, 2600, 700, 2600, 700), accel=(0.5, -0.25, 0.8), gyro=(10, -20, 30)))
    assert rig.c.live_sensor.accel == pytest.approx((0.5, -0.25, 0.8))
    assert rig.c.live_sensor.gyro == pytest.approx((10, -20, 30))

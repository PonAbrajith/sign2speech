"""Serial protocol, decoder, status lines and the single acquisition manager."""

import pytest

from sign2speech_v2.acquisition.decoder import DecodeError, StreamDecoder
from sign2speech_v2.acquisition.manager import AcquisitionManager, LinkEvent, LinkStatus
from sign2speech_v2.acquisition.transport import FakeTransport, TransportError
from sign2speech_v2.core.frame import ProtocolError, SensorFrame, Source, parse_data_line
from sign2speech_v2.core.status_lines import LineKind, parse_status_line

from conftest import data_line

LINE = "812,640,700,655,590,0.012,-0.020,0.998,1.25,-0.50,0.75"


# ---------------------------------------------------------------- data lines
def test_parse_data_line_keeps_finger_order_and_values():
    f = parse_data_line(LINE, 5.0)
    assert f.flex == (812, 640, 700, 655, 590)       # Thumb, Index, Middle, Ring, Little
    assert f.accel == pytest.approx((0.012, -0.020, 0.998))
    assert f.gyro == pytest.approx((1.25, -0.5, 0.75))
    assert f.timestamp == 5.0 and f.source is Source.REAL


def test_roundtrip_to_line():
    f = parse_data_line(LINE, 0)
    assert parse_data_line(f.to_line(), 0).flex == f.flex


@pytest.mark.parametrize("bad", ["", "1,2,3", LINE + ",5", "a,b,c,d,e,1,2,3,4,5,6",
                                 "4096,0,0,0,0,0,0,0,0,0,0", "-1,0,0,0,0,0,0,0,0,0,0",
                                 "1.5,0,0,0,0,0,0,0,0,0,0", "nan,0,0,0,0,0,0,0,0,0,0"])
def test_bad_lines_are_rejected(bad):
    with pytest.raises(ProtocolError):
        parse_data_line(bad, 0)


# ------------------------------------------------------------------- decoder
def test_decoder_handles_split_lines_and_crlf():
    d = StreamDecoder()
    assert d.feed(b"812,640,700,", 0) == []
    out = d.feed(b"655,590,0.01,0.02,1.0,0,0,0\r\n# READY\r\n", 0.1)
    assert isinstance(out[0], SensorFrame) and out[0].flex == (812, 640, 700, 655, 590)
    assert out[1].kind is LineKind.READY


def test_decoder_reports_garbage_without_crashing():
    d = StreamDecoder()
    out = d.feed(b"\xff\xfe\x00garbage\n5,5\n", 0)
    assert all(isinstance(o, DecodeError) for o in out) and len(out) == 2
    assert d.stats.errors == 2
    assert d.feed(b"x" * 600, 0)[0].message.startswith("more than")


# -------------------------------------------------------------- status lines
@pytest.mark.parametrize("line,kind,check", [
    ("# ESP32: OK chip=ESP32-D0WD-V3 rev=3 cpu=240MHz", LineKind.DEVICE, lambda s: s.device == "ESP32" and s.ok),
    ("# FLEX: ERROR ALL ADC = 0 - CHECK FLEX WIRING raw=0,0,0,0,0", LineKind.DEVICE,
     lambda s: s.device == "FLEX" and not s.ok),
    ("# MPU6050: OK addr=0x68 WHO_AM_I=0x70 compatible", LineKind.DEVICE,
     lambda s: s.ok and s.values["WHO_AM_I"] == "0x70"),
    ("# MPU6050: ERROR no answer at 0x68", LineKind.DEVICE, lambda s: s.device == "MPU6050" and not s.ok),
    ("# OLED: OK addr=0x3D", LineKind.DEVICE, lambda s: s.values["addr"] == "0x3D"),
    ("# OLED: ERROR not detected (scanned 0x3C 0x3D on SDA=18 SCL=19)", LineKind.DEVICE, lambda s: not s.ok),
    ("# GESTURE: HELLO", LineKind.GESTURE, lambda s: s.gesture == "HELLO"),
    ("# GESTURES: 5", LineKind.GESTURE_COUNT, lambda s: s.count == 5),
    ("# RECOGNITION: FINGER_PATTERN", LineKind.RECOGNITION, lambda s: s.ok),
    ("# RECOGNITION: DISABLED not calibrated", LineKind.RECOGNITION, lambda s: s.ok is False),
    ("# PINS: FLEX=32,35,34,39,36 OLED_SDA=18 OLED_SCL=19 MPU_SDA=22 MPU_SCL=21", LineKind.PINS,
     lambda s: s.values["FLEX"] == "32,35,34,39,36" and s.values["MPU_SCL"] == "21"),
    ("# I2C OLED: SDA=18 SCL=19 found 0x3C", LineKind.I2C_SCAN, lambda s: s.bus == "OLED" and s.found == (0x3C,)),
    ("# I2C MPU: SDA=22 SCL=21 found none", LineKind.I2C_SCAN, lambda s: s.bus == "MPU" and s.found == ()),
    ("# ADC Thumb GPIO32 samples=32 min=0 max=0 avg=0 step=0 mv=0 driver=ATTACHED", LineKind.ADC_CHANNEL,
     lambda s: s.adc.finger == "Thumb" and s.adc.gpio == 32 and s.adc.driver_attached),
    ("# STATUS MPU6050=OK OLED=ERROR HOST=1 UPTIME=12", LineKind.HEARTBEAT, lambda s: s.values["OLED"] == "ERROR"),
    ("# OLED SHOW: HELLO", LineKind.INFO, lambda s: True),
    ("# PONG", LineKind.PONG, lambda s: True),
])
def test_status_lines(line, kind, check):
    s = parse_status_line(line)
    assert s.kind is kind
    assert check(s)


# ------------------------------------------------------------------- manager
def make_manager():
    t = FakeTransport("COM9")
    m = AcquisitionManager(lambda port, baud: t, stale_timeout_s=1.0)
    events = []
    m.subscribe(events.append)
    return m, t, events


def test_manager_publishes_frames_to_subscribers_and_tracks_link():
    m, t, events = make_manager()
    assert m.link_status(0) is LinkStatus.DISCONNECTED
    ok, _ = m.connect("COM9", 115200, 0)
    assert ok and m.source is Source.REAL
    m.poll(0.1)
    assert m.link_status(0.1) is LinkStatus.NO_DATA
    t.feed(data_line((1, 2, 3, 4, 5)))
    m.poll(0.2)
    assert m.latest_frame.flex == (1, 2, 3, 4, 5)
    assert any(isinstance(e, SensorFrame) for e in events)
    assert m.link_status(0.5) is LinkStatus.LIVE
    assert m.link_status(1.3) is LinkStatus.STALE        # stream stopped


def test_second_subscriber_gets_the_same_frames():
    m, t, events = make_manager()
    other = []
    m.subscribe(other.append)
    m.connect("COM9", 115200, 0)
    t.feed(data_line((9, 9, 9, 9, 9)))
    m.poll(0.1)
    f1 = [e for e in events if isinstance(e, SensorFrame)]
    f2 = [e for e in other if isinstance(e, SensorFrame)]
    assert f1 == f2 and len(f1) == 1


def test_unplugged_cable_disconnects_cleanly():
    m, t, events = make_manager()
    m.connect("COM9", 115200, 0)
    t.fail_reads = True
    m.poll(0.1)
    assert m.source is None and t.closed
    assert m.link_status(0.1) is LinkStatus.DISCONNECTED
    assert any(isinstance(e, LinkEvent) and e.level == "error" for e in events)


def test_failed_open_reports_error():
    def opener(port, baud):
        raise TransportError("COM9 was not found")
    m = AcquisitionManager(opener)
    ok, msg = m.connect("COM9", 115200, 0)
    assert not ok and "not found" in msg and m.source is None


def test_commands_are_written_with_newline():
    m, t, _ = make_manager()
    assert not m.send_command("PING")          # not connected
    m.connect("COM9", 115200, 0)
    assert m.send_command("SHOW HELLO")
    assert t.written == [b"SHOW HELLO\n"]

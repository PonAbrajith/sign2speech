"""RAW ADC test classification, flex health, MPU motion."""

from sign2speech_v2.core.status_lines import parse_status_line
from sign2speech_v2.diagnostics.health import (AdcClass, RollingWindow, all_zero, classify, flex_health,
                                               motion_report, rows_from_reports, rows_from_values)


def test_classification():
    assert classify(0, 0, 0, 32) is AdcClass.ZERO
    assert classify(800, 820, 3, 32) is AdcClass.STABLE_NONZERO
    assert classify(700, 2600, 20, 32) is AdcClass.CHANGING
    assert classify(0, 4095, 900, 32) is AdcClass.NOISY
    assert classify(0, 0, 0, 0) is AdcClass.NO_DATA


def test_all_zero_detected_and_values_unchanged():
    history = [[0] * 50 for _ in range(5)]
    rows = rows_from_values(history)
    assert all_zero(rows)
    assert all(r.minimum == 0 and r.maximum == 0 and r.average == 0 for r in rows)   # never adjusted
    assert all("CHECK FLEX WIRING" in line for line in flex_health(rows, None))


def test_one_zero_finger_is_not_all_zero():
    history = [[0] * 50] + [[800] * 50 for _ in range(4)]
    rows = rows_from_values(history)
    assert not all_zero(rows) and rows[0].status is AdcClass.ZERO and rows[0].finger == "Thumb"
    assert rows[0].gpio == 32


def test_firmware_adc_report_rows():
    lines = [f"# ADC {n} GPIO{g} samples=32 min=800 max=812 avg=806 step=3 mv=690 driver=ATTACHED"
             for n, g in (("Thumb", 32), ("Index", 35), ("Middle", 34), ("Ring", 39))]
    lines.append("# ADC Little GPIO36 samples=32 min=0 max=0 avg=0 step=0 mv=0 driver=NOT_ATTACHED")
    rows = rows_from_reports([parse_status_line(x).adc for x in lines])
    assert [r.gpio for r in rows] == [32, 35, 34, 39, 36]
    assert rows[0].status is AdcClass.STABLE_NONZERO and rows[0].millivolts == 690
    assert rows[4].status is AdcClass.ZERO and rows[4].driver == "NOT ATTACHED"


def test_mpu_motion_detection():
    acc, gyr = RollingWindow(3), RollingWindow(3)
    for _ in range(20):
        acc.add((0.0, 0.0, 1.0))
        gyr.add((0.1, 0.1, 0.1))
    still = motion_report(acc, gyr)
    assert not still.accel_changing and not still.gyro_changing
    acc.add((0.4, 0.0, 0.9))
    gyr.add((40.0, 0.0, 0.0))
    moved = motion_report(acc, gyr)
    assert moved.accel_changing and moved.gyro_changing


def test_mpu_all_zero_is_an_error():
    acc, gyr = RollingWindow(3), RollingWindow(3)
    for _ in range(5):
        acc.add((0, 0, 0))
        gyr.add((0, 0, 0))
    assert "ERROR" in motion_report(acc, gyr).text

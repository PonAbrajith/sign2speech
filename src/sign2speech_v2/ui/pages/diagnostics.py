from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem)

from sign2speech_v2.config.pins import FINGER_NAMES, MPU_SCL, MPU_SDA, OLED_ADDRESSES, OLED_SCL, OLED_SDA
from sign2speech_v2.core.frame import Source
from sign2speech_v2.diagnostics.health import (ALL_ZERO_TEXT, all_zero, flex_health, motion_report,
                                               rows_from_reports, rows_from_values)
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.pages.base import Page
from sign2speech_v2.ui.widgets import Card, label, set_color, set_text

ADC_COLS = ("Finger", "GPIO", "Min", "Max", "Average", "Avg step", "mV", "Driver", "Class")


class DiagnosticsPage(Page):
    title = "Hardware Diagnostics"

    def __init__(self, *a) -> None:
        super().__init__(*a)
        top = QHBoxLayout()
        for text, cmd in (("Request STATUS", "STATUS"), ("RUN RAW ADC TEST", "ADC"), ("OLED / MPU I2C SCAN", "I2C"),
                          ("PING", "PING")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, cmd=cmd: self.act(self.c.send_command(cmd)))
            top.addWidget(b)
        reset = QPushButton("Reset board (EN)")
        reset.setToolTip("Restart the ESP32 like its EN/RST button to see its boot output and identify the "
                         "firmware. Normal boot only - this never flashes anything.")
        reset.clicked.connect(lambda: self.act(self.c.reset_board()))
        top.addWidget(reset)
        top.addStretch(1)
        self.layout_.addLayout(top)

        real = Card("REAL HARDWARE TEST")
        self.real_text = label("", size=10)
        self.real_text.setStyleSheet("font-family: Consolas;")
        self.real_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        real.body.addWidget(self.real_text)
        self.real_fingers = label("", size=12, bold=True)
        self.real_fingers.setStyleSheet("font-family: Consolas; font-size: 12pt; font-weight: 600;")
        real.body.addWidget(self.real_fingers)
        self.trace_btn = QPushButton("Start hardware trace (CSV)")
        self.trace_btn.setToolTip("Records every received line with its parsed values, finger states, live "
                                  "pattern and detection to workspace/traces/. Data is not modified.")
        self.trace_btn.clicked.connect(
            lambda: self.act(self.c.stop_trace() if self.c.tracing else self.c.start_trace()))
        real.body.addWidget(self.trace_btn)
        self.layout_.addWidget(real)

        self.esp = Card("ESP32 STATUS")
        self.esp_text = label("", size=10)
        self.esp.body.addWidget(self.esp_text)
        self.layout_.addWidget(self.esp)

        ser = Card("SERIAL STATUS")
        self.ser_text = label("", size=10)
        ser.body.addWidget(self.ser_text)
        self.layout_.addWidget(ser)

        adc = Card("RAW ADC TEST")
        self.adc_source = label("", "muted")
        adc.body.addWidget(self.adc_source)
        self.all_zero = label("", color=theme.ERROR, size=14, bold=True)
        adc.body.addWidget(self.all_zero)
        self.adc = QTableWidget(len(FINGER_NAMES), len(ADC_COLS))
        self.adc.setHorizontalHeaderLabels(ADC_COLS)
        self.adc.verticalHeader().setVisible(False)
        self.adc.setMinimumHeight(200)
        self.adc.horizontalHeader().setStretchLastSection(True)
        adc.body.addWidget(self.adc)
        self.layout_.addWidget(adc)

        fh = Card("FLEX SENSOR HEALTH")
        self.flex_text = label("", size=10)
        fh.body.addWidget(self.flex_text)
        self.layout_.addWidget(fh)

        mpu = Card("MPU6050 TEST  (move the board: values must change)")
        self.mpu_text = label("", size=10)
        mpu.body.addWidget(self.mpu_text)
        self.layout_.addWidget(mpu)

        oled = Card(f"OLED I2C SCAN  (OLED bus SDA D{OLED_SDA} / SCL D{OLED_SCL}; MPU bus SDA D{MPU_SDA} / SCL D{MPU_SCL})")
        self.oled_text = label("", size=10)
        oled.body.addWidget(self.oled_text)
        self.layout_.addWidget(oled)

        mon = Card("SERIAL MONITOR  (the one shared connection)")
        self.monitor = QPlainTextEdit()
        self.monitor.setReadOnly(True)
        self.monitor.setMaximumBlockCount(500)
        self.monitor.setMinimumHeight(180)
        mon.body.addWidget(self.monitor)
        row = QHBoxLayout()
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("Command: STATUS, I2C, ADC, PING")
        self.cmd.returnPressed.connect(self._send)
        send = QPushButton("Send")
        send.clicked.connect(self._send)
        self.show_data = QPushButton("Show data lines")
        self.show_data.setCheckable(True)
        row.addWidget(self.cmd, 1)
        row.addWidget(send)
        row.addWidget(self.show_data)
        mon.body.addLayout(row)
        self.layout_.addWidget(mon)
        self._mon_key = None

    def _send(self) -> None:
        if self.cmd.text().strip():
            self.act(self.c.send_command(self.cmd.text().strip()))
            self.cmd.clear()

    def refresh(self) -> None:
        c = self.c
        hw, ls = c.hardware, c.live_sensor
        self._refresh_real(hw, ls)
        pins = ("verified ✓" if hw.pins_verified else "MISMATCH ✖" if hw.pins_verified is False
                else "not reported yet")
        set_text(self.esp_text, "\n".join((
            f"Firmware: {hw.firmware or 'no report (press Request STATUS; V2 firmware required)'}",
            f"ESP32: {hw.esp32.text('OK', 'ERROR', 'no report')} {hw.esp32.detail}",
            f"Pins reported by the ESP32: {pins} {hw.reported_pins or ''}",
            f"Firmware gestures: {hw.firmware_gestures if hw.firmware_gestures is not None else '-'}   "
            f"Recognition: {hw.firmware_recognition or '-'}")))
        set_text(self.ser_text, f"Source: {hw.source.value if hw.source else 'NONE'}   Port: {hw.port or '-'}   "
                                f"Link: {hw.serial}   Frames: {hw.frames}   Bad lines: {hw.decode_errors}")

        if hw.adc_reports and len(hw.adc_reports) == len(FINGER_NAMES) and not hw.adc_testing:
            rows = rows_from_reports(hw.adc_reports)
            set_text(self.adc_source, "Source: firmware RAW ADC TEST (32 direct analogRead samples per pin)")
        else:
            rows = rows_from_values([[int(v) for v in ch] for ch in c.flex_window.values()])
            set_text(self.adc_source, "Source: live stream (last 100 frames). Press RUN RAW ADC TEST for the "
                                      "firmware's direct test with mV and driver status.")
        set_text(self.all_zero, ALL_ZERO_TEXT if all_zero(rows) else "")
        for r, row in enumerate(rows):
            vals = (row.finger, f"GPIO{row.gpio}", _n(row.minimum), _n(row.maximum), _n(row.average),
                    _n(row.mean_step, 1), _n(row.millivolts) + (" (est.)" if row.source == "live stream" and
                                                                 row.millivolts is not None else ""),
                    row.driver, row.status.value)
            for col, v in enumerate(vals):
                item = self.adc.item(r, col)
                if item is None:
                    self.adc.setItem(r, col, QTableWidgetItem(v))
                elif item.text() != v:
                    item.setText(v)
        set_text(self.flex_text, "\n".join(flex_health(rows, c.calibration.range)))

        m = motion_report(c.accel_window, c.gyro_window)
        mpu_status = hw.mpu.text("OK", "MPU6050 ERROR", "no report")
        who = f"  WHO_AM_I={hw.mpu_who_am_i}" if hw.mpu_who_am_i else ""
        vals = ""
        if ls.live and ls.accel and ls.gyro:
            vals = ("\nAccel X/Y/Z: " + "  ".join(f"{v:+.3f}" for v in ls.accel) + " g"
                    "\nGyro  X/Y/Z: " + "  ".join(f"{v:+.1f}" for v in ls.gyro) + " °/s")
        set_text(self.mpu_text, f"MPU6050: {mpu_status}{who} {hw.mpu.detail}\nMotion: {m.text}{vals}")
        set_color(self.mpu_text, theme.ERROR if (hw.mpu.known and not hw.mpu.ok) or m.all_zero else theme.TEXT, 10)

        oled_lines = [f"OLED: {hw.oled.text('OK', 'OLED NOT DETECTED', 'no report')} {hw.oled.detail}"]
        if hw.i2c_oled_found is not None:
            found = " ".join(f"0x{a:02X}" for a in hw.i2c_oled_found) or "none"
            hits = [f"0x{a:02X}" for a in OLED_ADDRESSES if a in hw.i2c_oled_found]
            oled_lines.append(f"OLED bus scan: found {found} → " +
                              (f"OLED at {', '.join(hits)}" if hits else "OLED NOT DETECTED (no 0x3C / 0x3D)"))
        else:
            oled_lines.append("OLED bus scan: not run (press OLED / MPU I2C SCAN)")
        if hw.i2c_mpu_found is not None:
            found = " ".join(f"0x{a:02X}" for a in hw.i2c_mpu_found) or "none"
            oled_lines.append(f"MPU bus scan: found {found} → " +
                              ("MPU6050 at 0x68" if 0x68 in hw.i2c_mpu_found else "MPU6050 ERROR (no 0x68)"))
        set_text(self.oled_text, "\n".join(oled_lines))

        # everything except valid sensor frames (those only with "Show data lines"): unknown text such
        # as another sketch's output must be visible
        lines = [text for data, text in c.serial_lines if self.show_data.isChecked() or not data]
        key = (len(c.serial_lines), lines[-1] if lines else None, self.show_data.isChecked())
        if key != self._mon_key:
            self._mon_key = key
            self.monitor.setPlainText("\n".join(lines[-200:]))
            self.monitor.verticalScrollBar().setValue(self.monitor.verticalScrollBar().maximum())


    def _refresh_real(self, hw, ls) -> None:
        """Everything here comes from the one acquisition stream - nothing is estimated or remembered."""
        real = self.c.acquisition.source is Source.REAL     # the connection itself, not a copy
        receiving = real and hw.last_byte_age is not None and hw.last_byte_age <= 1.0
        frames_ok = real and ls.live
        all0 = frames_ok and ls.raw is not None and all(v <= 5 for v in ls.raw)
        flex = "CHECK WIRING (all ADC = 0)" if all0 else "DATA RECEIVING" if frames_ok else "NO DATA"
        age = "never" if hw.last_frame_age is None else f"{hw.last_frame_age:.2f} sec"
        lines = [
            f"Port:              {hw.port or '-'}",
            f"ESP32:             {'CONNECTED' if real else 'DISCONNECTED'}",
            f"Serial:            {'RECEIVING' if receiving else 'NO DATA'}",
            f"Bytes received:    {hw.bytes_received}",
            f"Valid frames:      {hw.frames}",
            f"Invalid frames:    {hw.decode_errors}",
            f"Last frame age:    {age}",
            f"Firmware:          {hw.firmware_kind}" + (f" | {hw.firmware_detail}" if hw.firmware_detail else ""),
            f"Flex:              {flex}",
            # device status exactly as the firmware reported it (no report -> not assumed OK)
            f"MPU6050:           {hw.mpu.text('OK', 'MPU6050 ERROR', 'NO REPORT')}"
            + (f" WHO_AM_I={hw.mpu_who_am_i}" if hw.mpu_who_am_i else ""),
            "OLED:              "
            + hw.oled.text(f"OK 0x{hw.oled_address:02X}" if hw.oled_address else "OK", "OLED NOT DETECTED",
                           "NO REPORT"),
        ]
        if hw.last_text_line:
            lines.append(f'Last non-data line: "{hw.last_text_line}"')
        info = self.c.runtime_info()
        lines += ["", *(f"{k + ':':<26}{v}" for k, v in info.items())]
        set_text(self.real_text, "\n".join(lines))
        ok = frames_ok and not all0
        set_color(self.real_text, theme.LIVE if ok else theme.WARN if real else theme.MUTED, 10)
        if frames_ok:   # the last VALID packet, only while it is fresh (stale -> NO DATA)
            rows = "\n".join(f"{n + ':':<8}{v}" for n, v in zip(FINGER_NAMES, ls.raw))
            set_text(self.real_fingers, f"Last RAW FLEX (packet {ls.frame_count}):\n{rows}")
        else:
            set_text(self.real_fingers, "Last RAW FLEX:\nNO DATA")
        set_text(self.trace_btn, "STOP TRACE" if self.c.tracing else "Start hardware trace (CSV)")


def _n(v, digits: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)

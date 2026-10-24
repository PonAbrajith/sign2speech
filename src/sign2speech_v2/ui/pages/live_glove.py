from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QProgressBar, QSlider, QWidget

from sign2speech_v2.config.pins import FINGER_NAMES
from sign2speech_v2.core.frame import Source
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.pages.base import Page
from sign2speech_v2.ui.widgets import Card, FingerMeters, label, pattern_row, set_color, set_text


class LiveGlovePage(Page):
    title = "Live Glove"

    def __init__(self, *a) -> None:
        super().__init__(*a)
        self.banner = label("", size=12, bold=True)
        self.layout_.addWidget(self.banner)

        fingers = Card("LIVE FINGER DATA  (current stream: raw ADC, filtered, bending %, OPEN/BENT)")
        self.meters = FingerMeters()
        fingers.body.addWidget(self.meters)
        self.layout_.addWidget(fingers)

        pat = Card("PATTERN & DETECTION")
        w1, self.live_pattern = pattern_row("LIVE PATTERN:", theme.LIVE)
        w2, self.live_detection = pattern_row("LIVE DETECTION:", theme.DETECT)
        w3, self.saved_pattern = pattern_row("SAVED PATTERN:", theme.STORED)
        w4, self.sentence = pattern_row("SENTENCE:", theme.STORED)
        w0, self.fingers = pattern_row("FINGERS:", theme.LIVE)
        self.fingers.setStyleSheet(f"color: {theme.LIVE}; font-size: 11pt; font-weight: 600;")
        w5, self.speech_arm = pattern_row("SPEECH:", theme.MUTED)
        self.speech_arm.setWordWrap(True)
        self.sentence.setStyleSheet(f"color: {theme.STORED}; font-size: 12pt; font-weight: 600;")
        for w in (w0, w1, w2, w3, w4):
            pat.body.addWidget(w)
        self.meaning = label("", "muted")             # the SENTENCE's emotion metadata, not a match result
        pat.body.addWidget(self.meaning)
        pat.body.addWidget(w5)
        row = QHBoxLayout()
        row.addWidget(label("STABILITY", "muted"))
        self.stability = QProgressBar()
        self.stability.setFixedHeight(16)
        row.addWidget(self.stability, 1)
        self.accepted = label("", bold=True)
        row.addWidget(self.accepted)
        pat.body.addLayout(row)
        self.reason = label("", "muted")
        self.reason.setWordWrap(True)
        pat.body.addWidget(self.reason)
        self.esp32 = label("", "muted")
        pat.body.addWidget(self.esp32)
        self.layout_.addWidget(pat)

        mpu = Card("MPU6050  (accelerometer g, gyroscope °/s)")
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        self.imu = []
        for r, name in enumerate(("Accel", "Gyro")):
            grid.addWidget(label(name, bold=True), r, 0)
            for col, axis in enumerate("XYZ"):
                v = label("—", size=12)
                grid.addWidget(label(axis, "muted"), r, 1 + col * 2)
                grid.addWidget(v, r, 2 + col * 2)
                self.imu.append(v)
        mpu.body.addWidget(grid_w)
        self.motion = label("", "muted")
        mpu.body.addWidget(self.motion)
        self.layout_.addWidget(mpu)

        self.sim = Card("SIMULATION CONTROLS  (only while SIMULATION is running)")
        self.sliders = []
        for i, name in enumerate(FINGER_NAMES):
            row = QHBoxLayout()
            row.addWidget(label(name))
            s = QSlider(Qt.Horizontal)
            s.setRange(0, 100)
            s.valueChanged.connect(lambda v, i=i: self.c.set_sim_bend(i, v / 100))
            row.addWidget(s, 1)
            self.sim.body.addLayout(row)
            self.sliders.append(s)
        self.layout_.addWidget(self.sim)
        self.layout_.addStretch(1)

    def refresh(self) -> None:
        from sign2speech_v2.diagnostics.health import motion_report

        c = self.c
        ls, d = c.live_sensor, c.detection
        if ls.source is Source.SIMULATION:
            set_text(self.banner, "SIMULATION — values are simulated, not from the glove")
            set_color(self.banner, theme.SIM, 12, True)
        elif ls.live:
            set_text(self.banner, "REAL ESP32 — CONNECTED — DATA RECEIVING")
            set_color(self.banner, theme.LIVE, 12, True)
        else:
            hw = c.hardware
            text = ls.link
            if ls.source is Source.REAL and hw.firmware_kind not in ("EXPECTED V2", "V2 DIFFERENT BUILD"):
                # connected but no frames: say which firmware the board runs
                text = f"REAL ESP32 — {ls.link}  |  FIRMWARE: {hw.firmware_kind}"
                if hw.firmware_detail:
                    text += f"\n{hw.firmware_detail}"
            set_text(self.banner, text)
            set_color(self.banner, theme.WARN, 12, True)
        self.sim.setVisible(ls.source is Source.SIMULATION)

        ignored = c.ignored_fingers()
        self.meters.update_values(ls.raw if ls.live else None, ls.filtered, ls.bending, ls.finger_states, ignored)
        set_text(self.fingers, "   ".join(
            f"{FINGER_NAMES[i]} {'IGNORED' if i in ignored else s.word}" for i, s in enumerate(ls.finger_states))
            if ls.live and ls.finger_states else "—")
        sp = c.speech_state
        if sp.armed:
            set_text(self.speech_arm, "ARMED — an accepted gesture is spoken once")
            set_color(self.speech_arm, theme.LIVE, 12, True)
        else:
            set_text(self.speech_arm, f"NOT ARMED — gestures are shown but not spoken until the hand matches no "
                                      f"gesture for a moment ({sp.arm_count}/{sp.arm_needed})")
            set_color(self.speech_arm, theme.WARN, 11, True)
        set_text(self.live_pattern, str(d.live_pattern) if d.live_pattern and ls.live else "—")
        # NO SENSOR DATA is not the same as an UNKNOWN gesture
        set_text(self.live_detection, d.display if ls.live else "WAITING FOR DATA")
        # the stored pattern of the gesture the CURRENT hand matches (may contain X)
        set_text(self.saved_pattern, str(d.saved_pattern) if d.saved_pattern else "—")
        set_text(self.sentence, d.matched_sentence if (ls.live and d.matched_sentence) else "—")
        set_text(self.meaning, f"Sentence meaning (metadata of the sentence): {d.matched_emotion}"
                 if (ls.live and d.matched_emotion) else "")
        self.stability.setRange(0, max(1, d.hold_samples))
        self.stability.setValue(min(d.stability_count, d.hold_samples))
        set_text(self.accepted, f"ACCEPTED ✓ {d.accepted_gesture}" if d.accepted else "")
        set_color(self.accepted, theme.DETECT, None, True)
        set_text(self.reason, d.reason if d.status in ("UNKNOWN", "AMBIGUOUS") else "")
        set_text(self.esp32, f"ESP32 on-device last: {d.esp32_gesture}" if d.esp32_gesture else "")

        vals = (ls.accel or (None,) * 3) + (ls.gyro or (None,) * 3) if ls.live else (None,) * 6
        for i, v in enumerate(vals):
            set_text(self.imu[i], "—" if v is None else (f"{v:+.3f}" if i < 3 else f"{v:+.1f}"))
        set_text(self.motion, motion_report(c.accel_window, c.gyro_window).text if ls.live else "")

"""StudioController - owns the one acquisition stream and all seven states.

Live pipeline (per REAL or SIMULATION frame, never both):

    frame.raw -> FlexFilter -> filtered
              -> Calibration -> bending %, OPEN/BENT (hysteresis) -> LivePattern (O/B)
              -> Recognizer(enabled stored gestures) -> MATCH / UNKNOWN / AMBIGUOUS
              -> StabilityGate(hold N frames) -> ACCEPTED once
              -> SpeechArming: automatic speech only after a neutral (no match) period, so a hand
                 already in a gesture when recognition (re)starts is shown but never spoken
              -> SpeechService (laptop speaker)  +  "SHOW <NAME>" to the ESP32 OLED

The Gesture Builder editor and the library selection are NOT inputs to this
pipeline; they are only written by the builder/library actions below.
Qt-free: the UI calls tick() from a timer; tests call it with a fake clock.
"""

from __future__ import annotations

import csv
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sign2speech_v2.acquisition.decoder import DecodeError
from sign2speech_v2.acquisition.manager import AcquisitionManager, LinkEvent, LinkStatus, RawLine
from sign2speech_v2.acquisition.transport import Opener, PortInfo, list_ports, open_serial
from sign2speech_v2.calibration.capture import (CLOSED_SECONDS, OPEN_SECONDS, CaptureSession, analyze_closed,
                                                analyze_open)
from sign2speech_v2.calibration.model import Calibration, CalibrationStore, FingerStateClassifier
from sign2speech_v2.config.pins import FINGER_NAMES, FLEX_PINS, MPU_SCL, MPU_SDA, OLED_SCL, OLED_SDA
from sign2speech_v2.config.demo import (SPEECH_DEMO_NAME, SPEECH_DEMO_SENTENCE, ensure_all_demo_data,
                                        pattern_conflicts, suggested_style)
from sign2speech_v2.config.settings import Settings, Workspace
from sign2speech_v2.core.frame import SensorFrame, Source
from sign2speech_v2.core.patterns import GesturePattern, PatternToken
from sign2speech_v2.core.status_lines import LineKind, StatusLine
from sign2speech_v2.diagnostics.health import RollingWindow
from sign2speech_v2.gestures.repository import Gesture, GestureError, GestureRepository
from sign2speech_v2.gestures.samples import SampleStore
from sign2speech_v2.recognition.recognizer import (FingerPatternRecognizer, MatchStatus, RecognitionInput,
                                                   Recognizer)
from sign2speech_v2.recognition.stability import SpeechArming, StabilityGate
from sign2speech_v2.speech.engine import SpeechEngine, create_engine
from sign2speech_v2.speech.sentences import Sentence, SentenceError, SentenceRepository
from sign2speech_v2.speech.service import SpeechService
from sign2speech_v2.state.states import (CalibrationState, GestureEditorState, GestureStoreState, HardwareState,
                                         LiveDetectionState, LiveSensorState, SentenceEditorState, SpeechState)
from sign2speech_v2.processing.filter import FlexFilter

HOST_KEEPALIVE_S = 3.0
RECONNECT_EVERY_S = 3.0
IDENTIFY_TIMEOUT_S = 3.0
OLED_UNKNOWN = "<UNKNOWN>"          # marker: the ESP32 OLED currently shows "Unknown"
NO_DATA = "WAITING FOR DATA"      # detection text whenever there is no valid sensor data
TRACE_COLUMNS = ("time", "port", "source", "raw_line", "Thumb", "Index", "Middle", "Ring", "Little",
                 "AccelX", "AccelY", "AccelZ", "GyroX", "GyroY", "GyroZ", "finger_states", "live_pattern",
                 "detection")

# Firmware identification (HardwareState.firmware_kind)
FW_CHECKING = "CHECKING"
FW_V2 = "EXPECTED V2"
FW_V2_OTHER = "V2 DIFFERENT BUILD"
FW_OLD = "OLD FIRMWARE"
FW_UNKNOWN = "UNKNOWN"
FW_BOOTLOADER = "BOOTLOADER (download mode)"
FW_NO_RESPONSE = "NO RESPONSE"
# ESP32 ROM boot messages - printed by the chip itself, not by any sketch
ROM_PREFIXES = ("ets ", "rst:", "configsip", "clk_drv", "mode:", "load:", "entry ", "ho ", "csum", "boot:",
                "flash read err", "E (", "I (", "W (")


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str


class StudioController:
    def __init__(self, workspace: Workspace, settings: Settings | None = None, *, opener: Opener = open_serial,
                 speech_engine: SpeechEngine | None = None, clock: Callable[[], float] = time.monotonic,
                 port_lister: Callable[[], list[PortInfo]] = list_ports) -> None:
        self.workspace = workspace.ensure()
        self.settings = settings or Settings.load(workspace.settings_path)
        self.clock = clock
        self._list_ports = port_lister

        # the seven states
        self.live_sensor = LiveSensorState()
        self.calibration = CalibrationState()
        self.editor = GestureEditorState()
        self.store = GestureStoreState()
        self.detection = LiveDetectionState(hold_samples=self.settings.hold_samples)
        self.speech_state = SpeechState(enabled=self.settings.speech_enabled)
        self.hardware = HardwareState()
        self.sentence_editor = SentenceEditorState()   # Speech page; independent of gestures

        # services
        self.acquisition = AcquisitionManager(opener, self.settings.stale_timeout_s)
        self.acquisition.subscribe(self._on_event)
        self.repo = GestureRepository(workspace.db_path)
        self.sentence_repo = SentenceRepository(workspace.db_path)   # own table: speech_sentences
        self.sentences: list[Sentence] = []
        self.cal_store = CalibrationStore(workspace.calibration_path)
        self.samples = SampleStore(workspace.samples_dir)
        self.recognizer: Recognizer = FingerPatternRecognizer()
        self.gate = StabilityGate(self.settings.hold_samples, self.settings.release_samples)
        self.arming = SpeechArming(self.settings.arm_neutral_samples)
        self._sync_arming()
        self.filter = FlexFilter(self.settings.filter_window)
        self.classifier: FingerStateClassifier | None = None
        engine = speech_engine or create_engine(self.settings.speech_engine, self.settings.voice)
        self.speech = SpeechService(engine, self.speech_state)
        self.speech_state.engine_name = engine.name
        self.speech_state.available = engine.available

        # diagnostics windows fed by the same stream
        self.flex_window = RollingWindow(5, 100)
        self.accel_window = RollingWindow(3, 100)
        self.gyro_window = RollingWindow(3, 100)

        self.log: deque[tuple[str, str]] = deque(maxlen=300)
        self.serial_lines: deque[tuple[bool, str]] = deque(maxlen=500)   # (is sensor frame, text)
        self._capture: CaptureSession | None = None
        self._recorded: list[SensorFrame] = []
        self._last_host_at = -1e9
        self._last_reconnect_at = -1e9
        self._oled_showing: str | None = None
        self._frames_processed = 0
        self._esp32_events = 0
        self._identify_deadline: float | None = None
        self._trace = None
        self._trace_writer = None
        self._trace_path: Path | None = None

        self._sentence_by_id: dict = {}
        if self.settings.demo_defaults:
            # preloaded calibration, speech demo, sentence library, then the 10 prototype gestures
            for msg in ensure_all_demo_data(self.repo, self.sentence_repo, self.cal_store,
                                            self.settings.bend_threshold, self.settings.hysteresis):
                self._log("info", msg)
        self._load_calibration()
        self.reload_sentences()
        self.reload_gestures()
        if self.settings.demo_defaults and any(s.builtin_demo for s in self.sentences):
            # the Speech page opens ready to demonstrate: demo sentence, Neutral
            self.sentence_editor.sentence = SPEECH_DEMO_SENTENCE
            self.sentence_editor.name = SPEECH_DEMO_NAME

    # ================================================================ start-up
    def startup(self) -> Result:
        if not self.settings.auto_connect:
            return Result(False, "Auto-connect is off. Choose a port and press CONNECT.")
        return self.auto_connect()

    def auto_connect(self) -> Result:
        """Try the preferred port (COM9 by default), then ports that look like an ESP32."""
        now = self.clock()
        self._last_reconnect_at = now
        tried = []
        candidates = [self.settings.preferred_port] + [p.device for p in self._list_ports() if p.likely_esp32]
        for port in dict.fromkeys(c for c in candidates if c):
            tried.append(port)
            res = self.connect(port)
            if res.ok:
                return res
        return Result(False, f"No ESP32 found (tried {', '.join(tried) or 'no ports'}). WAITING FOR GLOVE.")

    def available_ports(self) -> list[PortInfo]:
        return self._list_ports()

    # ============================================================== connection
    def connect(self, port: str) -> Result:
        self._reset_pipeline()
        ok, msg = self.acquisition.connect(port, self.settings.baud, self.clock())
        if ok:
            self.hardware.reset_device()
            self.hardware.port = port
            self._start_identification()
            self.acquisition.send_command("STATUS")   # V1 and V2 firmware both answer with an id line
        return Result(ok, msg)

    def reset_board(self) -> Result:
        """Restart the connected ESP32 (EN pulse, like its reset button) to replay its boot
        output and identify the firmware. Normal boot only - never flashes."""
        ok, msg = self.acquisition.reset_board(self.clock())
        if ok:
            port = self.hardware.port
            self._reset_pipeline()
            self.hardware.reset_device()
            self.hardware.port = port
            self._start_identification(extra_s=2.0)
        return Result(ok, msg)

    def generated_fingerprint(self) -> str | None:
        """Fingerprint of the firmware last generated in this workspace (config.h), if any."""
        cfg = self.firmware_dir() / "config.h"
        try:
            m = re.search(r'FIRMWARE_FINGERPRINT\s+"([0-9a-f]+)"', cfg.read_text(encoding="utf-8"))
        except OSError:
            return None
        return m.group(1) if m else None

    def _start_identification(self, extra_s: float = 0.0) -> None:
        self.hardware.firmware_kind = FW_CHECKING
        self.hardware.firmware_detail = ""
        self._identify_deadline = self.clock() + IDENTIFY_TIMEOUT_S + extra_s

    def _identified(self, kind: str, detail: str) -> None:
        self.hardware.firmware_kind = kind
        self.hardware.firmware_detail = detail
        self._identify_deadline = None
        self._log("info" if kind == FW_V2 else "warning", f"ESP32 firmware: {kind} - {detail}")

    def _check_identification(self, now: float) -> None:
        if self._identify_deadline is None or now < self._identify_deadline:
            return
        acq = self.acquisition
        if acq.frames_received:
            self._identified(FW_UNKNOWN, "streams sensor frames but did not identify itself (not the V2 firmware)")
        elif self.hardware.last_text_line:
            self._identified(FW_UNKNOWN, f'not Sign2Speech firmware - it prints "{self.hardware.last_text_line}". '
                                         "Flash the V2 firmware (Code Generator).")
        elif acq.bytes_received:
            self._identified(FW_UNKNOWN, f"{acq.bytes_received} bytes but no readable lines (wrong baud rate?)")
        else:
            self._identified(FW_NO_RESPONSE, "port open but the ESP32 sends nothing and does not answer STATUS. "
                                             "Press Reset board to see what it runs; flash the V2 firmware.")

    def disconnect(self) -> Result:
        self._oled_clear()
        self.acquisition.disconnect(self.clock(), user=True)
        self._reset_pipeline()
        self.hardware.reset_device()
        return Result(True, "Disconnected. Auto-reconnect paused until you connect again.")

    def start_simulation(self) -> Result:
        ok, msg = self.acquisition.start_simulation(self.clock())
        if ok:
            self._reset_pipeline()
            self.hardware.reset_device()
        return Result(ok, msg)

    def stop_simulation(self) -> Result:
        self.acquisition.stop_simulation(self.clock())
        self._reset_pipeline()
        return Result(True, "Simulation stopped.")

    def send_command(self, text: str) -> Result:
        ok = self.acquisition.send_command(text)
        return Result(ok, f"> {text}" if ok else "Not connected to a real ESP32.")

    # ================================================================== tick
    def tick(self) -> None:
        now = self.clock()
        self.acquisition.poll(now)
        link = self.acquisition.link_status(now)
        src = self.acquisition.source
        self.live_sensor.source = src
        self.live_sensor.link = "SIMULATION" if (src is Source.SIMULATION and link is LinkStatus.LIVE) else link.value
        self.hardware.serial = self.live_sensor.link
        self.hardware.source = src
        self.hardware.port = self.acquisition.port
        self.hardware.frames = self.acquisition.frames_received
        self.hardware.decode_errors = self.acquisition.decoder_stats.errors
        acq = self.acquisition
        self.hardware.bytes_received = acq.bytes_received
        self.hardware.last_byte_age = None if acq.last_byte_at is None else now - acq.last_byte_at
        self.hardware.last_frame_age = None if acq.last_frame_at is None else now - acq.last_frame_at
        self.hardware.last_packet = acq.latest_frame.to_line() if acq.latest_frame is not None else ""
        if src is Source.REAL:
            self._check_identification(now)
        else:
            self._identify_deadline = None
            if src is None:
                self.hardware.firmware_kind = "NOT CHECKED"

        if link is not LinkStatus.LIVE and self.live_sensor.live:
            # stream stopped: never keep showing old values as live
            self.live_sensor.clear()
            self.detection.clear(NO_DATA)
            self._reset_processing()
            self._oled_clear()
        elif link is not LinkStatus.LIVE and self.detection.live_pattern is None:
            self.detection.reason = NO_DATA

        if self._capture is not None:
            self.calibration.progress = self._capture.progress(now)
            if self._capture.done(now):
                self._finish_capture()

        if src is Source.REAL and self.settings.oled_follow_studio and now - self._last_host_at >= HOST_KEEPALIVE_S:
            self._last_host_at = now
            self.acquisition.send_command("HOST")

        if (src is None and self.settings.auto_connect and not self.acquisition.user_disconnected
                and now - self._last_reconnect_at >= RECONNECT_EVERY_S):
            self.auto_connect()

        self.speech.refresh()

    # ======================================================== event handling
    def _on_event(self, event) -> None:
        if isinstance(event, SensorFrame):
            self._process_frame(event)
            if self._trace is not None:
                self._trace_frame(event)
        elif isinstance(event, StatusLine):
            self._on_status(event)
        elif isinstance(event, RawLine):
            self.serial_lines.append((event.data, ("> " if event.outgoing else "") + event.text))
        elif isinstance(event, LinkEvent):
            self._log(event.level, event.message)
            if event.level == "error" and self.acquisition.source is None:
                self._reset_pipeline()
        elif isinstance(event, DecodeError):
            self._on_text_line(event.line.strip())

    def _process_frame(self, frame: SensorFrame) -> None:
        now = self.clock()
        ls = self.live_sensor
        filtered = self.filter.update(frame.flex)
        ls.source = frame.source
        ls.live = True
        ls.raw = frame.flex
        ls.filtered = filtered if self.filter.ready else None   # no invented start values
        ls.accel = frame.accel
        ls.gyro = frame.gyro
        ls.timestamp = frame.timestamp
        ls.frame_count += 1
        self._frames_processed += 1
        self.flex_window.add(frame.flex)
        self.accel_window.add(frame.accel)
        self.gyro_window.add(frame.gyro)

        if self._capture is not None:
            self._capture.add(frame.flex)
        if self.editor.recording:
            self._recorded.append(frame)
            self.editor.samples_recorded = len(self._recorded)

        if not self.filter.ready:
            ls.bending = ls.finger_states = None
            self.detection.clear(NO_DATA)
            return
        if self.classifier is None:
            ls.bending = ls.finger_states = None
            self.detection.clear("NOT CALIBRATED")
            return
        ls.bending = self.classifier.bending(filtered)
        live_pattern = self.classifier.pattern(filtered)
        ls.finger_states = live_pattern.states

        result = self.recognizer.recognize(RecognitionInput(live_pattern, frame, filtered, ls.bending))
        d = self.detection
        d.live_pattern = live_pattern
        d.status = result.status.value
        d.reason = result.detail
        d.candidates = result.candidates
        d.recognizer = self.recognizer.name
        if result.status is MatchStatus.MATCH and result.gesture is not None:
            d.matched_gesture = result.gesture.name
            d.matched_display_text = result.gesture.display_text
            # gesture -> its mapped sentence -> that sentence's emotion metadata (display only)
            mapped = self.sentence_for(result.gesture)
            d.matched_sentence = mapped.sentence if mapped is not None else None
            d.matched_emotion = (mapped.emotion or None) if mapped is not None else None
            d.saved_pattern = result.gesture.finger_pattern
        else:
            d.matched_gesture = d.matched_display_text = d.matched_sentence = d.matched_emotion = None
            d.saved_pattern = None

        update = self.gate.update(d.matched_gesture)
        if self.arming.update(d.matched_gesture):
            self._log("info", f"Gesture speech ARMED (no match for {self.arming.neutral_samples} frames)")
        self._sync_arming()
        if update.released:
            d.accepted = False
            d.accepted_gesture = None
        if (self.gate.latched is None and self.gate.candidate is None
                and self.gate.count >= self.gate.hold_samples):
            self._oled_unknown()      # real data, stably matching no gesture (UNKNOWN / AMBIGUOUS)
        if update.accepted:
            d.accepted = True
            d.accepted_gesture = update.accepted
            d.accept_events += 1
            d.last_accepted = update.accepted
            self._log("info", f"ACCEPTED {update.accepted} (held {self.gate.hold_samples} samples)"
                      + (f' -> "{d.matched_sentence}" [{result.gesture.speech_style}]' if d.matched_sentence else "")
                      + (f" (sentence meaning: {d.matched_emotion})" if d.matched_emotion else ""))
            self._on_accepted(result.gesture, source="studio")
        if self.gate.latched is not None:
            d.stability_count = self.gate.hold_samples
            d.accepted = True
            d.accepted_gesture = self.gate.latched
        else:
            d.stability_count = self.gate.count if d.matched_gesture and self.gate.candidate == d.matched_gesture else 0
            d.accepted = False
            d.accepted_gesture = None

    def ignored_fingers(self) -> tuple[int, ...]:
        """Fingers that are X (ANY) in EVERY enabled gesture - they cannot affect recognition (e.g. Ring)."""
        enabled = self.store.enabled_gestures
        if not enabled:
            return ()
        return tuple(i for i in range(len(FINGER_NAMES))
                     if all(g.finger_pattern.tokens[i] is PatternToken.ANY for g in enabled))

    def sentence_texts(self) -> dict[str, str]:
        """Gesture name -> mapped sentence text (for the firmware generator)."""
        return {g.name: s.sentence for g in self.store.gestures if (s := self.sentence_for(g)) is not None}

    def sentence_for(self, gesture: Gesture | None):
        """The gesture's mapped sentence record (speech_sentences), or None."""
        if gesture is None or gesture.sentence_id is None:
            return None
        return self._sentence_by_id.get(gesture.sentence_id)

    def _on_accepted(self, gesture: Gesture | None, source: str) -> None:
        if gesture is None:
            return
        sentence = self.sentence_for(gesture)
        text = sentence.sentence if sentence is not None else None
        if self.settings.speech_trigger == source:
            if source == "esp32":
                self._esp32_events += 1
            event_id = self.detection.accept_events if source == "studio" else -self._esp32_events
            if self.arming.armed:
                self.speech.on_accepted(gesture, event_id, text)  # mapped sentence, gesture's default style
            else:
                # STARTUP ARMING: shown (Studio + OLED) but not spoken until the hand has been neutral
                msg = (f"{gesture.name} recognized but NOT SPOKEN: speech is not armed yet - move to a "
                       "hand shape that matches no gesture, then perform it again")
                self.speech_state.last_message = msg
                self._log("info", msg)
        if source == "studio" and self.settings.oled_follow_studio and self.acquisition.source is Source.REAL:
            # OLED shows only the sentence (word-wrapped to 128x64), the exact text spoken above
            show = f"SHOW {gesture.name}" + (f"|{text.replace('|', ' ')}" if text else "")
            if self.acquisition.send_command(show):
                self._oled_showing = gesture.name

    def _reset_gate(self) -> None:
        """Recognition restarts: a hand already in a gesture would be accepted again, so disarm speech."""
        self.gate.reset()
        self.arming.disarm()
        self._sync_arming()

    def _sync_arming(self) -> None:
        st = self.speech_state
        st.armed, st.arm_count, st.arm_needed = self.arming.armed, self.arming.count, self.arming.neutral_samples

    def _oled_unknown(self) -> None:
        """ESP32 OLED shows 'Unknown' (sent once per change, only for real data)."""
        if (self.settings.oled_follow_studio and self._oled_showing != OLED_UNKNOWN
                and self.acquisition.source is Source.REAL and self.acquisition.send_command("UNKNOWN")):
            self._oled_showing = OLED_UNKNOWN

    def _oled_clear(self) -> None:
        """No live data any more: ESP32 OLED shows 'Waiting...'."""
        if self._oled_showing is not None and self.acquisition.source is Source.REAL:
            self.acquisition.send_command("CLEAR")
        self._oled_showing = None

    def _on_status(self, s: StatusLine) -> None:
        hw = self.hardware
        if s.kind is LineKind.DEVICE and s.device:
            dev = {"ESP32": hw.esp32, "FLEX": hw.flex, "MPU6050": hw.mpu, "OLED": hw.oled}[s.device]
            dev.known, dev.ok, dev.detail = True, bool(s.ok), s.detail
            if s.device == "OLED" and s.ok and "addr" in s.values:
                try:
                    hw.oled_address = int(s.values["addr"], 16)
                except ValueError:
                    pass
            if s.device == "MPU6050":
                hw.mpu_who_am_i = s.values.get("WHO_AM_I")
        elif s.kind is LineKind.FIRMWARE:
            hw.firmware = s.detail
            if s.detail.startswith("Sign2Speech V2"):
                board_fp = s.detail.split()[-1]
                ws_fp = self.generated_fingerprint()
                if ws_fp == board_fp:
                    self._identified(FW_V2, f"Sign2Speech V2 {board_fp} (matches the generated firmware)")
                else:
                    self._identified(FW_V2_OTHER, f"board {board_fp}, workspace {ws_fp or 'none generated'} - "
                                                  "regenerate and flash to update the gesture table")
            else:
                self._identified(FW_UNKNOWN, s.detail)
        elif s.kind is LineKind.LEGACY_FIRMWARE:
            hw.firmware = s.text
            self._identified(FW_OLD, f"V1 firmware {s.detail} from the old project - flash the V2 firmware")
        elif s.kind is LineKind.PINS:
            hw.reported_pins = dict(s.values)
            hw.pins_verified = self._verify_reported_pins(s.values)
            if not hw.pins_verified:
                self._log("error", f"ESP32 reports different pins than the glove wiring: {s.text}")
        elif s.kind is LineKind.I2C_SCAN:
            if s.bus == "OLED":
                hw.i2c_oled_found = s.found
            else:
                hw.i2c_mpu_found = s.found
        elif s.kind is LineKind.GESTURE_COUNT:
            hw.firmware_gestures = s.count
        elif s.kind is LineKind.RECOGNITION:
            hw.firmware_recognition = s.detail
        elif s.kind is LineKind.HEARTBEAT:
            for key, dev in (("MPU6050", hw.mpu), ("OLED", hw.oled)):
                if key in s.values:
                    dev.known, dev.ok = True, s.values[key].upper() == "OK"
        elif s.kind is LineKind.ADC_BEGIN:
            hw.adc_reports = []
            hw.adc_testing = True
        elif s.kind is LineKind.ADC_CHANNEL and s.adc:
            hw.adc_reports.append(s.adc)
        elif s.kind is LineKind.ADC_END:
            hw.adc_testing = False
        elif s.kind is LineKind.GESTURE and s.gesture:
            self.detection.esp32_gesture = s.gesture
            self._log("info", f"ESP32 on-device recognition: {s.gesture}")
            if self.settings.speech_trigger == "esp32":
                g = self.repo.by_name(s.gesture)
                if g is not None:
                    self._on_accepted(g, source="esp32")
                else:
                    self._log("warning", f"ESP32 reported {s.gesture}, which is not in the Studio library")

    def _on_text_line(self, line: str) -> None:
        """A received line that is neither a sensor frame nor a '#' status line."""
        if not line:
            return
        if "waiting for download" in line.lower() or "DOWNLOAD_BOOT" in line.upper():
            self._identified(FW_BOOTLOADER, f'ESP32 is in the ROM bootloader ("{line}"). Press Reset board '
                                            "(or the EN button) to run the installed firmware.")
            return
        if line.startswith(ROM_PREFIXES) or line[:1].isdigit():
            return                      # chip boot messages / half data lines are not a firmware identity
        self.hardware.last_text_line = line[:80]

    @staticmethod
    def _verify_reported_pins(values: dict) -> bool:
        expected = {"FLEX": ",".join(map(str, FLEX_PINS)), "OLED_SDA": str(OLED_SDA), "OLED_SCL": str(OLED_SCL),
                    "MPU_SDA": str(MPU_SDA), "MPU_SCL": str(MPU_SCL)}
        return all(values.get(k) == v for k, v in expected.items())

    # ============================================================ calibration
    def _load_calibration(self) -> None:
        cal = self.cal_store.load()
        self.calibration.calibration = cal
        self.classifier = FingerStateClassifier(cal) if cal else None
        self.calibration.message = "Calibration loaded." if cal else "NOT CALIBRATED"

    def start_capture(self, kind: str) -> Result:
        if not self.acquisition.is_live(self.clock()):
            return Result(False, "No live glove data. Connect the ESP32 (or start the simulation) first.")
        if kind == "closed" and (self.calibration.open_result is None or not self.calibration.open_result.ok):
            return Result(False, "Capture OPEN successfully first.")
        seconds = OPEN_SECONDS if kind == "open" else CLOSED_SECONDS
        self._capture = CaptureSession(kind, self.clock(), seconds)
        self.calibration.capturing = kind
        self.calibration.progress = 0.0
        self.calibration.message = (f"Capturing {kind.upper()} for {seconds:.0f} s - "
                                    + ("keep the hand relaxed and still" if kind == "open"
                                       else "bend all fingers fully and HOLD"))
        if kind == "open":
            self.calibration.closed_result = None
        return Result(True, self.calibration.message)

    def _finish_capture(self) -> None:
        cap, self._capture = self._capture, None
        assert cap is not None
        self.calibration.capturing = None
        self.calibration.progress = 1.0
        if cap.kind == "open":
            res = analyze_open(cap.rows)
            self.calibration.open_result = res
            self.calibration.message = ("OPEN captured. Now CAPTURE CLOSED." if res.ok
                                        else "OPEN failed: " + " ".join(res.errors))
        else:
            assert self.calibration.open_result is not None
            res = analyze_closed(self.calibration.open_result, cap.rows, self.settings.calibration_min_range)
            self.calibration.closed_result = res
            weak = [f"{q.finger}: {q.status}" for q in res.quality if q.status != "GOOD" and not q.blocking]
            self.calibration.message = ("CLOSED captured. Review the table and SAVE CALIBRATION."
                                        + (f" ({'; '.join(weak)} - allowed)" if weak else "") if res.ok
                                        else "CLOSED failed: " + " ".join(res.errors))

    def save_calibration(self) -> Result:
        res = self.calibration.closed_result
        if res is None or not res.ok:
            return Result(False, "Nothing valid to save. Capture OPEN and CLOSED first.")
        cal = res.to_calibration(self.settings.bend_threshold, self.settings.hysteresis)
        self.cal_store.save(cal)
        self.calibration.calibration = cal
        self.classifier = FingerStateClassifier(cal)
        self._reset_gate()
        weak = [f"{q.finger}: {q.status}" for q in res.quality if q.status != "GOOD"]
        self.calibration.message = f"Calibration saved ({cal.captured_at})." + (
            f" WARNING {'; '.join(weak)}." if weak else "")
        self._log("warning" if weak else "info", self.calibration.message)
        return Result(True, self.calibration.message)

    def set_calibration(self, cal: Calibration) -> None:
        """Programmatic calibration (tests / import); stored like a captured one."""
        self.cal_store.save(cal)
        self.calibration.calibration = cal
        self.classifier = FingerStateClassifier(cal)
        self._reset_gate()

    def clear_calibration(self) -> Result:
        self.cal_store.clear()
        self.calibration.calibration = None
        self.classifier = None
        self.detection.clear("NOT CALIBRATED")
        self._reset_gate()
        return Result(True, "Calibration cleared. NOT CALIBRATED.")

    # =============================================================== gestures
    def reload_gestures(self) -> None:
        self.store.gestures = self.repo.all()
        self.recognizer.set_gestures(self.store.enabled_gestures)
        self._reset_gate()
        conflicts = pattern_conflicts(self.store.gestures)
        if conflicts != self.store.conflicts:
            for a, b in conflicts:
                self._log("warning", f"AMBIGUOUS: enabled gestures {a} and {b} can match the same hand shape")
        self.store.conflicts = conflicts

    def new_gesture(self) -> Result:
        """+ NEW GESTURE: clears ONLY the editor. The live stream keeps running."""
        if self.editor.recording:
            self.stop_recording(save=False)
        self.editor.reset()
        self.store.selected_id = None
        self.editor.message = "New gesture (unsaved). Make the hand sign, then CAPTURE FROM LIVE GLOVE."
        return Result(True, self.editor.message)

    def edit_gesture(self, gesture_id: int) -> Result:
        g = self.repo.get(gesture_id)
        if g is None:
            return Result(False, "That gesture no longer exists.")
        e = self.editor
        e.reset()
        e.current_id, e.name, e.display_text, e.description = g.id, g.name, g.display_text, g.isl_description
        e.pattern, e.category, e.speech_style = g.finger_pattern, g.category, g.speech_style
        e.sentence_role, e.enabled, e.confidence_threshold = g.sentence_role, g.enabled, g.confidence_threshold
        e.sentence_id = g.sentence_id
        e.samples_recorded = self.samples.count(g.name)
        self.store.selected_id = g.id
        return Result(True, f"Editing {g.name}")

    def capture_from_live(self) -> Result:
        """Copy the CURRENT live pattern into the editor (a snapshot)."""
        if not self.live_sensor.live:
            return Result(False, "No live glove data - cannot capture. " + self.live_sensor.link)
        if self.classifier is None:
            return Result(False, "NOT CALIBRATED - calibrate first so fingers can be read as OPEN/BENT.")
        live = self.detection.live_pattern
        if live is None:
            return Result(False, "No live pattern yet.")
        self.editor.pattern = GesturePattern.from_live(live)
        self.editor.message = f"Captured live pattern {live}."
        return Result(True, self.editor.message)

    def set_editor_token(self, finger: int, token: PatternToken) -> None:
        base = self.editor.pattern or GesturePattern((PatternToken.ANY,) * 5)
        tokens = list(base.tokens)
        tokens[finger] = token
        self.editor.pattern = GesturePattern(tuple(tokens))

    def save_editor(self) -> Result:
        e = self.editor
        if e.pattern is None:
            return Result(False, "No pattern. Make the sign and press CAPTURE FROM LIVE GLOVE first.")
        g = Gesture(name=e.name, display_text=e.display_text or e.name.title(), finger_pattern=e.pattern,
                    isl_description=e.description, category=e.category, enabled=e.enabled,
                    confidence_threshold=e.confidence_threshold, speech_style=e.speech_style,
                    sentence_role=e.sentence_role, sentence_id=e.sentence_id, id=e.current_id)
        try:
            stored = self.repo.save(g)
        except GestureError as exc:
            e.message = str(exc)
            return Result(False, str(exc))
        e.current_id, e.name, e.display_text = stored.id, stored.name, stored.display_text
        self.store.selected_id = stored.id
        self.reload_gestures()
        e.message = f"Saved {stored.name} ({stored.finger_pattern})."
        return Result(True, e.message)

    def delete_gesture(self, gesture_id: int) -> Result:
        g = self.repo.get(gesture_id)
        if g is None or not self.repo.delete(gesture_id):
            return Result(False, "Gesture not found.")
        if self.editor.current_id == gesture_id:
            self.editor.reset()
        if self.store.selected_id == gesture_id:
            self.store.selected_id = None
        self.reload_gestures()
        return Result(True, f"Deleted {g.name}. Calibration, hardware settings and live data are unchanged.")

    def set_gesture_enabled(self, gesture_id: int, enabled: bool) -> Result:
        self.repo.set_enabled(gesture_id, enabled)
        self.reload_gestures()
        if self.editor.current_id == gesture_id:
            self.editor.enabled = enabled
        return Result(True, "Enabled." if enabled else "Disabled (not used by live detection).")

    def select_gesture(self, gesture_id: int | None) -> None:
        """Library selection. Affects only the library/editor - never live detection."""
        self.store.selected_id = gesture_id

    # ---------------------------------------------------------- samples
    def start_recording(self) -> Result:
        if not self.editor.name.strip():
            return Result(False, "Enter the gesture name first (samples are stored per label).")
        if not self.live_sensor.live:
            return Result(False, "No live glove data to record.")
        self._recorded = []
        self.editor.recording = True
        self.editor.samples_recorded = 0
        return Result(True, "Recording samples...")

    def stop_recording(self, save: bool = True) -> Result:
        self.editor.recording = False
        frames, self._recorded = self._recorded, []
        if not save or not frames:
            return Result(True, "Recording discarded." if frames else "No samples recorded.")
        n = self.samples.append(self.editor.name, frames)
        self.editor.samples_recorded = self.samples.count(self.editor.name)
        return Result(True, f"Saved {n} samples to {self.samples.path_for(self.editor.name).name}.")

    # ============================================================== sentences
    def reload_sentences(self) -> None:
        self.sentences = self.sentence_repo.all()
        self._sentence_by_id = {s.id: s for s in self.sentences}
        if hasattr(self, "store"):          # a deleted sentence unlinks gestures (ON DELETE SET NULL)
            self.store.gestures = self.repo.all()
            self.recognizer.set_gestures(self.store.enabled_gestures)

    def clear_sentence(self) -> Result:
        self.sentence_editor.reset()
        return Result(True, "Sentence editor cleared.")

    def load_sentence(self, sentence_id: int) -> Result:
        s = self.sentence_repo.get(sentence_id)
        if s is None:
            return Result(False, "That sentence no longer exists.")
        e = self.sentence_editor
        e.current_id, e.name, e.sentence, e.style = s.id, s.name, s.sentence, s.style
        e.emotion, e.icon, e.meaning = s.emotion, s.icon, s.meaning
        e.message = f"Loaded {s.name} ({s.style})."
        return Result(True, e.message)

    def save_sentence(self) -> Result:
        """Always stores a NEW record: the same sentence with another style is another row."""
        e = self.sentence_editor
        try:
            stored = self.sentence_repo.add(Sentence(e.name, e.sentence, e.style, emotion=e.emotion, icon=e.icon,
                                                     meaning=e.meaning))
        except SentenceError as exc:
            e.message = str(exc)
            return Result(False, str(exc))
        e.current_id, e.name, e.sentence = stored.id, stored.name, stored.sentence
        self.reload_sentences()
        e.message = f'Saved {stored.name}: "{stored.sentence}" ({stored.style}).'
        return Result(True, e.message)

    def suggested_sentence_style(self) -> str | None:
        """A style that fits the sentence's emotion metadata - a suggestion only, never applied silently."""
        return suggested_style(self.sentence_editor.emotion)

    def apply_suggested_style(self) -> Result:
        style = self.suggested_sentence_style()
        if style is None:
            return Result(False, "No suggestion for this emotion / meaning.")
        self.sentence_editor.style = style
        return Result(True, f"Speech style set to {style} (you can choose any other style).")

    def speak_sentence(self) -> Result:
        """Speak the editor's CURRENT text with the CURRENTLY selected style."""
        e = self.sentence_editor
        if not e.sentence.strip():
            return Result(False, "Type or load a sentence first.")
        r = self.speech.say(e.sentence, e.style)
        return Result(r.ok, r.message)

    def delete_sentence(self, sentence_id: int) -> Result:
        s = self.sentence_repo.get(sentence_id)
        if s is None or not self.sentence_repo.delete(sentence_id):
            return Result(False, "Sentence not found.")
        if self.sentence_editor.current_id == sentence_id:
            self.sentence_editor.current_id = None
        self.reload_sentences()
        return Result(True, f'Deleted {s.name} ({s.style}). Other styles of this sentence are kept.')

    def set_sentence_enabled(self, sentence_id: int, enabled: bool) -> Result:
        self.sentence_repo.set_enabled(sentence_id, enabled)
        self.reload_sentences()
        return Result(True, "Sentence enabled." if enabled else "Sentence disabled.")

    # ============================================================ simulation
    def set_sim_pattern(self, compact: str) -> None:
        if self.acquisition.simulator is not None:
            self.acquisition.simulator.set_pattern(compact)

    def set_sim_bend(self, finger: int, fraction: float) -> None:
        if self.acquisition.simulator is not None:
            self.acquisition.simulator.set_bend(finger, fraction)

    # ============================================================== settings
    def save_settings(self) -> None:
        self.settings.save(self.workspace.settings_path)

    def apply_settings(self) -> None:
        s = self.settings
        self.acquisition.stale_timeout_s = s.stale_timeout_s
        self.gate = StabilityGate(s.hold_samples, s.release_samples)
        self.arming = SpeechArming(s.arm_neutral_samples)
        self._sync_arming()
        self.detection.hold_samples = s.hold_samples
        self.filter = FlexFilter(s.filter_window)
        self.speech_state.enabled = s.speech_enabled
        self.save_settings()

    # ============================================================ hardware trace
    def start_trace(self) -> Result:
        """Record every received frame and what the pipeline made of it (CSV, data unchanged)."""
        if self._trace is not None:
            return Result(False, f"Already tracing to {self._trace_path}")
        folder = self.workspace.root / "traces"
        folder.mkdir(parents=True, exist_ok=True)
        self._trace_path = folder / time.strftime("trace_%Y%m%d_%H%M%S.csv")
        self._trace = self._trace_path.open("w", newline="", encoding="utf-8")
        self._trace_writer = csv.writer(self._trace)
        self._trace_writer.writerow(TRACE_COLUMNS)
        return Result(True, f"Tracing to {self._trace_path}")

    def stop_trace(self) -> Result:
        if self._trace is None:
            return Result(False, "No trace running.")
        self._trace.close()
        self._trace = None
        return Result(True, f"Trace saved: {self._trace_path}")

    @property
    def tracing(self) -> bool:
        return self._trace is not None

    def _trace_frame(self, frame: SensorFrame) -> None:
        ls, d = self.live_sensor, self.detection
        states = "".join(s.value for s in ls.finger_states) if ls.finger_states else ""
        self._trace_writer.writerow([
            f"{time.time():.3f}", self.acquisition.port or "", frame.source.name, frame.raw_line or frame.to_line(),
            *frame.flex, *frame.accel, *frame.gyro, states,
            str(d.live_pattern) if d.live_pattern else "", d.display])

    def runtime_info(self) -> dict[str, str]:
        """Where this running Studio really comes from (proves no stale/other package or workspace)."""
        import sys

        import sign2speech_v2

        src = self.acquisition.source
        return {
            "Python executable": sys.executable,
            "Package path": str(Path(sign2speech_v2.__file__).resolve().parent),
            "Workspace": str(self.workspace.root.resolve()),
            "Database": str(self.workspace.db_path.resolve()),
            "Serial port": self.acquisition.port or "(not connected)",
            "Mode": "SIMULATION" if src is Source.SIMULATION else "REAL HARDWARE",
            "Simulation active": "YES" if src is Source.SIMULATION else "NO",
            "Simulator module loaded": "YES" if "sign2speech_v2.acquisition.simulator" in sys.modules else "NO",
        }

    # ================================================================ misc
    def _reset_processing(self) -> None:
        self.filter.reset()
        if self.classifier is not None:
            self.classifier.reset()
        self._reset_gate()
        self.flex_window.clear()
        self.accel_window.clear()
        self.gyro_window.clear()

    def _reset_pipeline(self) -> None:
        self._reset_processing()
        self.live_sensor.clear()
        self.detection.clear(NO_DATA)
        self._capture = None
        self.calibration.capturing = None
        self._oled_showing = None
        if self.editor.recording:
            self.editor.recording = False
            self._recorded = []

    def _log(self, level: str, text: str) -> None:
        self.log.append((level, time.strftime("%H:%M:%S") + "  " + text))

    def firmware_dir(self) -> Path:
        return self.workspace.firmware_dir / "Sign2Speech_V2"

    def close(self) -> None:
        if self._trace is not None:
            self.stop_trace()
        self._oled_clear()
        self.acquisition.disconnect(self.clock(), user=True, quiet=True)
        self.acquisition.stop_simulation(self.clock())
        self.speech.close()
        self.repo.close()
        self.sentence_repo.close()

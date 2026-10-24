"""The seven Studio state objects.

They are deliberately separate and never reference each other:

  LiveSensorState    what the glove is doing NOW (only written by the live pipeline)
  CalibrationState   the saved calibration + capture progress
  GestureEditorState the gesture being edited in Gesture Builder
  GestureStoreState  the stored gestures + library selection
  LiveDetectionState the result of matching the CURRENT live pattern
  SpeechState        speech engine status + last spoken gesture
  HardwareState      serial / ESP32 / flex / MPU6050 / OLED status

Rules (enforced by tests):
  * The editor and the library selection are never read by the live pipeline.
  * LiveDetectionState.live_pattern is a LivePattern (O/B only, never X).
  * A stored pattern is only shown on the SAVED PATTERN line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sign2speech_v2.calibration.capture import ClosedResult, OpenResult
from sign2speech_v2.calibration.model import Calibration
from sign2speech_v2.core.frame import Source
from sign2speech_v2.core.patterns import FingerState, GesturePattern, LivePattern
from sign2speech_v2.core.status_lines import AdcChannelReport


@dataclass
class LiveSensorState:
    source: Source | None = None
    link: str = "WAITING FOR GLOVE"
    live: bool = False                            # a fresh frame arrived within the stale timeout
    raw: tuple[int, ...] | None = None
    filtered: tuple[float, ...] | None = None
    bending: tuple[float, ...] | None = None      # 0..1, None when not calibrated
    finger_states: tuple[FingerState, ...] | None = None   # None when not calibrated
    accel: tuple[float, ...] | None = None
    gyro: tuple[float, ...] | None = None
    timestamp: float | None = None
    frame_count: int = 0

    def clear(self) -> None:
        """Drop the values: they must not be shown as live any more."""
        self.live = False
        self.raw = self.filtered = self.bending = self.finger_states = None
        self.accel = self.gyro = None
        self.timestamp = None


@dataclass
class CalibrationState:
    calibration: Calibration | None = None
    capturing: str | None = None                  # "open" | "closed" | None
    progress: float = 0.0
    open_result: OpenResult | None = None
    closed_result: ClosedResult | None = None
    message: str = ""

    @property
    def calibrated(self) -> bool:
        return self.calibration is not None

    @property
    def open(self) -> tuple[int, ...] | None:
        return tuple(f.open for f in self.calibration.fingers) if self.calibration else None

    @property
    def closed(self) -> tuple[int, ...] | None:
        return tuple(f.closed for f in self.calibration.fingers) if self.calibration else None

    @property
    def range(self) -> tuple[int, ...] | None:
        return tuple(f.range for f in self.calibration.fingers) if self.calibration else None

    @property
    def threshold(self) -> tuple[int, ...] | None:
        return self.calibration.thresholds() if self.calibration else None


@dataclass
class GestureEditorState:
    current_id: int | None = None                 # None = "New gesture (unsaved)"
    name: str = ""
    display_text: str = ""
    description: str = ""
    pattern: GesturePattern | None = None
    category: str = "General"
    speech_style: str = "Neutral"
    sentence_role: str = "Word"
    enabled: bool = True
    confidence_threshold: float = 1.0
    sentence_id: int | None = None                # mapped speech sentence (speech_sentences.id)
    samples_recorded: int = 0
    recording: bool = False
    message: str = ""

    @property
    def is_new(self) -> bool:
        return self.current_id is None

    @property
    def title(self) -> str:
        return "New gesture (unsaved)" if self.is_new else f"Editing {self.name}"

    def reset(self) -> None:
        """+ NEW GESTURE: a completely empty editor."""
        fresh = GestureEditorState()
        self.__dict__.update(fresh.__dict__)


@dataclass
class GestureStoreState:
    gestures: list = field(default_factory=list)  # list[Gesture], all stored gestures
    selected_id: int | None = None                # library/editor selection ONLY
    conflicts: list = field(default_factory=list)  # [(name, name)] enabled patterns that overlap (AMBIGUOUS)

    @property
    def enabled_gestures(self) -> list:
        return [g for g in self.gestures if g.enabled]


@dataclass
class LiveDetectionState:
    live_pattern: LivePattern | None = None       # O/B only - the CURRENT hand
    status: str = "NOT READY"                     # MATCH | UNKNOWN | AMBIGUOUS | NOT READY
    reason: str = "WAITING FOR DATA"              # why NOT READY (no data at start-up)
    matched_gesture: str | None = None            # name of the unique match on this frame
    matched_display_text: str | None = None
    matched_sentence: str | None = None           # the matched gesture's mapped sentence (stored data)
    matched_emotion: str | None = None            # that SENTENCE's emotion/meaning metadata (never a match key)
    saved_pattern: GesturePattern | None = None   # the matched gesture's stored pattern (may contain X)
    candidates: tuple[str, ...] = ()
    stability_count: int = 0
    hold_samples: int = 10
    accepted: bool = False                        # the matched gesture is currently latched
    accepted_gesture: str | None = None
    accept_events: int = 0                        # increments once per acceptance
    last_accepted: str | None = None
    esp32_gesture: str | None = None              # last '# GESTURE:' line from the ESP32 (on-device)
    recognizer: str = "FINGER_PATTERN"

    @property
    def display(self) -> str:
        """LIVE DETECTION text."""
        if self.status == "MATCH" and self.matched_gesture:
            return self.matched_gesture
        if self.status == "NOT READY" and self.reason:
            return self.reason
        return self.status

    def clear(self, reason: str) -> None:
        self.live_pattern = None
        self.status = "NOT READY"
        self.reason = reason
        self.matched_gesture = self.matched_display_text = self.matched_sentence = self.matched_emotion = None
        self.saved_pattern = None
        self.candidates = ()
        self.stability_count = 0
        self.accepted = False
        self.accepted_gesture = None


@dataclass
class SpeechState:
    enabled: bool = True
    available: bool = False
    engine_name: str = "none"
    speaking: bool = False
    last_spoken_gesture: str | None = None
    last_text: str | None = None
    last_style: str | None = None
    last_message: str = ""
    spoken_count: int = 0
    # STARTUP ARMING (automatic gesture speech only; the Speech page's SPEAK is never blocked)
    armed: bool = False
    arm_count: int = 0                            # consecutive neutral (no match) frames so far
    arm_needed: int = 0

    def record(self, gesture: str | None, text: str, ok: bool, message: str, style: str | None = None) -> None:
        self.last_message = message
        if ok:
            if gesture is not None:
                self.last_spoken_gesture = gesture
            self.last_text = text
            self.last_style = style
            self.spoken_count += 1


@dataclass
class SentenceEditorState:
    """The sentence being typed/tested on the Speech page. Separate from gestures and live data."""
    current_id: int | None = None                 # the saved sentence it was loaded from, if any
    name: str = ""
    sentence: str = ""
    style: str = "Neutral"                        # speech style: HOW it is spoken (manual choice)
    emotion: str = ""                             # emotion / meaning: WHAT the sentence is about (metadata)
    icon: str = ""
    meaning: str = ""
    message: str = ""

    def reset(self) -> None:
        self.__dict__.update(SentenceEditorState().__dict__)


@dataclass
class DeviceStatus:
    known: bool = False
    ok: bool = False
    detail: str = ""

    def text(self, ok_text: str, error_text: str, unknown: str = "UNKNOWN") -> str:
        if not self.known:
            return unknown
        return ok_text if self.ok else error_text


@dataclass
class HardwareState:
    serial: str = "WAITING FOR GLOVE"
    port: str | None = None
    source: Source | None = None
    firmware: str = ""
    esp32: DeviceStatus = field(default_factory=DeviceStatus)
    flex: DeviceStatus = field(default_factory=DeviceStatus)
    mpu: DeviceStatus = field(default_factory=DeviceStatus)
    oled: DeviceStatus = field(default_factory=DeviceStatus)
    oled_address: int | None = None
    mpu_who_am_i: str | None = None
    reported_pins: dict = field(default_factory=dict)
    pins_verified: bool | None = None             # None until the firmware reports its pins
    i2c_oled_found: tuple[int, ...] | None = None
    i2c_mpu_found: tuple[int, ...] | None = None
    firmware_gestures: int | None = None
    firmware_recognition: str = ""
    adc_reports: list[AdcChannelReport] = field(default_factory=list)
    adc_testing: bool = False
    frames: int = 0
    decode_errors: int = 0
    # which firmware is on the board (see StudioController firmware identification)
    firmware_kind: str = "NOT CHECKED"
    firmware_detail: str = ""
    bytes_received: int = 0
    last_byte_age: float | None = None           # seconds since the last byte of any kind
    last_frame_age: float | None = None          # seconds since the last VALID sensor frame
    last_packet: str = ""                        # the last valid sensor frame as received
    last_text_line: str = ""                     # last non-data, non-# line (e.g. another sketch)

    def reset_device(self) -> None:
        keep = {"serial": self.serial, "port": self.port, "source": self.source}
        self.__dict__.update(HardwareState(**keep).__dict__)

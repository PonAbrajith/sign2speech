"""AcquisitionManager - the ONE serial connection in the Studio.

Every page (Live Glove, Calibration, Gesture Builder, Diagnostics) gets its
data from this single stream through subscribers; no page opens its own
port or reader.

Exactly one source is active at a time:
  REAL ESP32  - after connect(); real serial data has priority
  SIMULATION  - only after an explicit start_simulation(), and only while no
                real ESP32 is connected. A real connection is never replaced
                by simulation automatically.

The manager is polled (poll(now)) from the UI timer, which keeps it free of
threads and fully testable with a FakeTransport and a fake clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

from sign2speech_v2.acquisition.decoder import DecodeError, StreamDecoder
from sign2speech_v2.acquisition.transport import Opener, Transport, TransportError, open_serial
from sign2speech_v2.core.frame import SensorFrame, Source
from sign2speech_v2.core.status_lines import StatusLine

if TYPE_CHECKING:  # the simulator module is never loaded unless Start Simulation is pressed
    from sign2speech_v2.acquisition.simulator import SimulatedGlove


class LinkStatus(str, Enum):
    DISCONNECTED = "WAITING FOR GLOVE"
    NO_DATA = "CONNECTED — NO DATA"
    LIVE = "LIVE"
    STALE = "WAITING FOR LIVE DATA"


@dataclass(frozen=True)
class LinkEvent:
    message: str
    level: str = "info"   # info | warning | error


@dataclass(frozen=True)
class RawLine:
    """A line exactly as received/sent, for the serial monitor."""
    text: str
    outgoing: bool = False
    data: bool = False      # a valid sensor data frame


Event = SensorFrame | StatusLine | DecodeError | LinkEvent | RawLine
Subscriber = Callable[[Event], None]


class AcquisitionManager:
    def __init__(self, opener: Opener = open_serial, stale_timeout_s: float = 1.0) -> None:
        self._opener = opener
        self.stale_timeout_s = stale_timeout_s
        self._transport: Transport | None = None
        self._simulator: SimulatedGlove | None = None
        self._decoder = StreamDecoder(Source.REAL)
        self._subscribers: list[Subscriber] = []
        self.port: str | None = None
        self.opened_at: float | None = None
        self.last_frame_at: float | None = None
        self.latest_frame: SensorFrame | None = None
        self.frames_received = 0
        self.bytes_received = 0
        self.last_byte_at: float | None = None
        self.user_disconnected = False

    # --- subscription ------------------------------------------------------
    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.append(callback)

    def _publish(self, event: Event) -> None:
        for cb in list(self._subscribers):
            cb(event)

    # --- source control ----------------------------------------------------
    @property
    def source(self) -> Source | None:
        if self._transport is not None:
            return Source.REAL
        if self._simulator is not None:
            return Source.SIMULATION
        return None

    @property
    def simulator(self) -> SimulatedGlove | None:
        return self._simulator

    @property
    def decoder_stats(self):
        return self._decoder.stats

    def connect(self, port: str, baud: int, now: float) -> tuple[bool, str]:
        if self._simulator is not None:
            return False, "Simulation is running. Stop the simulation before connecting the real ESP32."
        self.disconnect(now, user=False, quiet=True)
        try:
            self._transport = self._opener(port, baud)
        except (TransportError, OSError) as exc:
            self._transport = None
            msg = str(exc)
            self._publish(LinkEvent(msg, "error"))
            return False, msg
        self.port = port
        self.opened_at = now
        self.user_disconnected = False
        self._reset_stream()
        self._decoder = StreamDecoder(Source.REAL)
        msg = f"Connected to {port} at {baud} baud (REAL ESP32)"
        self._publish(LinkEvent(msg))
        return True, msg

    def disconnect(self, now: float, user: bool = True, quiet: bool = False) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            if not quiet:
                self._publish(LinkEvent(f"Disconnected from {self.port}"))
        if user:
            self.user_disconnected = True
        self.port = None
        self.opened_at = None
        self._reset_stream()

    def start_simulation(self, now: float, seed: int | None = None) -> tuple[bool, str]:
        if self._transport is not None:
            return False, "A real ESP32 is connected. Real data has priority; disconnect it first to simulate."
        from sign2speech_v2.acquisition.simulator import SimulatedGlove

        self._simulator = SimulatedGlove(seed)
        self.opened_at = now
        self._reset_stream()
        self._publish(LinkEvent("SIMULATION started (no real glove data is shown while simulating)", "warning"))
        return True, "Simulation started"

    def stop_simulation(self, now: float) -> None:
        if self._simulator is not None:
            self._simulator = None
            self.opened_at = None
            self._reset_stream()
            self._publish(LinkEvent("Simulation stopped"))

    def _reset_stream(self) -> None:
        self.latest_frame = None
        self.last_frame_at = None
        self.frames_received = 0
        self.bytes_received = 0
        self.last_byte_at = None
        self._decoder.reset()

    # --- commands to the ESP32 ---------------------------------------------
    def send_command(self, text: str) -> bool:
        if self._transport is None:
            return False
        try:
            self._transport.write((text.strip() + "\n").encode("utf-8"))
        except TransportError as exc:
            self._publish(LinkEvent(str(exc), "error"))
            return False
        self._publish(RawLine(text.strip(), outgoing=True))
        return True

    # --- polling -----------------------------------------------------------
    def poll(self, now: float) -> None:
        if self._transport is not None:
            try:
                data = self._transport.read_available()
            except TransportError as exc:
                self._transport.close()
                self._transport = None
                lost = self.port
                self.port = None
                self.opened_at = None
                self._reset_stream()
                self._publish(LinkEvent(f"ESP32 connection lost ({lost}): {exc}", "error"))
                return
            if data:
                self.bytes_received += len(data)
                self.last_byte_at = now
                for item in self._decoder.feed(data, now):
                    self._handle(item, now)
        elif self._simulator is not None:
            for frame in self._simulator.frames_due(now):
                self._handle(frame, now)

    def _handle(self, item, now: float) -> None:
        if isinstance(item, SensorFrame):
            if item.source is not self.source:   # never mix sources
                return
            self.latest_frame = item
            self.last_frame_at = now
            self.frames_received += 1
            self._publish(RawLine(item.to_line(), data=True))
            self._publish(item)
            return
        if isinstance(item, StatusLine):
            self._publish(RawLine("# " + item.text))
        elif isinstance(item, DecodeError) and item.line:
            self._publish(RawLine(item.line))
        self._publish(item)

    def reset_board(self, now: float) -> tuple[bool, str]:
        """EN-pulse restart of the real ESP32 (replays its boot output). Never flashes."""
        if self._transport is None:
            return False, "Not connected to a real ESP32."
        reset = getattr(self._transport, "pulse_reset", None)
        if reset is None:
            return False, "This connection cannot reset the board."
        try:
            reset()
        except TransportError as exc:
            self._publish(LinkEvent(str(exc), "error"))
            return False, str(exc)
        self._decoder.reset()
        self._publish(RawLine("reset board (EN pulse, normal boot - not flashing)", outgoing=True))
        return True, "ESP32 restarted (EN pulse). Watch its boot output."

    def link_status(self, now: float) -> LinkStatus:
        if self.source is None:
            return LinkStatus.DISCONNECTED
        if self.last_frame_at is None:
            return LinkStatus.NO_DATA
        if now - self.last_frame_at > self.stale_timeout_s:
            return LinkStatus.STALE
        return LinkStatus.LIVE

    def is_live(self, now: float) -> bool:
        return self.link_status(now) is LinkStatus.LIVE

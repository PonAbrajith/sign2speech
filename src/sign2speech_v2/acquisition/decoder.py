"""Bytes -> lines -> frames / status lines (pure logic, no I/O).

Handles lines split across reads, CRLF/LF, the half line received right
after connecting, garbage from a wrong baud rate and over-long lines.
"""

from __future__ import annotations

from dataclasses import dataclass

from sign2speech_v2.core.frame import ProtocolError, SensorFrame, Source, parse_data_line
from sign2speech_v2.core.status_lines import StatusLine, is_status_line, parse_status_line

MAX_LINE_BYTES = 512


@dataclass(frozen=True)
class DecodeError:
    message: str
    line: str


DecodedItem = SensorFrame | StatusLine | DecodeError


@dataclass
class DecoderStats:
    bytes: int = 0
    frames: int = 0
    status_lines: int = 0
    errors: int = 0


class StreamDecoder:
    def __init__(self, source: Source = Source.REAL) -> None:
        self.source = source
        self.stats = DecoderStats()
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes, now: float) -> list[DecodedItem]:
        out: list[DecodedItem] = []
        self.stats.bytes += len(data)
        self._buffer.extend(data)
        while (idx := self._buffer.find(b"\n")) >= 0:
            raw = bytes(self._buffer[:idx]).rstrip(b"\r")
            del self._buffer[: idx + 1]
            item = self._line(raw, now)
            if item is not None:
                out.append(item)
        if len(self._buffer) > MAX_LINE_BYTES:
            self._buffer.clear()
            self.stats.errors += 1
            out.append(DecodeError(f"more than {MAX_LINE_BYTES} bytes without a line end (wrong baud rate?)", ""))
        return out

    def _line(self, raw: bytes, now: float) -> DecodedItem | None:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self.stats.errors += 1
            return DecodeError("undecodable bytes (wrong baud rate?)", raw[:40].decode("latin-1"))
        if not text.strip():
            return None
        if is_status_line(text):
            self.stats.status_lines += 1
            return parse_status_line(text)
        try:
            frame = parse_data_line(text, now, self.source)
        except ProtocolError as exc:
            self.stats.errors += 1
            return DecodeError(str(exc), text[:80])
        self.stats.frames += 1
        return frame

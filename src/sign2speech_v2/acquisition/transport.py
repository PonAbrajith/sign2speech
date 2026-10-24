"""Byte transports: the real serial port, and a fake one for tests.

Opening the port never resets or flashes the ESP32: DTR and RTS are kept
released. Nothing in V2 uploads firmware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

ESP32_USB_BRIDGES = {0x10C4: "Silicon Labs CP210x", 0x1A86: "WCH CH340/CH9102", 0x0403: "FTDI",
                     0x303A: "Espressif USB"}


class TransportError(OSError):
    pass


class Transport(Protocol):
    name: str

    def read_available(self) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


class PySerialTransport:
    def __init__(self, port: str, baud: int) -> None:
        import serial  # local import: tests never need a real port

        self.name = port
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = baud
        ser.timeout = 0            # non-blocking reads: the manager is polled
        ser.write_timeout = 0.2
        ser.dtr = False            # do not hold the ESP32 in reset / bootloader
        ser.rts = False
        try:
            ser.open()
        except (serial.SerialException, OSError, ValueError) as exc:
            raise TransportError(_friendly_open_error(port, exc)) from exc
        self._ser = ser

    def read_available(self) -> bytes:
        try:
            n = self._ser.in_waiting
            return self._ser.read(n) if n else b""
        except Exception as exc:  # unplugged cable raises SerialException / OSError
            raise TransportError(f"{self.name}: read failed ({exc}). Was the USB cable unplugged?") from exc

    def write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
        except Exception as exc:
            raise TransportError(f"{self.name}: write failed ({exc})") from exc

    def pulse_reset(self) -> None:
        """Restart the ESP32 like pressing its EN button: EN low via RTS while IO0 stays high
        (DTR released), so it boots the installed firmware normally. Never enters the bootloader."""
        import time

        try:
            self._ser.dtr = False
            self._ser.rts = True
            self._ser.dtr = False      # Windows driver quirk: re-apply DTR after changing RTS
            time.sleep(0.12)
            self._ser.rts = False
            self._ser.dtr = False
        except Exception as exc:
            raise TransportError(f"{self.name}: reset failed ({exc})") from exc

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


def _friendly_open_error(port: str, exc: Exception) -> str:
    text = str(exc)
    if "PermissionError" in text or "Access is denied" in text:
        return f"{port} is in use by another program (Arduino IDE serial monitor?). Close it and connect again."
    if "FileNotFoundError" in text or "could not open port" in text:
        return f"{port} was not found. Is the ESP32 plugged in with a data USB cable?"
    return f"{port}: {text}"


@dataclass
class FakeTransport:
    """In-memory transport for tests: feed() bytes in, inspect written."""

    name: str = "FAKE"
    incoming: bytearray = field(default_factory=bytearray)
    written: list[bytes] = field(default_factory=list)
    closed: bool = False
    fail_reads: bool = False
    resets: int = 0

    def feed(self, data: bytes | str) -> None:
        self.incoming.extend(data.encode() if isinstance(data, str) else data)

    def read_available(self) -> bytes:
        if self.fail_reads:
            raise TransportError(f"{self.name}: device disconnected")
        data = bytes(self.incoming)
        self.incoming.clear()
        return data

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def pulse_reset(self) -> None:
        self.resets += 1

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    likely_esp32: bool


def list_ports() -> list[PortInfo]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    out = []
    for p in lp.comports():
        bridge = ESP32_USB_BRIDGES.get(p.vid) if p.vid is not None else None
        out.append(PortInfo(p.device, bridge or p.description or "", bridge is not None))
    out.sort(key=lambda p: (not p.likely_esp32, p.device))
    return out


Opener = Callable[[str, int], Transport]


def open_serial(port: str, baud: int) -> Transport:
    return PySerialTransport(port, baud)

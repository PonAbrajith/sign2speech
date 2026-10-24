"""Shared fixtures. Every test uses a temporary workspace; a session guard
fails the run if the real workspace (studio.db / calibration.json) changes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("S2S_SPEECH", "none")   # never play audio from the default engine in tests

from sign2speech_v2.acquisition.transport import FakeTransport, PortInfo  # noqa: E402
from sign2speech_v2.calibration.model import Calibration, FingerCalibration  # noqa: E402
from sign2speech_v2.config.pins import FINGER_NAMES  # noqa: E402
from sign2speech_v2.config.settings import Settings, Workspace, default_workspace  # noqa: E402
from sign2speech_v2.controller import StudioController  # noqa: E402
from sign2speech_v2.speech.engine import SpeakResult, SpeechEngine  # noqa: E402

REAL_WS = default_workspace()


def _fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@pytest.fixture(scope="session", autouse=True)
def real_workspace_untouched():
    files = (REAL_WS.db_path, REAL_WS.calibration_path, REAL_WS.settings_path)
    before = {f: _fingerprint(f) for f in files}
    yield
    after = {f: _fingerprint(f) for f in files}
    assert before == after, "a test modified the real workspace!"


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class RecordingEngine(SpeechEngine):
    name = "recording (test)"

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        return True

    def speak(self, text: str, style: str = "Neutral") -> SpeakResult:
        self.spoken.append((text, style))
        return SpeakResult(True, f"spoke {text!r}")


OPEN_ADC = 700
CLOSED_ADC = 2600


def make_calibration(open_v: int = OPEN_ADC, closed_v: int = CLOSED_ADC) -> Calibration:
    return Calibration(tuple(FingerCalibration(n, open_v, closed_v, 4.0) for n in FINGER_NAMES))


def data_line(flex, accel=(0.01, 0.02, 1.0), gyro=(0.1, -0.2, 0.3)) -> str:
    return ",".join(map(str, flex)) + "," + ",".join(f"{v:.3f}" for v in accel) + "," + \
        ",".join(f"{v:.2f}" for v in gyro) + "\n"


def pattern_flex(compact: str) -> tuple[int, ...]:
    return tuple(CLOSED_ADC if c == "B" else OPEN_ADC for c in compact)


class Rig:
    """A controller wired to a FakeTransport ESP32 and a fake clock."""

    def __init__(self, tmp_path: Path, settings: Settings | None = None, ports=("COM9",),
                 demo: bool = False) -> None:
        self.clock = FakeClock()
        self.engine = RecordingEngine()
        self.transports: dict[str, FakeTransport] = {}
        self.open_calls: list[str] = []
        self.present = set(ports)
        self.ws = Workspace(tmp_path / "ws")
        settings = settings or Settings(auto_connect=False)
        settings.demo_defaults = demo       # most tests start from an empty library / no calibration
        self.c = StudioController(self.ws, settings, opener=self._open,
                                  speech_engine=self.engine, clock=self.clock,
                                  port_lister=lambda: [PortInfo(p, "CP210x", True) for p in sorted(self.present)])

    def _open(self, port: str, baud: int):
        self.open_calls.append(port)
        if port not in self.present:
            raise OSError(f"{port} was not found")
        t = FakeTransport(name=port)
        self.transports[port] = t
        return t

    @property
    def esp(self) -> FakeTransport:
        return self.transports[self.c.acquisition.port]

    def connect(self, port: str = "COM9"):
        res = self.c.connect(port)
        assert res.ok, res.message
        return res

    def send(self, text: str, dt: float = 0.02) -> None:
        self.esp.feed(text)
        self.clock.advance(dt)
        self.c.tick()

    def frames(self, flex, n: int = 1, dt: float = 0.02, **kw) -> None:
        for _ in range(n):
            self.send(data_line(flex, **kw), dt)

    def hold(self, compact: str, n: int) -> None:
        self.frames(pattern_flex(compact), n)

    def written(self) -> list[str]:
        return [b.decode().strip() for b in self.esp.written]


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.c.close()


@pytest.fixture
def calibrated_rig(tmp_path):
    r = Rig(tmp_path)
    r.c.set_calibration(make_calibration())
    r.connect()
    yield r
    r.c.close()

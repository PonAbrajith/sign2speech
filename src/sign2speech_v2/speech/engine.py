"""SpeechEngine -> local speech on the laptop's built-in speaker.

Engines (all offline, no cloud, no internet):
  Pyttsx3Engine - an installed local Python speech engine (preferred if installed)
  SapiEngine    - Windows' built-in System.Speech (SAPI) through one persistent
                  PowerShell worker process (no install needed)
  NullEngine    - no engine available; never claims to have spoken

Speech styles are SELECTED per gesture (rate/volume presets). The system
does not detect human emotion.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StyleVoice:
    """Selectable expressive speech style = a voice preset. Not emotion detection.

    rate   SpeechSynthesizer.Rate, -10..10 (measured on this laptop: 0 -> 2.16 s, +4 -> 1.34 s, -4 -> 3.32 s
           for "I need your help right now.")
    volume SpeechSynthesizer.Volume, 0..100
    pitch  SSML <prosody pitch> in percent. Windows voices honour it in steps: measured median voice pitch
           81 / 86 / 91 / 97 / 102 Hz for -20 / -10 / 0 / +10 / +20 % (Microsoft David Desktop).
    """
    rate: int
    volume: int
    pitch: int = 0

    @property
    def pitch_text(self) -> str:
        return f"{self.pitch:+d}%"


# Neutral is below full volume so Angry / Urgent can be delivered louder.
STYLE_VOICES: dict[str, StyleVoice] = {
    "Neutral": StyleVoice(rate=0, volume=85, pitch=0),
    "Happy": StyleVoice(rate=2, volume=90, pitch=20),      # slightly faster, brighter / higher
    "Sad": StyleVoice(rate=-3, volume=60, pitch=-20),      # slower, softer, lower
    "Angry": StyleVoice(rate=2, volume=100, pitch=-10),    # faster, strongest, lower / harder
    "Calm": StyleVoice(rate=-2, volume=70, pitch=-10),     # slower, smooth, steady
    "Urgent": StyleVoice(rate=5, volume=100, pitch=10),    # fastest, strong, raised
    "Custom": StyleVoice(rate=0, volume=85, pitch=0),
}


def style_voice(style: str) -> StyleVoice:
    return STYLE_VOICES.get(style, STYLE_VOICES["Neutral"])


@dataclass(frozen=True)
class SpeakResult:
    ok: bool
    message: str


class SpeechEngine(ABC):
    name = "engine"

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def speak(self, text: str, style: str = "Neutral") -> SpeakResult:
        """Queue `text` for speaking and return immediately."""

    @property
    def speaking(self) -> bool:
        return False

    def voices(self) -> list[str]:
        return []

    def set_voice(self, name: str) -> None:
        pass

    def close(self) -> None:
        pass


class NullEngine(SpeechEngine):
    name = "none"

    def __init__(self, reason: str = "no local speech engine available") -> None:
        self.reason = reason

    @property
    def available(self) -> bool:
        return False

    def speak(self, text: str, style: str = "Neutral") -> SpeakResult:
        return SpeakResult(False, f"NOT spoken ({self.reason}): {text!r}")


# PowerShell worker: one JSON request per stdin line, answers READY/DONE/ERR on stdout.
_SAPI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToDefaultAudioDevice()
$names = ($s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name }) -join '|'
[Console]::Out.WriteLine("READY " + $names)
[Console]::Out.Flush()
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  try {
    $req = $line | ConvertFrom-Json
    if ($req.voice) { try { $s.SelectVoice([string]$req.voice) } catch {} }
    $s.Rate = [int]$req.rate
    $s.Volume = [int]$req.volume
    if ($req.wav) { $s.SetOutputToWaveFile([string]$req.wav) } else { $s.SetOutputToDefaultAudioDevice() }
    # pitch via SSML prosody (System.Speech has no pitch property); the text is XML-escaped
    $lang = $s.Voice.Culture.Name
    $body = [System.Security.SecurityElement]::Escape([string]$req.text)
    $ssml = "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='$lang'>" +
            "<prosody pitch='$([string]$req.pitch)'>$body</prosody></speak>"
    $s.SpeakSsml($ssml)
    $s.SetOutputToDefaultAudioDevice()
    [Console]::Out.WriteLine("DONE")
  } catch {
    [Console]::Out.WriteLine("ERR " + $_.Exception.Message)
  }
  [Console]::Out.Flush()
}
"""


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    return shutil.which("powershell") or shutil.which("powershell.exe")


class SapiEngine(SpeechEngine):
    """Windows System.Speech in a persistent background PowerShell process."""

    name = "Windows SAPI (System.Speech, offline)"

    def __init__(self, voice: str = "", wav_path: str | None = None) -> None:
        self._exe = _powershell()
        self._voice = voice
        self._wav = wav_path      # tests only: render to a WAV file instead of the speaker
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._pending = 0
        self._voices: list[str] = []
        self._ready = threading.Event()
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self._exe is not None

    def _start(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        if not self._exe:
            return False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._ready.clear()
        self._proc = subprocess.Popen(
            [self._exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SAPI_SCRIPT],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", bufsize=1, creationflags=flags)
        self._pending = 0
        threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()
        return True

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("READY"):
                self._voices = [v for v in line[5:].strip().split("|") if v]
                self._ready.set()
            elif line == "DONE" or line.startswith("ERR"):
                with self._lock:
                    self._pending = max(0, self._pending - 1)
                if line.startswith("ERR"):
                    self.last_error = line[3:].strip()
        with self._lock:
            self._pending = 0
        self._ready.set()

    def speak(self, text: str, style: str = "Neutral") -> SpeakResult:
        text = text.strip()
        if not text:
            return SpeakResult(False, "nothing to speak")
        if not self._start():
            return SpeakResult(False, "Windows PowerShell not found; SAPI speech unavailable")
        request = json.dumps(self.request(text, style))
        try:
            assert self._proc is not None and self._proc.stdin is not None
            with self._lock:
                self._pending += 1
            self._proc.stdin.write(request + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            with self._lock:
                self._pending = 0
            self._proc = None
            return SpeakResult(False, f"speech worker failed: {exc}")
        return SpeakResult(True, f"speaking {text!r} ({style})")

    def request(self, text: str, style: str) -> dict:
        """The exact parameters sent to Windows speech for this text + style."""
        sv = style_voice(style)
        return {"text": text, "rate": sv.rate, "volume": sv.volume, "pitch": sv.pitch_text,
                "voice": self._voice, "wav": self._wav or ""}

    @property
    def speaking(self) -> bool:
        with self._lock:
            return self._pending > 0

    def wait_idle(self, timeout_s: float = 30.0) -> bool:
        import time

        end = time.monotonic() + timeout_s
        while self.speaking and time.monotonic() < end:
            time.sleep(0.05)
        return not self.speaking

    def voices(self) -> list[str]:
        if self._start():
            self._ready.wait(timeout=10)
        return list(self._voices)

    def set_voice(self, name: str) -> None:
        self._voice = name

    def close(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()
            self._proc = None


class Pyttsx3Engine(SpeechEngine):
    """pyttsx3 (local, offline) in a dedicated worker thread."""

    name = "pyttsx3 (local)"

    def __init__(self, voice: str = "") -> None:
        import pyttsx3  # noqa: F401  (ImportError -> caller falls back)

        self._voice = voice
        self._queue: queue.Queue = queue.Queue()
        self._busy = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def available(self) -> bool:
        return True

    def _worker(self) -> None:
        import pyttsx3

        engine = pyttsx3.init()
        base_rate = engine.getProperty("rate") or 200
        while True:
            text, style, voice = self._queue.get()
            if text is None:
                break
            sv = style_voice(style)
            try:
                if voice:
                    for v in engine.getProperty("voices"):
                        if v.name == voice:
                            engine.setProperty("voice", v.id)
                engine.setProperty("rate", int(base_rate * (1 + sv.rate * 0.08)))
                engine.setProperty("volume", sv.volume / 100)   # pyttsx3/SAPI5 driver: no pitch control
                engine.say(text)
                engine.runAndWait()
            finally:
                if self._queue.empty():
                    self._busy.clear()

    def speak(self, text: str, style: str = "Neutral") -> SpeakResult:
        if not text.strip():
            return SpeakResult(False, "nothing to speak")
        self._busy.set()
        self._queue.put((text.strip(), style, self._voice))
        return SpeakResult(True, f"speaking {text!r} ({style})")

    @property
    def speaking(self) -> bool:
        return self._busy.is_set()

    def set_voice(self, name: str) -> None:
        self._voice = name

    def close(self) -> None:
        self._queue.put((None, None, None))


def create_engine(kind: str = "auto", voice: str = "") -> SpeechEngine:
    """auto: pyttsx3 if installed, else Windows SAPI, else NullEngine."""
    if os.environ.get("S2S_SPEECH") == "none":
        return NullEngine("speech disabled by S2S_SPEECH=none")
    if kind in ("auto", "pyttsx3"):
        try:
            return Pyttsx3Engine(voice)
        except Exception:
            if kind == "pyttsx3":
                return NullEngine("pyttsx3 is not installed")
    if kind in ("auto", "sapi"):
        engine = SapiEngine(voice)
        if engine.available:
            return engine
        return NullEngine("Windows speech (PowerShell System.Speech) not available")
    return NullEngine("speech engine set to none")

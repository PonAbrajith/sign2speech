"""Compile-only arduino-cli wrapper (verifies generated firmware builds).

There is intentionally NO upload/flash function in V2. Flash the sketch
yourself with the Arduino IDE (board "ESP32 Dev Module") when you choose to.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FQBN = "esp32:esp32:esp32"
KNOWN_LOCATIONS = (
    Path(r"C:\Program Files\Arduino CLI\arduino-cli.exe"),
    Path(r"C:\Program Files\Arduino PLC IDE Tools\arduino-cli.exe"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\arduino-ide\resources\app\lib\backend\resources\arduino-cli.exe")),
    Path(r"C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"),
)


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    output: str
    command: str


def find_arduino_cli() -> str | None:
    exe = os.environ.get("S2S_ARDUINO_CLI") or shutil.which("arduino-cli")
    if exe:
        return exe
    return next((str(p) for p in KNOWN_LOCATIONS if p.exists()), None)


def compile_sketch(sketch_dir: Path, exe: str | None = None, timeout_s: float = 600) -> CompileResult:
    exe = exe or find_arduino_cli()
    if not exe:
        return CompileResult(False, "arduino-cli not found (set S2S_ARDUINO_CLI or install Arduino CLI).", "")
    cmd = [exe, "compile", "--fqbn", FQBN, str(sketch_dir)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CompileResult(False, str(exc), " ".join(cmd))
    return CompileResult(proc.returncode == 0, (proc.stdout + proc.stderr).strip(), " ".join(cmd))

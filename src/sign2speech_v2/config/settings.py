"""Studio settings (workspace/settings.json) and workspace paths.

Settings hold tunables only - never pins (see config.pins) and never
calibration or gestures (they have their own stores).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DEFAULT_PORT = "COM9"
DEFAULT_BAUD = 115200


@dataclass
class Settings:
    preferred_port: str = DEFAULT_PORT     # tried first at start-up; any port can be chosen in the UI
    baud: int = DEFAULT_BAUD
    auto_connect: bool = True              # try preferred port (then ESP32-looking ports) at start-up
    stale_timeout_s: float = 1.0           # no frame for this long -> WAITING FOR LIVE DATA
    filter_window: int = 5                 # moving average, samples
    hysteresis: float = 0.08               # bend fraction either side of the threshold
    bend_threshold: float = 0.5            # fraction of open->closed range that counts as BENT
    calibration_min_range: int = 20        # counts a primary finger must move (and >= 5x its noise)
    hold_samples: int = 10                 # ~0.2 s at 50 Hz before a gesture is accepted
    release_samples: int = 5               # frames away from the gesture before it can repeat
    arm_neutral_samples: int = 25          # ~0.5 s of no match before automatic gesture speech is armed
    speech_trigger: str = "studio"         # "studio" = Studio's live detection, "esp32" = '# GESTURE:' lines
    speech_enabled: bool = True
    speech_engine: str = "auto"            # auto | pyttsx3 | sapi | none
    voice: str = ""                        # installed voice name; "" = system default
    oled_follow_studio: bool = True        # send the Studio's accepted gesture to the ESP32 OLED
    demo_defaults: bool = True             # seed BUILT-IN DEMO gestures + PRELOADED CALIBRATION once

    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "studio.db"

    @property
    def calibration_path(self) -> Path:
        return self.root / "calibration.json"

    @property
    def settings_path(self) -> Path:
        return self.root / "settings.json"

    @property
    def samples_dir(self) -> Path:
        return self.root / "dataset" / "samples"

    @property
    def firmware_dir(self) -> Path:
        return self.root / "firmware"

    def ensure(self) -> "Workspace":
        self.samples_dir.mkdir(parents=True, exist_ok=True)
        self.firmware_dir.mkdir(parents=True, exist_ok=True)
        return self


def default_workspace() -> Workspace:
    # src/sign2speech_v2/config/settings.py -> project root
    return Workspace(Path(__file__).resolve().parents[3] / "workspace")

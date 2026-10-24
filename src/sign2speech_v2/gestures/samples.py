"""Optional labelled sample recording: workspace/dataset/samples/<LABEL>.csv

Row: timestamp, gesture, Flex1..Flex5 (raw ADC), AccelX..Z (g), GyroX..Z (deg/s), source.
Samples are NOT needed by the deterministic finger-pattern recognizer; they
exist so an ML recognizer can be trained later.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sign2speech_v2.core.frame import SensorFrame
from sign2speech_v2.gestures.repository import NAME_RE, normalize_name

COLUMNS = ("timestamp", "gesture", "Flex1", "Flex2", "Flex3", "Flex4", "Flex5",
           "AccelX", "AccelY", "AccelZ", "GyroX", "GyroY", "GyroZ", "source")


class SampleStore:
    def __init__(self, samples_dir: Path) -> None:
        self.dir = samples_dir

    def path_for(self, label: str) -> Path:
        label = normalize_name(label)
        if not NAME_RE.match(label):
            raise ValueError(f"invalid gesture label {label!r}")
        return self.dir / f"{label}.csv"

    def append(self, label: str, frames: list[SensorFrame]) -> int:
        if not frames:
            return 0
        path = self.path_for(label)
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(COLUMNS)
            for f in frames:
                w.writerow([f"{f.timestamp:.3f}", normalize_name(label), *f.flex,
                            *(f"{v:.3f}" for v in f.accel), *(f"{v:.2f}" for v in f.gyro), f.source.name.lower()])
        return len(frames)

    def count(self, label: str) -> int:
        path = self.path_for(label)
        if not path.exists():
            return 0
        with path.open(encoding="utf-8") as fh:
            return max(0, sum(1 for _ in fh) - 1)

    def delete(self, label: str) -> bool:
        path = self.path_for(label)
        if path.exists():
            path.unlink()
            return True
        return False

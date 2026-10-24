"""Per-finger moving-average filter. Raw values are always kept alongside."""

from __future__ import annotations

from collections import deque
from typing import Sequence


class FlexFilter:
    def __init__(self, window: int = 5, channels: int = 5) -> None:
        if not 1 <= window <= 64:
            raise ValueError(f"filter window must be 1..64, got {window}")
        self.window = window
        self._hist = [deque(maxlen=window) for _ in range(channels)]

    def reset(self) -> None:
        for h in self._hist:
            h.clear()

    @property
    def ready(self) -> bool:
        """True once the window holds `window` real samples (no invented start values)."""
        return len(self._hist[0]) >= self.window

    def update(self, raw: Sequence[int]) -> tuple[float, ...]:
        if len(raw) != len(self._hist):
            raise ValueError(f"expected {len(self._hist)} values, got {len(raw)}")
        for h, v in zip(self._hist, raw):
            h.append(int(v))
        return tuple(sum(h) / len(h) for h in self._hist)

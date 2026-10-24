"""Small reusable widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget)

from sign2speech_v2.config.pins import FINGER_NAMES, finger_pin_label
from sign2speech_v2.ui import theme


def label(text: str = "", name: str | None = None, color: str | None = None, size: int | None = None,
          bold: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    style = []
    if color:
        style.append(f"color: {color};")
    if size:
        style.append(f"font-size: {size}pt;")
    if bold:
        style.append("font-weight: 600;")
    if style:
        lab.setStyleSheet(" ".join(style))
    return lab


def set_color(lab: QLabel, color: str, size: int | None = None, bold: bool = False) -> None:
    style = f"color: {color};" + (f" font-size: {size}pt;" if size else "") + (" font-weight: 600;" if bold else "")
    if lab.styleSheet() != style:
        lab.setStyleSheet(style)


def set_text(lab: QLabel, text: str) -> None:
    if lab.text() != text:
        lab.setText(text)


class Card(QFrame):
    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        if title:
            self.body.addWidget(label(title, "h2"))


class StatusTile(Card):
    """Title + big value + detail line (Dashboard)."""

    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.value = label("—", size=13, bold=True)
        self.detail = label("", "muted")
        self.detail.setWordWrap(True)
        self.body.addWidget(self.value)
        self.body.addWidget(self.detail)

    def show(self, value: str, color: str, detail: str = "") -> None:  # type: ignore[override]
        set_text(self.value, value)
        set_color(self.value, color, 13, True)
        set_text(self.detail, detail)


class FingerMeters(QWidget):
    """LIVE FINGER DATA: one row per finger - name, bar, bending %, RAW, FILTERED, OPEN/BENT."""

    def __init__(self) -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        for col, head in enumerate(("FINGER", "BENDING", "", "RAW ADC", "FILTERED", "STATE")):
            grid.addWidget(label(head, "muted"), 0, col)
        self.bars, self.pct, self.raw, self.filt, self.state = [], [], [], [], []
        for i, name in enumerate(FINGER_NAMES):
            n = label(name, bold=True, size=11)
            n.setToolTip(finger_pin_label(i))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(18)
            bar.setMinimumWidth(220)
            pct = label("—", size=11)
            raw = label("—", size=11)
            filt = label("—", size=11)
            state = label("—", size=12, bold=True)
            for col, w in enumerate((n, bar, pct, raw, filt, state)):
                grid.addWidget(w, i + 1, col)
            self.bars.append(bar)
            self.pct.append(pct)
            self.raw.append(raw)
            self.filt.append(filt)
            self.state.append(state)
        grid.setColumnStretch(1, 1)

    def update_values(self, raw, filtered, bending, states, ignored=()) -> None:
        """`ignored`: fingers no enabled gesture uses (X everywhere) - shown as IGNORED, values still shown."""
        for i in range(len(FINGER_NAMES)):
            if raw is None:
                self.bars[i].setValue(0)
                for lab in (self.pct[i], self.raw[i], self.filt[i], self.state[i]):
                    set_text(lab, "—")
                set_color(self.state[i], theme.MUTED, 12, True)
                continue
            set_text(self.raw[i], f"ADC {raw[i]}")
            if filtered is None:                    # fewer real samples than the filter window
                self.bars[i].setValue(0)
                set_text(self.filt[i], "WAITING")
                set_text(self.pct[i], "—")
                set_text(self.state[i], "WAITING FOR DATA")
                set_color(self.state[i], theme.MUTED, 10, True)
                continue
            set_text(self.filt[i], f"{filtered[i]:.0f}")
            if bending is None:
                self.bars[i].setValue(int(min(100, filtered[i] / 4095 * 100)))
                set_text(self.pct[i], "n/a")
                set_text(self.state[i], "NOT CALIBRATED")
                set_color(self.state[i], theme.WARN, 10, True)
            else:
                p = int(round(bending[i] * 100))
                self.bars[i].setValue(p)
                set_text(self.pct[i], f"{p}%")
                word = states[i].word if states else "—"
                if i in ignored:
                    set_text(self.state[i], "IGNORED")
                    set_color(self.state[i], theme.MUTED, 12, True)
                    self.state[i].setToolTip(f"Reads {word} now; every enabled gesture has X (ANY) for this finger.")
                    continue
                self.state[i].setToolTip("")
                set_text(self.state[i], word)
                set_color(self.state[i], theme.LIVE if word == "OPEN" else theme.DETECT, 12, True)


def pattern_row(title: str, color: str) -> tuple[QWidget, QLabel]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    t = label(title, color=color, bold=True, size=11)
    t.setMinimumWidth(170)
    v = label("—", color=color, bold=True, size=18)
    v.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(t)
    lay.addWidget(v, 1)
    return w, v

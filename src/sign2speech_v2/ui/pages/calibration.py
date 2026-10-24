from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem

from sign2speech_v2.calibration.capture import DEAD, GOOD, WEAK, assess
from sign2speech_v2.config.pins import FINGER_NAMES, finger_pin_label
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.pages.base import Page
from sign2speech_v2.ui.widgets import Card, FingerMeters, label, set_color, set_text

COLUMNS = ("Finger", "Open", "Closed", "Range", "Noise", "Quality", "Threshold", "Inverted")
QUALITY_COLOR = {GOOD: theme.LIVE, WEAK: theme.WARN, DEAD: theme.ERROR}


class CalibrationPage(Page):
    title = "Calibration"

    def __init__(self, *a) -> None:
        super().__init__(*a)
        intro = label("Calibration is a one-time setup: it measures each finger's OPEN and CLOSED readings from the "
                      "real live stream. Live values are shown without calibration; OPEN/BENT needs it.", "muted")
        intro.setWordWrap(True)
        self.layout_.addWidget(intro)

        live = Card("LIVE FINGER DATA")
        self.meters = FingerMeters()
        live.body.addWidget(self.meters)
        self.layout_.addWidget(live)

        steps = Card("CAPTURE")
        row = QHBoxLayout()
        self.btn_open = QPushButton("1. CAPTURE OPEN (2 s)")
        self.btn_closed = QPushButton("2. CAPTURE CLOSED (3 s)")
        self.btn_save = QPushButton("3. SAVE CALIBRATION")
        self.btn_save.setObjectName("primary")
        self.btn_clear = QPushButton("Clear calibration")
        self.btn_clear.setObjectName("danger")
        self.btn_open.clicked.connect(lambda: self.act(self.c.start_capture("open")))
        self.btn_closed.clicked.connect(lambda: self.act(self.c.start_capture("closed")))
        self.btn_save.clicked.connect(lambda: self.act(self.c.save_calibration()))
        self.btn_clear.clicked.connect(lambda: self.act(self.c.clear_calibration()))
        for b in (self.btn_open, self.btn_closed, self.btn_save):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_clear)
        steps.body.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        steps.body.addWidget(self.progress)
        self.status = label("", size=11, bold=True)
        self.status.setWordWrap(True)
        steps.body.addWidget(self.status)
        self.messages = label("", "muted")
        steps.body.addWidget(self.messages)
        self.layout_.addWidget(steps)

        tbl = Card("CURRENT CALIBRATION  (saved configuration — NOT live sensor readings)")
        self.which = label("", "muted")
        tbl.body.addWidget(self.which)
        self.table = QTableWidget(len(FINGER_NAMES), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(200)
        self.table.horizontalHeader().setStretchLastSection(True)
        tbl.body.addWidget(self.table)
        self.layout_.addWidget(tbl)
        self.layout_.addStretch(1)

    def refresh(self) -> None:
        c = self.c
        ls, cs = c.live_sensor, c.calibration
        self.meters.update_values(ls.raw if ls.live else None, ls.filtered, ls.bending, ls.finger_states)
        self.progress.setValue(int(cs.progress * 100) if cs.capturing else (100 if cs.progress >= 1 else 0))
        state = ("CAPTURING " + cs.capturing.upper()) if cs.capturing else (
            "CALIBRATED" if cs.calibrated else "NOT CALIBRATED")
        set_text(self.status, f"{state} — {cs.message}" if cs.message else state)
        set_color(self.status, theme.LIVE if cs.calibrated and not cs.capturing else theme.WARN, 11, True)
        msgs = []
        for res in (cs.open_result, cs.closed_result):
            if res is not None:
                msgs += [f"✖ {e}" for e in res.errors] + [f"⚠ {w}" for w in res.warnings]
        set_text(self.messages, "\n".join(dict.fromkeys(msgs)))
        busy = cs.capturing is not None
        self.btn_open.setEnabled(not busy and ls.live)
        self.btn_closed.setEnabled(not busy and ls.live and cs.open_result is not None and cs.open_result.ok)
        self.btn_save.setEnabled(not busy and cs.closed_result is not None and cs.closed_result.ok)

        # pending (just captured, unsaved) calibration first, else the saved one
        rows = quality = None
        pending = cs.closed_result
        if pending is not None and pending.fingers and (cs.calibration is None
                                                        or cs.calibration.fingers != pending.fingers):
            rows, quality = pending.fingers, pending.quality
            src = "Captured — NOT SAVED" + ("" if pending.ok else " (has errors, cannot be saved)")
        elif cs.calibration is not None:
            rows = cs.calibration.fingers
            quality = tuple(assess(f, c.settings.calibration_min_range) for f in rows)
            src = (f"{cs.calibration.label} — old values from an earlier measurement, NOT verified on this glove "
                   "now. CAPTURE OPEN, CAPTURE CLOSED, SAVE." if cs.calibration.preloaded
                   else f"{cs.calibration.label or 'Saved calibration'} ({cs.calibration.captured_at})")
        else:
            src = "No calibration yet."
        set_text(self.which, src)
        set_color(self.which, theme.WARN if cs.calibration is not None and cs.calibration.preloaded
                  and rows is cs.calibration.fingers else theme.MUTED)
        cal = cs.calibration
        t = c.settings.bend_threshold if cal is None else cal.bend_threshold
        h = c.settings.hysteresis if cal is None else cal.hysteresis
        for i in range(len(FINGER_NAMES)):
            q = quality[i] if quality else None
            if rows is None:
                vals = (finger_pin_label(i),) + ("—",) * 7
            else:
                f = rows[i]
                # OPEN/BENT points derived from this finger's own open/closed (hysteresis deadband between)
                lo, hi = f.threshold(t - h), f.threshold(t + h)
                thr = f"OPEN {'<' if not f.inverted else '>'} {lo} · BENT {'>' if not f.inverted else '<'} {hi}"
                vals = (finger_pin_label(i), str(f.open), str(f.closed), str(f.range), f"±{f.noise:.1f}",
                        q.status if q else "—", thr, "yes" if f.inverted else "no")
            for col, v in enumerate(vals):
                item = self.table.item(i, col)
                if item is None:
                    item = QTableWidgetItem(v)
                    self.table.setItem(i, col, item)
                elif item.text() != v:
                    item.setText(v)
                if col == 5:
                    item.setForeground(QColor(QUALITY_COLOR.get(v, theme.MUTED)))
                    item.setToolTip(q.reason if q else "")

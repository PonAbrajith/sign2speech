from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QPlainTextEdit, QWidget

from sign2speech_v2.core.frame import Source
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.pages.base import Page
from sign2speech_v2.ui.widgets import Card, StatusTile, label, set_color, set_text


class DashboardPage(Page):
    title = "Dashboard"

    def __init__(self, *a) -> None:
        super().__init__(*a)
        det = Card("LIVE DETECTION  (from the current glove data)")
        self.detection = label("—", color=theme.DETECT, size=30, bold=True)
        self.detail = label("", "muted")
        det.body.addWidget(self.detection)
        det.body.addWidget(self.detail)
        self.layout_.addWidget(det)

        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.tiles = {}
        for i, key in enumerate(("SOURCE", "SERIAL", "ESP32", "FLEX", "MPU6050", "OLED", "CALIBRATION", "GESTURES",
                                 "SPEECH")):
            tile = StatusTile(key)
            self.tiles[key] = tile
            grid.addWidget(tile, i // 3, i % 3)
        self.layout_.addWidget(grid_w)

        log = Card("EVENT LOG")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMinimumHeight(160)
        log.body.addWidget(self.log)
        self.layout_.addWidget(log)
        self._log_len = 0

    def refresh(self) -> None:
        c = self.c
        ls, hw, d = c.live_sensor, c.hardware, c.detection
        set_text(self.detection, d.display if ls.live else "WAITING FOR DATA")
        set_text(self.detail, f"LIVE PATTERN: {d.live_pattern}" if d.live_pattern else "")

        src = ls.source
        self.tiles["SOURCE"].show(src.value if src else "NONE", theme.SIM if src is Source.SIMULATION else
                                  theme.LIVE if src is Source.REAL else theme.MUTED, hw.port or "")
        link_ok = ls.link in ("LIVE", "SIMULATION")
        self.tiles["SERIAL"].show(ls.link, theme.LIVE if link_ok else theme.WARN,
                                  f"{hw.frames} frames, {hw.decode_errors} bad lines")
        fw_ok = hw.firmware_kind == "EXPECTED V2"
        esp = hw.esp32.text("OK", "ERROR", "NO REPORT") if hw.firmware_kind in ("NOT CHECKED", "CHECKING") \
            else hw.firmware_kind
        self.tiles["ESP32"].show(esp, theme.LIVE if fw_ok else theme.MUTED if hw.source is None else theme.WARN,
                                 hw.firmware_detail or hw.firmware or hw.esp32.detail)
        flex_txt = "CHECK FLEX WIRING" if (hw.flex.known and not hw.flex.ok) else hw.flex.text("OK", "", "NO REPORT")
        if ls.raw is not None and all(v <= 5 for v in ls.raw):
            flex_txt = "CHECK FLEX WIRING"
        self.tiles["FLEX"].show(flex_txt, theme.ERROR if "CHECK" in flex_txt else _c(hw.flex),
                                "raw " + ", ".join(map(str, ls.raw)) if ls.raw else "")
        self.tiles["MPU6050"].show(hw.mpu.text("OK", "MPU6050 ERROR", "NO REPORT"), _c(hw.mpu),
                                   (f"WHO_AM_I={hw.mpu_who_am_i}" if hw.mpu_who_am_i else hw.mpu.detail))
        oled = hw.oled.text(f"OK 0x{hw.oled_address:02X}" if hw.oled_address else "OK", "OLED NOT DETECTED",
                            "NO REPORT")
        self.tiles["OLED"].show(oled, _c(hw.oled), "SDA D18 / SCL D19")
        cal = c.calibration
        cal_label = cal.calibration.label if cal.calibration else ""
        self.tiles["CALIBRATION"].show(cal_label or ("CALIBRATED" if cal.calibrated else "NOT CALIBRATED"),
                                       theme.WARN if not cal.calibrated or cal.calibration.preloaded else theme.LIVE,
                                       "capture OPEN/CLOSED on the Calibration page" if cal_label
                                       else cal.calibration.captured_at if cal.calibration else "")
        n_all, n_en = len(c.store.gestures), len(c.store.enabled_gestures)
        self.tiles["GESTURES"].show(f"{n_en} enabled", theme.STORED if n_en else theme.WARN, f"{n_all} stored")
        sp = c.speech_state
        last = sp.last_text or "—"
        self.tiles["SPEECH"].show(("SPEAKING" if sp.speaking else "READY") if sp.available else "UNAVAILABLE",
                                  theme.LIVE if sp.available else theme.ERROR,
                                  f"{'ARMED' if sp.armed else 'NOT ARMED (hold a neutral hand shape)'}; "
                                  f"{sp.engine_name}; last: {last}")
        if len(c.log) != self._log_len or (c.log and self._log_len == c.log.maxlen):
            self.log.setPlainText("\n".join(t for _, t in c.log))
            self._log_len = len(c.log)
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())


def _c(dev) -> str:
    if not dev.known:
        return theme.MUTED
    return theme.LIVE if dev.ok else theme.ERROR

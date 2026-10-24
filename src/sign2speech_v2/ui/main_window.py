from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from sign2speech_v2.controller import Result, StudioController
from sign2speech_v2.core.frame import Source
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.pages.calibration import CalibrationPage
from sign2speech_v2.ui.pages.code_generator import CodeGeneratorPage
from sign2speech_v2.ui.pages.dashboard import DashboardPage
from sign2speech_v2.ui.pages.diagnostics import DiagnosticsPage
from sign2speech_v2.ui.pages.gesture_builder import GestureBuilderPage
from sign2speech_v2.ui.pages.gesture_library import GestureLibraryPage
from sign2speech_v2.ui.pages.live_glove import LiveGlovePage
from sign2speech_v2.ui.pages.speech import SpeechPage
from sign2speech_v2.ui.widgets import label, set_color, set_text

TICK_MS = 15          # acquisition poll (the ESP32 streams at 50 Hz)
REFRESH_MS = 100      # UI repaint

PAGE_ORDER = ("Dashboard", "Live Glove", "Calibration", "Gesture Builder", "Gesture Library", "Speech",
              "Hardware Diagnostics", "Code Generator")


class MainWindow(QMainWindow):
    def __init__(self, controller: StudioController, start_timers: bool = True) -> None:
        super().__init__()
        self.c = controller
        self.setWindowTitle("Sign2Speech V2 Studio")
        self.resize(1280, 860)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._connection_bar())

        body = QHBoxLayout()
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(230)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        self.pages = {
            "Dashboard": DashboardPage(controller, self.notify),
            "Live Glove": LiveGlovePage(controller, self.notify),
            "Calibration": CalibrationPage(controller, self.notify),
            "Gesture Builder": GestureBuilderPage(controller, self.notify),
            "Gesture Library": GestureLibraryPage(controller, self.notify, self._open_builder),
            "Speech": SpeechPage(controller, self.notify),
            "Hardware Diagnostics": DiagnosticsPage(controller, self.notify),
            "Code Generator": CodeGeneratorPage(controller, self.notify),
        }
        for name in PAGE_ORDER:
            self.nav.addItem(name)
            self.stack.addWidget(self.pages[name])
        self.nav.currentRowChanged.connect(self._page_changed)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.status = self.statusBar()
        self.nav.setCurrentRow(PAGE_ORDER.index("Live Glove"))

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.c.tick)
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.refresh)
        if start_timers:
            self.tick_timer.start(TICK_MS)
            self.ui_timer.start(REFRESH_MS)

    # ---- connection bar ------------------------------------------------------
    def _connection_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setStyleSheet(f"QFrame#topbar {{ background: {theme.PANEL}; border-bottom: 1px solid {theme.BORDER}; }}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.addWidget(label("SIGN2SPEECH V2", size=12, bold=True))
        lay.addSpacing(16)
        self.source_badge = QLabel("")
        lay.addWidget(self.source_badge)
        self.link = label("", size=11, bold=True)
        lay.addWidget(self.link)
        lay.addStretch(1)
        self.port_box = QComboBox()
        self.port_box.setMinimumWidth(240)
        self.port_box.setEditable(True)
        lay.addWidget(self.port_box)
        refresh = QPushButton("↻")
        refresh.setToolTip("Refresh serial ports")
        refresh.clicked.connect(self.refresh_ports)
        lay.addWidget(refresh)
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setObjectName("live")
        self.btn_connect.clicked.connect(self._toggle_connect)
        lay.addWidget(self.btn_connect)
        self.btn_sim = QPushButton("Start Simulation")
        self.btn_sim.clicked.connect(self._toggle_sim)
        lay.addWidget(self.btn_sim)
        self.refresh_ports()
        return bar

    def refresh_ports(self) -> None:
        current = self.port_box.currentData() or self.c.settings.preferred_port
        self.port_box.clear()
        ports = self.c.available_ports()
        for p in ports:
            self.port_box.addItem(f"{p.device}  {p.description}" + ("  (ESP32?)" if p.likely_esp32 else ""),
                                  p.device)
        if not any(p.device == self.c.settings.preferred_port for p in ports):
            self.port_box.addItem(f"{self.c.settings.preferred_port}  (preferred, not present)",
                                  self.c.settings.preferred_port)
        idx = self.port_box.findData(current)
        self.port_box.setCurrentIndex(max(0, idx))

    def _chosen_port(self) -> str:
        data = self.port_box.currentData()
        if data and self.port_box.currentText().startswith(data):
            return data
        return self.port_box.currentText().split()[0] if self.port_box.currentText().strip() else ""

    def _toggle_connect(self) -> None:
        if self.c.acquisition.source is Source.REAL:
            self.notify(self.c.disconnect())
        else:
            port = self._chosen_port()
            res = self.c.connect(port) if port else Result(False, "Choose a serial port.")
            if res.ok:
                self.c.settings.preferred_port = port
                self.c.save_settings()
            self.notify(res)
        self.refresh()

    def _toggle_sim(self) -> None:
        if self.c.acquisition.source is Source.SIMULATION:
            self.notify(self.c.stop_simulation())
        else:
            self.notify(self.c.start_simulation())
        self.refresh()

    # ---- helpers ---------------------------------------------------------------
    def notify(self, result: Result) -> None:
        self.status.showMessage(("✓ " if result.ok else "✖ ") + result.message, 8000)

    def _open_builder(self) -> None:
        builder = self.pages["Gesture Builder"]
        builder._load_editor()
        self.nav.setCurrentRow(PAGE_ORDER.index("Gesture Builder"))

    def _page_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        self.refresh()

    def refresh(self) -> None:
        c = self.c
        src = c.acquisition.source
        if src is Source.REAL:
            text, color = "REAL ESP32", theme.LIVE
        elif src is Source.SIMULATION:
            text, color = "SIMULATION", theme.SIM
        else:
            text, color = "REAL HARDWARE", theme.MUTED     # default mode; no data source connected yet
        set_text(self.source_badge, f"  {text}  ")
        style = f"background: {color}; color: #111; border-radius: 4px; font-weight: 700; padding: 2px 4px;"
        if self.source_badge.styleSheet() != style:
            self.source_badge.setStyleSheet(style)
        link = c.live_sensor.link
        set_text(self.link, link + (f"  {c.acquisition.port}" if c.acquisition.port else ""))
        set_color(self.link, theme.LIVE if link in ("LIVE", "SIMULATION") else theme.WARN, 11, True)
        set_text(self.btn_connect, "DISCONNECT" if src is Source.REAL else "CONNECT")
        self.btn_connect.setEnabled(src is not Source.SIMULATION)
        set_text(self.btn_sim, "Stop Simulation" if src is Source.SIMULATION else "Start Simulation")
        self.btn_sim.setEnabled(src is not Source.REAL)
        self.stack.currentWidget().refresh()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.tick_timer.stop()
        self.ui_timer.stop()
        self.c.close()
        super().closeEvent(event)

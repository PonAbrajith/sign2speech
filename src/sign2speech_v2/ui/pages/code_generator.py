from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPlainTextEdit, QPushButton

from sign2speech_v2.codegen.arduino_cli import compile_sketch
from sign2speech_v2.codegen.generator import GeneratedFirmware, generate
from sign2speech_v2.config import pins
from sign2speech_v2.controller import Result
from sign2speech_v2.ui import theme
from sign2speech_v2.ui.pages.base import Page
from sign2speech_v2.ui.widgets import Card, label, set_color, set_text


class _CompileWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, path) -> None:
        super().__init__()
        self.path = path

    def run(self) -> None:
        r = compile_sketch(self.path)
        self.finished.emit(r.ok, r.output[-4000:])


class CodeGeneratorPage(Page):
    title = "Code Generator"

    def __init__(self, *a) -> None:
        super().__init__(*a)
        info = Card("CONFIGURATION  (locked hardware + current calibration + enabled gestures)")
        self.summary = label("", size=10)
        info.body.addWidget(self.summary)
        note = label("The Studio only WRITES the sketch. It never uploads or flashes the ESP32. Open the .ino in "
                     "the Arduino IDE (board: ESP32 Dev Module) and upload it yourself when you choose.", "muted")
        note.setWordWrap(True)
        info.body.addWidget(note)
        row = QHBoxLayout()
        gen = QPushButton("GENERATE ESP32 FIRMWARE")
        gen.setObjectName("primary")
        gen.clicked.connect(self._generate)
        self.verify = QPushButton("Verify compile (arduino-cli, no upload)")
        self.verify.clicked.connect(self._compile)
        self.verify.setEnabled(False)
        row.addWidget(gen)
        row.addWidget(self.verify)
        row.addStretch(1)
        info.body.addLayout(row)
        self.result = label("", size=10)
        info.body.addWidget(self.result)
        self.layout_.addWidget(info)

        prev = Card("GENERATED FILES")
        self.file_box = QComboBox()
        self.file_box.currentTextChanged.connect(self._show_file)
        prev.body.addWidget(self.file_box)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(360)
        self.preview.setStyleSheet("font-family: Consolas; font-size: 9pt;")
        prev.body.addWidget(self.preview)
        self.layout_.addWidget(prev)
        self._gen: GeneratedFirmware | None = None
        self._path = None
        self._thread: QThread | None = None

    def _generate(self) -> Result:
        c = self.c
        try:
            gen = generate(c.repo.all(), c.calibration.calibration, c.settings, sentences=c.sentence_texts())
        except pins.WiringError as exc:
            return self.act(Result(False, f"Refused: {exc}"))
        self._gen = gen
        self._path = gen.write(c.workspace.firmware_dir)
        self.file_box.blockSignals(True)
        self.file_box.clear()
        self.file_box.addItems(list(gen.files))
        self.file_box.blockSignals(False)
        self._show_file(self.file_box.currentText())
        self.verify.setEnabled(True)
        warn = "\n".join("⚠ " + w for w in gen.warnings)
        set_text(self.result, f"Written to {self._path}\nFingerprint {gen.fingerprint}; gestures: "
                              f"{', '.join(gen.gesture_names) or 'none'}" + (f"\n{warn}" if warn else ""))
        set_color(self.result, theme.WARN if gen.warnings else theme.LIVE, 10)
        return self.act(Result(True, f"Firmware generated in {self._path}"))

    def _show_file(self, name: str) -> None:
        if self._gen and name in self._gen.files:
            self.preview.setPlainText(self._gen.files[name])

    def _compile(self) -> None:
        if self._path is None or self._thread is not None:
            return
        set_text(self.result, self.result.text() + "\nCompiling with arduino-cli (about a minute)…")
        self.verify.setEnabled(False)
        self._thread = QThread()
        self._worker = _CompileWorker(self._path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._compiled)
        self._thread.start()

    def _compiled(self, ok: bool, output: str) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self.verify.setEnabled(True)
        tail = "\n".join(output.splitlines()[-6:])
        set_text(self.result, self.result.text().split("\nCompiling")[0] +
                 ("\nCOMPILE OK ✓\n" if ok else "\nCOMPILE FAILED ✖\n") + tail)
        set_color(self.result, theme.LIVE if ok else theme.ERROR, 10)
        self.notify(Result(ok, "Firmware compiles." if ok else "Firmware compile failed - see Code Generator."))

    def refresh(self) -> None:
        c = self.c
        cal = c.calibration.calibration
        enabled = c.store.enabled_gestures
        text = "\n".join((
            f"Flex pins (Thumb, Index, Middle, Ring, Little): {{{', '.join(map(str, pins.FLEX_PINS))}}}",
            f"OLED: SDA {pins.OLED_SDA}, SCL {pins.OLED_SCL} on TwoWire port {pins.OLED_I2C_PORT} (Wire1), "
            f"address 0x3C / 0x3D",
            f"MPU6050: SDA {pins.MPU_SDA}, SCL {pins.MPU_SCL} on TwoWire port {pins.MPU_I2C_PORT} (Wire), address 0x68",
            "Audio: none on the ESP32 (laptop speaks via the Studio)",
            f"Calibration: {'yes (' + cal.captured_at + ')' if cal else 'NOT CALIBRATED - on-device recognition off'}",
            f"Enabled gestures ({len(enabled)}): " + (", ".join(f"{g.name} [{g.finger_pattern.compact()}]"
                                                              for g in enabled) or "none"),
            f"Hold {c.settings.hold_samples} samples, release {c.settings.release_samples}, "
            f"filter window {c.settings.filter_window}",
        ))
        set_text(self.summary, text)

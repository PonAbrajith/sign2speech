"""Entry point.

    python run_studio.py [--workspace DIR] [--port COM9] [--no-connect]
    python run_studio.py --generate-firmware [--compile] [--workspace DIR]

Start-up: load workspace -> load calibration -> load gesture database ->
try the preferred port (COM9 by default, then ESP32-looking ports) -> live
monitoring starts immediately. Simulation only starts when the user presses
Start Simulation.

--generate-firmware writes workspace/firmware/Sign2Speech_V2 from the locked
pins, the saved calibration and the enabled gestures, then exits (no GUI).
--compile additionally checks it with arduino-cli. Nothing is ever uploaded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sign2speech_v2.config.settings import Settings, Workspace, default_workspace


def generate_firmware(ws: Workspace, compile_it: bool = False) -> int:
    from sign2speech_v2.calibration.model import CalibrationStore
    from sign2speech_v2.codegen.arduino_cli import compile_sketch
    from sign2speech_v2.codegen.generator import generate
    from sign2speech_v2.config.demo import ensure_all_demo_data
    from sign2speech_v2.gestures.repository import GestureRepository
    from sign2speech_v2.speech.sentences import SentenceRepository

    # messages may contain emoji (sentence icons); a Windows cp1252 console must not crash the command
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ws.ensure()
    settings = Settings.load(ws.settings_path)
    repo = GestureRepository(ws.db_path)
    sentence_repo = SentenceRepository(ws.db_path)
    cal_store = CalibrationStore(ws.calibration_path)
    try:
        if settings.demo_defaults:          # same one-time seeding as the Studio start-up
            for msg in ensure_all_demo_data(repo, sentence_repo, cal_store, settings.bend_threshold,
                                            settings.hysteresis):
                print(msg)
        by_id = {s.id: s.sentence for s in sentence_repo.all()}
        gestures = repo.all()
        texts = {g.name: by_id[g.sentence_id] for g in gestures if g.sentence_id in by_id}
        fw = generate(gestures, cal_store.load(), settings, sentences=texts)
    finally:
        repo.close()
        sentence_repo.close()
    out = fw.write(ws.firmware_dir)
    print(f"Firmware written: {out}")
    print(f"Fingerprint: {fw.fingerprint}")
    print(f"Calibrated: {'yes' if fw.calibrated else 'NO (on-device recognition disabled)'}")
    print(f"Enabled gestures: {', '.join(fw.gesture_names) or 'none'}")
    for w in fw.warnings:
        print(f"WARNING: {w}")
    if compile_it:
        r = compile_sketch(out)
        print("\n".join(r.output.splitlines()[-3:]))
        print("COMPILE OK" if r.ok else "COMPILE FAILED")
        if not r.ok:
            return 1
    print("Not uploaded. Flash it yourself (see README: 'Flash the firmware').")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sign2Speech V2 Studio")
    parser.add_argument("--workspace", type=Path, help="workspace folder (default: .\\workspace)")
    parser.add_argument("--port", help="serial port to try first (default from settings: COM9)")
    parser.add_argument("--no-connect", action="store_true", help="do not auto-connect at start-up")
    parser.add_argument("--generate-firmware", action="store_true",
                        help="write workspace/firmware/Sign2Speech_V2 and exit (never uploads)")
    parser.add_argument("--compile", action="store_true", help="with --generate-firmware: verify with arduino-cli")
    args = parser.parse_args(argv)
    ws = Workspace(args.workspace) if args.workspace else default_workspace()

    if args.generate_firmware:
        return generate_firmware(ws, args.compile)

    from PySide6.QtWidgets import QApplication

    from sign2speech_v2.controller import StudioController
    from sign2speech_v2.ui.main_window import MainWindow
    from sign2speech_v2.ui.theme import STYLE

    settings = Settings.load(ws.settings_path)
    if args.port:
        settings.preferred_port = args.port
    if args.no_connect:
        settings.auto_connect = False

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    controller = StudioController(ws, settings)
    window = MainWindow(controller)
    window.show()
    result = controller.startup()
    window.notify(result)
    # prove which code/workspace/port this process really uses (also on Hardware Diagnostics)
    for key, value in controller.runtime_info().items():
        print(f"[sign2speech-v2] {key}: {value}", flush=True)
    print(f"[sign2speech-v2] Start-up: {result.message}", flush=True)
    window.refresh_ports()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

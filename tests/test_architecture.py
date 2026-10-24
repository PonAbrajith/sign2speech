"""State separation rules, checked on the source code itself."""

import ast
import dataclasses
import inspect
from pathlib import Path

import sign2speech_v2.controller as controller_mod
from sign2speech_v2.state import states

SRC = Path(controller_mod.__file__).resolve().parent
STATE_CLASSES = (states.LiveSensorState, states.CalibrationState, states.GestureEditorState,
                 states.GestureStoreState, states.LiveDetectionState, states.SpeechState, states.HardwareState)


def test_seven_separate_state_objects_never_reference_each_other():
    names = {c.__name__ for c in STATE_CLASSES}
    for cls in STATE_CLASSES:
        for f in dataclasses.fields(cls):
            assert not any(n in str(f.type) for n in names - {cls.__name__}), (cls.__name__, f.name)


def _method_source_names(func) -> set[str]:
    tree = ast.parse(inspect.getsource(func).lstrip().replace("\n    ", "\n"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            out.add(node.attr)
    return out


def test_live_pipeline_never_reads_editor_or_selection():
    used = _method_source_names(controller_mod.StudioController._process_frame)
    assert "editor" in used            # only to append recorded samples (checked below)
    assert "store" not in used
    src = inspect.getsource(controller_mod.StudioController._process_frame)
    editor_uses = {line.strip() for line in src.splitlines() if "self.editor" in line}
    assert editor_uses == {"if self.editor.recording:", "self.editor.samples_recorded = len(self._recorded)"}
    assert "selected_id" not in src


def test_live_pages_do_not_read_editor_or_selection():
    for page in ("live_glove.py", "dashboard.py"):
        text = (SRC / "ui" / "pages" / page).read_text(encoding="utf-8")
        assert ".editor" not in text and "selected_id" not in text, page


def test_capture_is_the_only_live_to_editor_path():
    src = inspect.getsource(controller_mod.StudioController)
    assignments = [line.strip() for line in src.splitlines() if "self.editor.pattern =" in line]
    assert assignments == ["self.editor.pattern = GesturePattern.from_live(live)",
                           "self.editor.pattern = GesturePattern(tuple(tokens))"]


def test_one_serial_connection_manager():
    hits = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "serial.Serial(" in text or "AcquisitionManager(" in text:
            hits.append(py.relative_to(SRC).as_posix())
    assert sorted(hits) == ["acquisition/transport.py", "controller.py"]


def test_no_firmware_upload_code():
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "--upload" not in text and "esptool" not in text.lower(), py.name

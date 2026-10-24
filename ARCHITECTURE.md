# Sign2Speech V2 — Architecture

## Data flow

```
ESP32 ──USB serial──► AcquisitionManager (ONE connection, polled) ──► subscribers
                         │  REAL ESP32  xor  SIMULATION (explicit only)
                         ▼
StudioController._process_frame
   raw ─► FlexFilter ─► filtered ─► Calibration ─► bending %, OPEN/BENT (hysteresis) ─► LivePattern (O/B only)
                                                                                         │
                               enabled stored gestures ─► Recognizer (FingerPatternRecognizer | MLRecognizer)
                                                                                         │
                                            MATCH / UNKNOWN / AMBIGUOUS ─► StabilityGate (hold 10, release 5)
                                                                                         │ ACCEPTED once
                                                        SpeechService ─► laptop speaker   └► "SHOW NAME" ─► ESP32 OLED
```

Gesture Builder → `capture_from_live()` copies the *current* `LivePattern` into the editor as a new
`GesturePattern` (a snapshot). That is the only path from live data to the editor, and nothing flows back.

## Packages (`src/sign2speech_v2`)

| Package | Role |
|---|---|
| `config/pins.py` | the only pin map + `verify_wiring()` |
| `config/settings.py` | tunables + workspace paths |
| `core/` | `SensorFrame` + line protocol, status-line parser, `LivePattern` / `GesturePattern` |
| `acquisition/` | decoder, transports (pyserial / fake), `AcquisitionManager`, simulator |
| `processing/` | moving-average filter |
| `calibration/` | capture analysis (range, noise, rail checks), `Calibration`, classifier |
| `recognition/` | `Recognizer` interface, `FingerPatternRecognizer`, `MLRecognizer` stub, `StabilityGate` |
| `gestures/` | SQLite repository, sample CSV store |
| `speech/` | `SpeechEngine` (pyttsx3 / Windows SAPI / Null), `SpeechService` (speak once) |
| `diagnostics/` | RAW ADC classification, flex health, MPU motion |
| `codegen/` | firmware generator, templates, compile-only arduino-cli wrapper |
| `state/states.py` | the seven state objects |
| `controller.py` | owns everything above; Qt-free, `tick()` driven |
| `ui/` | PySide6 window + 8 pages (read state, call controller actions) |

## The seven states

| State | Written by | Never |
|---|---|---|
| `LiveSensorState` | live pipeline | holds stored/editor data; cleared when the stream stops |
| `CalibrationState` | calibration actions | touched by gesture delete |
| `GestureEditorState` | builder actions | read by the live pipeline |
| `GestureStoreState` | repository reload / library selection | selection read by the live pipeline |
| `LiveDetectionState` | live pipeline | shows X in `live_pattern` (type allows only O/B) |
| `SpeechState` | `SpeechService` | spoken per frame (only per acceptance) |
| `HardwareState` | status lines from the ESP32 | invents OK without a report |

Enforced by `tests/test_architecture.py` (source inspection), `tests/test_live_pipeline.py` and
`tests/test_ui.py`.

## Firmware safety

`hardware_lock.h` is copied verbatim (never generated) and `static_assert`s GPIO 32/35/34/39/36, OLED 18/19,
MPU 22/21 and separate TwoWire ports. The Python generator additionally refuses to run when
`config/pins.py` differs from the locked values. Tested by compiling a deliberately mis-pinned sketch.

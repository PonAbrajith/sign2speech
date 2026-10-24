# Sign2Speech V2 Studio

Wearable Indian Sign Language glove prototype: an **ESP32** with **five flex sensors** and an **MPU6050**
reads the current hand, a stored gesture is recognized, the result is shown on the glove's **OLED**, and the
**laptop's built-in speaker** says it (local, offline Windows speech — no cloud, no external audio hardware).

Built from scratch. `F:\sign2speech` (V1) is a read-only reference and is never modified.

## Hardware (locked — see `src/sign2speech_v2/config/pins.py` and `codegen/templates/hardware_lock.h`)

| Finger | ESP32 pin | GPIO | Stream column |
|--------|-----------|------|---------------|
| Thumb  | D32 | 32 | Flex1 |
| Index  | D35 | 35 | Flex2 |
| Middle | D34 | 34 | Flex3 |
| Ring   | VN  | 39 | Flex4 |
| Little | VP  | 36 | Flex5 |

Each flex sensor is its own voltage divider. Finger order is always Thumb, Index, Middle, Ring, Little.

| Device | Bus | SDA | SCL | Address |
|--------|-----|-----|-----|---------|
| OLED SSD1306 | `Wire1` (TwoWire port 1) | D18 | D19 | 0x3C or 0x3D (both scanned) |
| MPU6050 | `Wire` (TwoWire port 0) | D22 | D21 | 0x68 (WHO_AM_I 0x70 accepted when data registers read) |

Audio: **none on the ESP32.** ESP32 → USB serial → Studio → laptop speaker.

## Install (Windows, PowerShell)

```powershell
cd F:\sign2speech_v2
py -3.13 -m venv .venv                      # already present
.venv\Scripts\python.exe -m pip install -r requirements.txt
# optional local Python speech engine (otherwise Windows System.Speech is used):
# .venv\Scripts\python.exe -m pip install pyttsx3
```

## Launch

```powershell
cd F:\sign2speech_v2
.venv\Scripts\python.exe run_studio.py                 # tries COM9, then any ESP32-looking port
.venv\Scripts\python.exe run_studio.py --port COM5     # another port first
.venv\Scripts\python.exe run_studio.py --no-connect    # start without connecting
.venv\Scripts\python.exe run_studio.py --workspace D:\other_workspace
```

At start-up the Studio loads the workspace, calibration and gesture database, tries the preferred port
(COM9 by default; any port can be chosen in the top bar), and shows live values as soon as data arrives.
If no glove is found it shows **WAITING FOR GLOVE** and keeps retrying every 3 s. It **never** switches to
simulation by itself — simulation runs only after **Start Simulation**, and only while no real ESP32 is
connected. The top bar always shows **REAL ESP32** or **SIMULATION**.

## First use (the acceptance workflow)

1. **Generate and flash the V2 firmware once** (see below). The Studio never flashes anything.
2. Plug in the ESP32 → open the Studio → **Live Glove**: raw ADC, filtered value, bending % and OPEN/BENT per
   finger update live. Bend each finger and watch its row change.
3. **Calibration** → CAPTURE OPEN (hand relaxed, 2 s) → CAPTURE CLOSED (bend all fingers and hold, 3 s) →
   SAVE CALIBRATION. Stored in `workspace\calibration.json`, separate from gestures.
4. **Gesture Builder** → **+ NEW GESTURE** → make the sign → watch **LIVE PATTERN** → **CAPTURE FROM LIVE
   GLOVE** → name (e.g. `HELLO`), display text (`Hello`), ISL description → **SAVE GESTURE**.
   Pattern letters: `O` open, `B` bent, `X` either (set per finger in the editor).
5. Make the sign again: LIVE PATTERN matches → LIVE DETECTION = `HELLO` after ~0.2 s (10 samples) →
   the ESP32 OLED shows `Gesture: HELLO` (otherwise `Gesture: Waiting...`) → the laptop says "Hello" **once**. Holding the sign does not
   repeat; leave the sign and perform it again to repeat.
6. Change the hand shape → LIVE DETECTION becomes `UNKNOWN` (or another gesture, or `AMBIGUOUS` if two
   stored patterns match — the Studio never guesses).

The gesture chosen in the editor / library is only the editing target. It never drives LIVE DETECTION.

## Demo mode (preconfigured, no capture needed)

On first start (or first `--generate-firmware`) the workspace gets **10 prototype gestures**, each mapped to a
sentence in the speech library (`gestures.sentence_id` → `speech_sentences`). Accepted gesture → its sentence is
spoken once on the laptop in the gesture's default style, and the OLED shows **only the word-wrapped sentence**
(no gesture name, no labels).

| Gesture | Pattern (T I M R L) | Sentence | Emotion / meaning | Default style |
|---|---|---|---|---|
| WATER | `O O O X O` | Please give me some water. | Need / request | Urgent |
| HUNGRY | `B B B X B` | I am feeling hungry. | Hunger / discomfort | Sad |
| HAPPY | `O B B X B` | I am very happy to see you. | Happiness | Happy |
| SAD | `O B O X O` | I am feeling sad today. | Sadness | Sad |
| ANGRY | `B O B X O` | Please don't touch my things. | Anger | Angry |
| SCARED | `B B O X O` | I am scared, please help me. | Fear / emergency | Urgent |
| TIRED | `O O O X B` | I am feeling tired. | Tiredness / discomfort | Sad |
| LEAVE_ME_ALONE | `O O B X B` | Please leave me alone. | Need for personal space | Angry ("Firm" is not a style) |
| NOT_WELL | `B O O X B` | I am not feeling well. | Illness / discomfort | Calm |
| THANK_YOU | `O B B X O` | Thank you for helping me. | Gratitude | Happy |

These are **prototype trigger patterns chosen for this glove, not official ISL handshapes**.
**Ring is always X (ANY)**: its measured swing (~0-10 ADC) is too small to tell gestures apart. Thumb, Index,
Middle and Little form 10 different O/B combinations (of 16), so no two gestures can match the same hand. Ten
4-finger codes cannot all differ in ≥ 2 fingers (at most 8 can); this set has the minimum number of
one-finger-apart pairs (9) and avoids the middle-finger-only shape. Data model: live finger pattern → **gesture** (the only recognition key) → its mapped **sentence**
(`gestures.sentence_id`) → that sentence's emotion / meaning (metadata of the sentence, shown in the libraries) and
the mapping's configured default style (`gestures.speech_style`). Emotion is never stored on a gesture, never
matched, never detected and never selects the style; OLED and speech output only the sentence.

Each is installed once (per-gesture marker in the `meta` table): never duplicated, never re-created after you
delete it, and a user gesture with the same name is never replaced. Built-in rows from the previous release
(exact 5-finger patterns) are moved to the Ring-X pattern once, in place (same id and sentence) - but only if
their pattern was never edited. The first demo release's HELLO / BYE are **disabled** (not deleted) because the
same hand shapes are now WATER / HUNGRY. Any two enabled gestures that could match the same hand are reported as
AMBIGUOUS in the Gesture Library, the event log and by the firmware generator - the Studio never guesses.

**PRELOADED / NEEDS REAL CALIBRATION** (old values from an earlier measurement, raw ADC open / closed):
Thumb 181/1090 · Index 37/1183 · Middle 44/1132 · Ring 6/201 · Little 19/807.
Written to `workspace\calibration.json` only if no calibration exists, and shown in amber as *not verified*.
The glove currently reads roughly Thumb 0-500, Index/Middle/Little 0-200, Ring 0-10, so with these values no
finger ever reaches BENT - **capture a real calibration before the demo** (Calibration page: CAPTURE OPEN →
CAPTURE CLOSED → SAVE CALIBRATION, then regenerate + flash the firmware). Seeding can be switched off with
`"demo_defaults": false` in `workspace\settings.json`.

**How calibration works.** Only the measured OPEN and CLOSED values are stored. Bending % = position between
that finger's own open (0 %) and closed (100 %) reading, clamped; BENT above 58 %, OPEN below 42 % (threshold
0.5 ± hysteresis 0.08, deadband between) - so a finger that runs 0-190 switches at ~110 / ~80 ADC and one that
runs 0-480 at ~280 / ~200. There is no absolute ADC threshold. Each finger gets a QUALITY:

| Quality | When | Effect |
|---|---|---|
| GOOD | range ≥ `calibration_min_range` (settings, default 20 counts) and ≥ 5× its noise | - |
| WEAK SENSOR | clean, but range below the minimum | blocks saving, **except Ring** (warning `WEAK SENSOR — RING RANGE IS VERY SMALL`) |
| DEAD / INVALID | stuck at 0 or 4095 while open AND bent, range lost in noise, or bend not held | blocks saving, **except Ring** (warning) |

A finger reading 0 while straight is normal on this glove; it is only *stuck* if it stays on the rail when bent.
Ring is never used to tell the built-in gestures apart (all use Ring = X); its weakness is still shown on the
Calibration page, in the capture messages and in the event log.

Recognition still uses only the real ESP32 stream: raw ADC → filter → calibration → OPEN/BENT → live pattern →
match against the enabled gestures → 10-frame hold → OLED + laptop speech once. No data → `WAITING FOR DATA`;
real data matching none → `UNKNOWN` (OLED `Unknown`).

**Startup arming.** Whenever recognition (re)starts - Studio start, the glove (re)connecting, a new
calibration or a changed gesture set - automatic gesture speech is **NOT ARMED**. A gesture the hand is already
in is still shown (LIVE DETECTION, OLED sentence) but not spoken. Speech arms once the hand has matched no
gesture for `arm_neutral_samples` frames (settings, default 25 ≈ 0.5 s); Live Glove and the Dashboard show
ARMED / NOT ARMED. The Speech page's SPEAK is never blocked. Recognition itself is unchanged.

Demo steps: close the Studio → flash (below) → start the Studio → move to a hand shape that matches no gesture
(e.g. only the thumb curled) until SPEECH shows ARMED → open hand = WATER, fist = HUNGRY, ...

## Generate firmware

```powershell
cd F:\sign2speech_v2
.venv\Scripts\python.exe run_studio.py --generate-firmware            # write the sketch, no GUI
.venv\Scripts\python.exe run_studio.py --generate-firmware --compile  # + verify with arduino-cli
```

or **Code Generator** page → **GENERATE ESP32 FIRMWARE**. Both write
`workspace\firmware\Sign2Speech_V2\` (`Sign2Speech_V2.ino`, `config.h`, `gestures.h`, `hardware_lock.h`)
from the locked pins, the saved calibration and the **enabled** gestures. The Studio never uploads.

## Flash the firmware (you do this; the Studio never flashes)

**Close the Studio first** (or press DISCONNECT) — only one program can use COM9, and an upload while the
Studio holds the port fails with "Access is denied / port busy".

Arduino IDE: open `workspace\firmware\Sign2Speech_V2\Sign2Speech_V2.ino`, board **ESP32 Dev Module**,
port **COM9**, libraries *Adafruit SSD1306* + *Adafruit GFX*, **Upload**.

arduino-cli (same result):

```powershell
& "C:\Program Files\Arduino PLC IDE Tools\arduino-cli.exe" compile --upload --fqbn esp32:esp32:esp32 --port COM9 F:\sign2speech_v2\workspace\firmware\Sign2Speech_V2
```

The upload log should end with `Hard resetting via RTS pin...`. Then start the Studio: **Hardware
Diagnostics → REAL HARDWARE TEST** must show `Firmware: EXPECTED V2`, `Serial: RECEIVING` and rising
`Valid frames`. Other results: `V2 DIFFERENT BUILD` (flashed from an older generation — regenerate and
flash), `OLD FIRMWARE` (V1), `UNKNOWN` (another sketch, e.g. an OLED test), `NO RESPONSE` (press
**Reset board (EN)** to see what the board prints).

Regenerate after calibrating or changing gestures so the ESP32's own (on-device) recognition matches.
While the Studio is connected, the OLED follows the Studio's live result (`HOST` / `SHOW <NAME>` /
`CLEAR` commands), so newly created gestures appear on the OLED even before you re-flash.

Firmware serial output: `# ESP32: OK`, `# PINS: ...`, `# FLEX: OK`, `# MPU6050: OK addr=0x68 WHO_AM_I=0x70`,
`# OLED: OK addr=0x3C`, `# GESTURES: n`, `# RECOGNITION: FINGER_PATTERN`, 50 Hz data lines
`Flex1..Flex5,AccelX,AccelY,AccelZ,GyroX,GyroY,GyroZ`, and `# GESTURE: HELLO` once per accepted gesture.
Commands: `STATUS`, `I2C`, `ADC`, `PING`, `HOST`, `SHOW <NAME>`, `CLEAR`.

## Pages

Dashboard · **Live Glove** · Calibration · Gesture Builder · Gesture Library · Speech ·
Hardware Diagnostics (ESP32 status, serial status, RAW ADC TEST with min/max/avg/step/mV/driver and
ZERO / STABLE NONZERO / CHANGING / NOISY, flex health, MPU6050 motion test, OLED/MPU I2C scan, serial
monitor) · Code Generator.

Speech styles (Neutral, Happy, Sad, Angry, Calm, Urgent, Custom) are rate/volume presets **you choose**
per gesture. The system does not detect emotion.

Recognition is deterministic finger-pattern matching (`FingerPatternRecognizer`), not machine learning.
`MLRecognizer` is the adapter slot for a trained model later; optional labelled samples can be recorded
to `workspace\dataset\samples\<LABEL>.csv` for that purpose.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q                         # everything (~1 min incl. 2 ESP32 compiles)
.venv\Scripts\python.exe -m pytest -q -m "not firmware_compile"  # fast (~5 s)
```

Tests use temporary workspaces only; a session guard fails the run if `workspace\studio.db`,
`calibration.json` or `settings.json` change. The suite includes running the **generated firmware on the
PC** (MinGW g++ with stubbed Arduino/Wire/SSD1306 in `tests\host\`) and compiling it for the ESP32 with
arduino-cli. Real hardware still has to be verified on the glove (see *Hardware Diagnostics*).

## Workspace

```
workspace\
  studio.db              gesture definitions (SQLite table `gestures`)
  calibration.json       per-finger open / closed / noise (+ threshold, hysteresis)
  settings.json          port, timeouts, hold/release samples, speech options (created when changed)
  dataset\samples\       optional recorded samples  <LABEL>.csv
  firmware\              generated Arduino sketch
```

See `ARCHITECTURE.md` for the state design.

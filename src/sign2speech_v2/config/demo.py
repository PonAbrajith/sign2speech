"""Preconfigured demo defaults: two BUILT-IN DEMO gestures and the PRELOADED CALIBRATION.

They are written ONCE into the normal stores (the `gestures` table and calibration.json), so the
rest of the system treats them exactly like user data: the live pipeline still takes every value
from the real ESP32 stream, and recognition still compares the CURRENT live pattern with the
stored patterns. Nothing here produces sensor values.

"Once" is recorded in the database (meta table), so a user who deletes or edits a demo gesture,
or recalibrates, never has it put back or overwritten. Gestures a user created with the same
name are never touched.

HELLO = all five fingers open, BYE = all five bent. These are DEMO mappings chosen for the
prototype demonstration - not official ISL handshapes.
"""

from __future__ import annotations

import dataclasses

from sign2speech_v2.calibration.model import PRELOADED_LABEL, Calibration, CalibrationStore, FingerCalibration
from sign2speech_v2.config.pins import FINGER_NAMES
from sign2speech_v2.core.patterns import GesturePattern
from sign2speech_v2.gestures.repository import Gesture, GestureRepository

# Previously measured on this physical glove (raw ADC): finger -> (open, closed).
PRELOADED_VALUES: dict[str, tuple[int, int]] = {
    "Thumb": (181, 1090),    # GPIO32
    "Index": (37, 1183),     # GPIO35
    "Middle": (44, 1132),    # GPIO34
    "Ring": (6, 201),        # GPIO39 (VN)
    "Little": (19, 807),     # GPIO36 (VP)
}


META_GESTURES = "demo_gestures_seeded"
META_SPEECH = "speech_demo_seeded"

# BUILT-IN SPEECH DEMO: one sentence, six selectable expressive speech styles (voice presets,
# not emotion detection). Stored in speech_sentences - never in the gestures table.
SPEECH_DEMO_NAME = "HELP_REQUEST"
SPEECH_DEMO_SENTENCE = "I need your help right now."
SPEECH_DEMO_STYLES = ("Neutral", "Happy", "Sad", "Angry", "Calm", "Urgent")
META_CALIBRATION = "preloaded_calibration_seeded"
META_SENTENCE_LIBRARY = "sentence_library_seeded"
META_RING_X = "builtin_gesture_ring_x"     # per gesture: the Ring-X pattern release is applied
META_NO_EMOTION = "builtin_gesture_no_emotion_category"   # per gesture: category moved off "Emotion"

# BUILT-IN SENTENCES: (name, sentence, emotion, icon, meaning). Emotion/meaning is metadata that
# describes the sentence - it is never detected from the user and never selects the voice style.
BUILTIN_SENTENCES: tuple[tuple[str, str, str, str, str], ...] = (
    ("WATER_REQUEST", "Please give me some water.", "Need / request", "\U0001F4A7", "Need / request"),
    ("HUNGRY", "I am feeling hungry.", "Hunger / discomfort", "\U0001F37D\uFE0F", "Hunger / discomfort"),
    ("HAPPY_TO_SEE_YOU", "I am very happy to see you.", "Happiness", "\U0001F60A", "Happiness"),
    ("SAD_TODAY", "I am feeling sad today.", "Sadness", "\U0001F622", "Sadness"),
    ("DONT_TOUCH", "Please don't touch my things.", "Anger", "\U0001F620", "Anger"),
    ("SCARED_HELP", "I am scared, please help me.", "Fear / emergency", "\U0001F628", "Fear / emergency"),
    ("TIRED", "I am feeling tired.", "Tiredness / discomfort", "\U0001F634", "Tiredness / discomfort"),
    ("LEAVE_ME_ALONE", "Please leave me alone.", "Need for personal space", "\U0001F6D1",
     "Need for personal space"),
    ("NOT_WELL", "I am not feeling well.", "Illness / discomfort", "\U0001F912", "Illness / discomfort"),
    ("THANK_YOU", "Thank you for helping me.", "Gratitude", "\U0001F64F", "Gratitude"),
)
# the first library release (seeded behind the single META_SENTENCE_LIBRARY flag)
FIRST_LIBRARY_NAMES = ("WATER_REQUEST", "HUNGRY", "HAPPY_TO_SEE_YOU", "SAD_TODAY", "DONT_TOUCH", "SCARED_HELP")

# UI suggestion only (the user picks the style; nothing is changed automatically).
SUGGESTED_STYLE: dict[str, str] = {
    "Need / request": "Urgent",
    "Hunger / discomfort": "Calm",
    "Happiness": "Happy",
    "Sadness": "Sad",
    "Anger": "Angry",
    "Fear / emergency": "Urgent",
    "Tiredness / discomfort": "Calm",
    "Need for personal space": "Calm",
    "Illness / discomfort": "Sad",
    "Gratitude": "Happy",
}


def suggested_style(emotion: str) -> str | None:
    return SUGGESTED_STYLE.get(emotion.strip())


def preloaded_calibration(bend_threshold: float = 0.5, hysteresis: float = 0.08) -> Calibration:
    # noise was not recorded with these values; 0 = unknown (it is only used by the capture checks)
    fingers = tuple(FingerCalibration(n, *PRELOADED_VALUES[n], noise=0.0) for n in FINGER_NAMES)
    return Calibration(fingers, bend_threshold, hysteresis, captured_at="", label=PRELOADED_LABEL)


def ensure_demo_defaults(repo: GestureRepository, cal_store: CalibrationStore,
                         bend_threshold: float = 0.5, hysteresis: float = 0.08) -> list[str]:
    """Seed the preloaded calibration if never seeded before (gestures: ensure_prototype_gestures)."""
    done = []
    # The first demo release installed HELLO (O O O O O) / BYE (B B B B B) under META_GESTURES. New
    # workspaces get the 10 prototype gestures instead (ensure_prototype_gestures), where the open hand is
    # WATER and the fist is HUNGRY; existing HELLO / BYE rows are left in place (disabled if they clash).
    if repo.meta(META_GESTURES) is None:
        repo.set_meta(META_GESTURES, "1")
    if repo.meta(META_CALIBRATION) is None:
        if cal_store.load() is None:                   # never replace a calibration that already exists
            cal_store.save(preloaded_calibration(bend_threshold, hysteresis))
            done.append(f"Loaded {PRELOADED_LABEL}: old values from an earlier measurement - capture OPEN/CLOSED on the "
                        "Calibration page")
        repo.set_meta(META_CALIBRATION, "1")
    return done


def ensure_speech_demo(sentence_repo, meta_repo: GestureRepository) -> list[str]:
    """Add the demo sentence once per style (never duplicates, never re-adds after the user deletes one)."""
    from sign2speech_v2.speech.sentences import Sentence

    if meta_repo.meta(META_SPEECH) is not None:
        return []
    done = []
    for style in SPEECH_DEMO_STYLES:
        if sentence_repo.find(SPEECH_DEMO_NAME, SPEECH_DEMO_SENTENCE, style) is None:
            sentence_repo.add(Sentence(SPEECH_DEMO_NAME, SPEECH_DEMO_SENTENCE, style, builtin_demo=True))
            done.append(f'Added BUILT-IN SPEECH DEMO "{SPEECH_DEMO_SENTENCE}" ({style})')
    meta_repo.set_meta(META_SPEECH, "1")
    return done


def ensure_sentence_library(sentence_repo, meta_repo: GestureRepository) -> list[str]:
    """Add each built-in sentence (Neutral style) once.

    Tracked per record, so sentences added to the library later still reach existing workspaces,
    while a built-in sentence the user deleted is never put back and nothing is duplicated.
    """
    from sign2speech_v2.speech.sentences import Sentence

    legacy = meta_repo.meta(META_SENTENCE_LIBRARY) is not None   # first release already seeded
    done = []
    for name, text, emotion, icon, meaning in BUILTIN_SENTENCES:
        key = f"builtin_sentence:{name}"
        if meta_repo.meta(key) is not None:
            continue
        if not (legacy and name in FIRST_LIBRARY_NAMES) and sentence_repo.find(name, text, "Neutral") is None:
            sentence_repo.add(Sentence(name, text, "Neutral", builtin_demo=True, emotion=emotion, icon=icon,
                                       meaning=meaning))
            done.append(f'Added BUILT-IN SENTENCE {icon} "{text}" (emotion: {emotion})')
        meta_repo.set_meta(key, "1")
    meta_repo.set_meta(META_SENTENCE_LIBRARY, "1")
    return done


# ============================================================== 10 prototype gestures
# PROTOTYPE / DEMO trigger patterns chosen for this glove - NOT official ISL handshapes.
# Finger order Thumb, Index, Middle, Ring, Little.
#
# Ring is always X (ANY): its measured swing is only ~0-10 ADC, too small/noisy to tell gestures apart.
# The other four fingers carry the information: 10 of the 16 Thumb/Index/Middle/Little O/B combinations,
# all different, so no two gestures can match the same hand (never AMBIGUOUS). Ten codes in 4 bits
# cannot all be >= 2 fingers apart (at most 8 can), so the set has the fewest one-finger-apart pairs:
# 9, the minimum once the middle-finger-only shape (? B O ? B) is excluded.
#
# DATA MODEL - the finger pattern belongs to the GESTURE, and the gesture's output is its SENTENCE:
#   live finger pattern -> gesture (recognition key) -> mapped sentence (speech_sentences.id)
#                       -> the sentence's emotion / meaning (METADATA of the sentence, shown in the libraries)
#                       -> the mapping's configured default speech style (gestures.speech_style)
# No emotion is stored with, keyed by or derived from a pattern or gesture: the style below is configured
# per gesture -> sentence mapping, and emotion never selects it. ("Firm" is not a style: LEAVE_ME_ALONE = Angry.)
PROTOTYPE_GESTURES: tuple[tuple[str, str, str, str, str], ...] = (
    # gesture,         pattern (T I M R L), display,     mapped sentence,    default style
    ("WATER",          "O O O X O", "Water",          "WATER_REQUEST",    "Urgent"),   # open hand
    ("HUNGRY",         "B B B X B", "Hungry",         "HUNGRY",           "Sad"),      # fist
    ("HAPPY",          "O B B X B", "Happy",          "HAPPY_TO_SEE_YOU", "Happy"),    # thumb out, fingers curled
    ("SAD",            "O B O X O", "Sad",            "SAD_TODAY",        "Sad"),      # only the index curled
    ("ANGRY",          "B O B X O", "Angry",          "DONT_TOUCH",       "Angry"),    # index + little up, thumb in
    ("SCARED",         "B B O X O", "Scared",         "SCARED_HELP",      "Urgent"),   # thumb + index curled
    ("TIRED",          "O O O X B", "Tired",          "TIRED",            "Sad"),      # little curled
    ("LEAVE_ME_ALONE", "O O B X B", "Leave me alone", "LEAVE_ME_ALONE",   "Angry"),    # thumb + index out
    ("NOT_WELL",       "B O O X B", "Not well",       "NOT_WELL",         "Calm"),     # index + middle up, thumb in
    ("THANK_YOU",      "O B B X O", "Thank you",      "THANK_YOU",        "Happy"),    # thumb + little out
)
PROTOTYPE_CATEGORY = "General"            # earlier releases stored "Emotion" here (see _separate_emotion)
# Patterns installed by the previous release (exact O/B, Ring used). A built-in row that still has its
# old pattern is moved to the new one once; a pattern the user edited is never touched.
PREVIOUS_PROTOTYPE_PATTERNS: dict[str, str] = {
    "WATER": "OOOOO", "HUNGRY": "BBBBB", "HAPPY": "OOOBB", "SAD": "OBBBO", "ANGRY": "BBBOO",
    "SCARED": "BOBOO", "TIRED": "BOOOB", "LEAVE_ME_ALONE": "OBOBB", "NOT_WELL": "BBOOB", "THANK_YOU": "OOBBO",
}
PRIMARY_FINGERS = (0, 1, 2, 4)            # Thumb, Index, Middle, Little (Ring excluded)
PROTOTYPE_DESCRIPTION = ("PROTOTYPE DEMO trigger pattern chosen for this glove - not an official Indian Sign "
                         "Language handshape. Ring is ANY (X): its sensor range is too small to rely on.")


def _library_entry(sentence_name: str) -> tuple[str, str, str, str, str]:
    return next(e for e in BUILTIN_SENTENCES if e[0] == sentence_name)


def primary_code(pattern: GesturePattern) -> str:
    """The four reliable fingers (Thumb, Index, Middle, Little) of a pattern, e.g. 'OOOO'."""
    return "".join(pattern.tokens[i].value for i in PRIMARY_FINGERS)


def ensure_prototype_gestures(repo: GestureRepository, sentence_repo) -> list[str]:
    """Install each prototype gesture once (per-record marker), mapped to its sentence.

    Never overwrites a user's gesture with the same name, never re-creates one the user deleted.
    Built-in rows from earlier releases are upgraded once, in place (_upgrade_previous_patterns,
    _separate_emotion). Older BUILT-IN demo gestures (HELLO / BYE) whose pattern would now be AMBIGUOUS
    with a prototype gesture are DISABLED once (not deleted); re-enabling them later is respected.
    """
    from sign2speech_v2.speech.sentences import Sentence

    done = []
    for name, pattern, display, sentence_name, style in PROTOTYPE_GESTURES:
        key = f"builtin_gesture:{name}"
        if repo.meta(key) is not None:
            continue
        if repo.by_name(name) is not None:
            done.append(f"Kept your existing gesture {name} (prototype {name} not installed)")
        else:
            _, text, emotion, icon, meaning = _library_entry(sentence_name)
            sentence = sentence_repo.find(sentence_name, text, "Neutral") or next(
                (s for s in sentence_repo.all() if s.sentence == text), None)
            if sentence is None:                 # the gesture needs its sentence: add the library record
                sentence = sentence_repo.add(Sentence(sentence_name, text, "Neutral", builtin_demo=True,
                                                      emotion=emotion, icon=icon, meaning=meaning))
            repo.save(Gesture(name=name, display_text=display, finger_pattern=GesturePattern.parse(pattern),
                              isl_description=PROTOTYPE_DESCRIPTION, category=PROTOTYPE_CATEGORY,
                              speech_style=style, builtin_demo=True, sentence_id=sentence.id))
            done.append(f'Added PROTOTYPE gesture {name} ({pattern}) -> "{text}" [{style}]')
        repo.set_meta(key, "1")
        repo.set_meta(f"{META_RING_X}:{name}", "1")      # installed with the current pattern
        repo.set_meta(f"{META_NO_EMOTION}:{name}", "1")  # installed without an emotion category

    done += _upgrade_previous_patterns(repo)
    done += _separate_emotion(repo)

    protos = [g for g in repo.all() if g.enabled and g.name in {p[0] for p in PROTOTYPE_GESTURES}]
    for g in repo.all():
        key = f"legacy_disabled:{g.name}"
        if not g.builtin_demo or not g.enabled or g in protos or repo.meta(key) is not None:
            continue
        clash = [p.name for p in protos if p.finger_pattern.overlaps(g.finger_pattern)]
        if clash:
            repo.set_enabled(g.id, False)
            repo.set_meta(key, "1")
            done.append(f"Disabled older built-in demo gesture {g.name} ({g.finger_pattern}): the same hand shape "
                        f"is now {', '.join(clash)}")
    return done


def _upgrade_previous_patterns(repo: GestureRepository) -> list[str]:
    """Move built-in prototype rows from the previous exact-O/B patterns to the Ring-X patterns (once).

    Only a row that is still built-in AND still has exactly its previous pattern is changed - anything
    the user edited (the pattern, or their own gesture with the same name) is left alone. The row keeps
    its id, sentence link, enabled flag and display text; its style is the configured mapping style.
    """
    done = []
    for name, pattern, _, _, style in PROTOTYPE_GESTURES:
        key = f"{META_RING_X}:{name}"
        if repo.meta(key) is not None:
            continue
        g = repo.by_name(name)
        if g is not None and g.builtin_demo and g.finger_pattern.compact() == PREVIOUS_PROTOTYPE_PATTERNS[name]:
            new = GesturePattern.parse(pattern)
            repo.save(dataclasses.replace(g, finger_pattern=new, isl_description=PROTOTYPE_DESCRIPTION,
                                          speech_style=style))
            done.append(f"Updated PROTOTYPE gesture {name}: {g.finger_pattern} -> {new} (Ring = ANY)")
        repo.set_meta(key, "1")
    return done


def _separate_emotion(repo: GestureRepository) -> list[str]:
    """Built-in gestures from earlier releases were filed under category "Emotion"; emotion belongs to the
    sentence, so move them to PROTOTYPE_CATEGORY (once, built-in rows only, only if still "Emotion")."""
    done = []
    for name, *_ in PROTOTYPE_GESTURES:
        key = f"{META_NO_EMOTION}:{name}"
        if repo.meta(key) is not None:
            continue
        g = repo.by_name(name)
        if g is not None and g.builtin_demo and g.category == "Emotion":
            repo.save(dataclasses.replace(g, category=PROTOTYPE_CATEGORY))
            done.append(f"{name}: category Emotion -> {PROTOTYPE_CATEGORY} (emotion is metadata of its sentence)")
        repo.set_meta(key, "1")
    return done


def ensure_all_demo_data(repo: GestureRepository, sentence_repo, cal_store: CalibrationStore,
                         bend_threshold: float = 0.5, hysteresis: float = 0.08) -> list[str]:
    """Everything the preconfigured demo needs, in dependency order (sentences before gestures)."""
    return (ensure_demo_defaults(repo, cal_store, bend_threshold, hysteresis)
            + ensure_speech_demo(sentence_repo, repo)
            + ensure_sentence_library(sentence_repo, repo)
            + ensure_prototype_gestures(repo, sentence_repo))


def pattern_conflicts(gestures) -> list[tuple[str, str]]:
    """Pairs of ENABLED gestures whose patterns overlap (the matcher would report AMBIGUOUS)."""
    enabled = [g for g in gestures if g.enabled]
    return [(a.name, b.name) for i, a in enumerate(enabled) for b in enabled[i + 1:]
            if a.finger_pattern.overlaps(b.finger_pattern)]

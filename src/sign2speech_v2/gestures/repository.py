"""Gesture definitions in SQLite (workspace/studio.db, table `gestures`).

Only gesture definitions live here. Calibration, hardware settings and
recorded samples are stored elsewhere, so deleting a gesture can never
touch them.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from sign2speech_v2.core.patterns import GesturePattern, PatternError

SPEECH_STYLES = ("Neutral", "Happy", "Sad", "Angry", "Calm", "Urgent", "Custom")
SENTENCE_ROLES = ("Word", "Greeting", "Question", "Request", "Response", "Subject", "Verb", "Object", "End")
# no "Emotion": emotion / meaning is metadata of the mapped SENTENCE, never of a gesture or pattern
CATEGORIES = ("General", "Greeting", "Need", "Question", "Response", "Number", "Letter")
NAME_RE = re.compile(r"^[A-Z0-9_]{1,24}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS gestures (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL UNIQUE,
    display_text         TEXT    NOT NULL,
    isl_description      TEXT    NOT NULL DEFAULT '',
    category             TEXT    NOT NULL DEFAULT 'General',
    enabled              INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    finger_pattern       TEXT    NOT NULL CHECK (length(finger_pattern) = 5),
    confidence_threshold REAL    NOT NULL DEFAULT 1.0,
    speech_style         TEXT    NOT NULL DEFAULT 'Neutral',
    sentence_role        TEXT    NOT NULL DEFAULT 'Word',
    builtin_demo         INTEGER NOT NULL DEFAULT 0 CHECK (builtin_demo IN (0, 1)),
    sentence_id          INTEGER REFERENCES speech_sentences(id) ON DELETE SET NULL,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class GestureError(ValueError):
    pass


@dataclass(frozen=True)
class Gesture:
    name: str
    display_text: str
    finger_pattern: GesturePattern
    isl_description: str = ""
    category: str = "General"
    enabled: bool = True
    confidence_threshold: float = 1.0
    speech_style: str = "Neutral"
    sentence_role: str = "Word"
    builtin_demo: bool = False          # shipped prototype/demo trigger pattern, not user-captured
    # gesture -> sentence mapping (speech_sentences.id). The sentence text lives only in speech_sentences;
    # speech_style above is the mapping's DEFAULT style. None = speak display_text.
    sentence_id: int | None = None
    id: int | None = None
    created_at: str = ""
    updated_at: str = ""


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", "_", name.strip()).upper()


def validate(g: Gesture) -> None:
    if not NAME_RE.match(g.name):
        raise GestureError("Gesture name must be 1-24 characters: A-Z, 0-9 or _ (e.g. HELLO, THANK_YOU).")
    if not g.display_text.strip():
        raise GestureError("Display text (what the laptop speaks) is required.")
    if g.finger_pattern.all_any:
        raise GestureError("Pattern X X X X X matches every hand shape; capture a real pattern.")
    if g.speech_style not in SPEECH_STYLES:
        raise GestureError(f"Unknown speech style {g.speech_style!r}.")
    if not 0.0 < g.confidence_threshold <= 1.0:
        raise GestureError("Confidence threshold must be in (0, 1].")


class GestureRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        # gestures.sentence_id references speech_sentences: with foreign keys ON the parent table must
        # exist before any gesture row is written (the sentence module owns its schema)
        from sign2speech_v2.speech.sentences import SCHEMA as SENTENCE_SCHEMA

        self._conn.executescript(SENTENCE_SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(gestures)")}
        if "builtin_demo" not in cols:      # database created before the demo mode existed
            self._conn.execute("ALTER TABLE gestures ADD COLUMN builtin_demo INTEGER NOT NULL DEFAULT 0")
        if "sentence_id" not in cols:       # database created before gesture -> sentence mapping existed
            self._conn.execute("ALTER TABLE gestures ADD COLUMN sentence_id INTEGER "
                               "REFERENCES speech_sentences(id) ON DELETE SET NULL")
        self._conn.commit()

    # --- small key/value store (e.g. "demo defaults already seeded") -------
    def meta(self, key: str) -> str | None:
        r = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def close(self) -> None:
        self._conn.close()

    # --- queries -----------------------------------------------------------
    def all(self) -> list[Gesture]:
        return [self._row(r) for r in self._conn.execute("SELECT * FROM gestures ORDER BY name")]

    def enabled(self) -> list[Gesture]:
        return [g for g in self.all() if g.enabled]

    def get(self, gesture_id: int) -> Gesture | None:
        r = self._conn.execute("SELECT * FROM gestures WHERE id = ?", (gesture_id,)).fetchone()
        return self._row(r) if r else None

    def by_name(self, name: str) -> Gesture | None:
        r = self._conn.execute("SELECT * FROM gestures WHERE name = ?", (normalize_name(name),)).fetchone()
        return self._row(r) if r else None

    # --- writes ------------------------------------------------------------
    def save(self, g: Gesture) -> Gesture:
        """Insert (id None) or update (id set). Returns the stored gesture."""
        g = replace(g, name=normalize_name(g.name), display_text=g.display_text.strip())
        validate(g)
        now = datetime.now().isoformat(timespec="seconds")
        other = self.by_name(g.name)
        if other is not None and other.id != g.id:
            raise GestureError(f"A gesture named {g.name} already exists.")
        values = (g.name, g.display_text, g.isl_description, g.category, int(g.enabled),
                  g.finger_pattern.compact(), g.confidence_threshold, g.speech_style, g.sentence_role,
                  int(g.builtin_demo), g.sentence_id)
        with self._conn:
            if g.id is None:
                cur = self._conn.execute(
                    "INSERT INTO gestures (name, display_text, isl_description, category, enabled, finger_pattern,"
                    " confidence_threshold, speech_style, sentence_role, builtin_demo, sentence_id, created_at,"
                    " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values + (now, now))
                new_id = int(cur.lastrowid)
            else:
                cur = self._conn.execute(
                    "UPDATE gestures SET name=?, display_text=?, isl_description=?, category=?, enabled=?,"
                    " finger_pattern=?, confidence_threshold=?, speech_style=?, sentence_role=?, builtin_demo=?,"
                    " sentence_id=?,"
                    " updated_at=?"
                    " WHERE id=?", values + (now, g.id))
                if cur.rowcount != 1:
                    raise GestureError(f"Gesture id {g.id} no longer exists.")
                new_id = g.id
        stored = self.get(new_id)
        assert stored is not None
        return stored

    def delete(self, gesture_id: int) -> bool:
        with self._conn:
            return self._conn.execute("DELETE FROM gestures WHERE id = ?", (gesture_id,)).rowcount == 1

    def set_enabled(self, gesture_id: int, enabled: bool) -> None:
        with self._conn:
            self._conn.execute("UPDATE gestures SET enabled=?, updated_at=? WHERE id=?",
                               (int(enabled), datetime.now().isoformat(timespec="seconds"), gesture_id))

    def integrity_ok(self) -> bool:
        return self._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    @staticmethod
    def _row(r: sqlite3.Row) -> Gesture:
        try:
            pattern = GesturePattern.parse(r["finger_pattern"])
        except PatternError as exc:
            raise GestureError(f"stored pattern for {r['name']} is invalid: {exc}") from exc
        return Gesture(name=r["name"], display_text=r["display_text"], finger_pattern=pattern,
                       isl_description=r["isl_description"], category=r["category"], enabled=bool(r["enabled"]),
                       confidence_threshold=r["confidence_threshold"], speech_style=r["speech_style"],
                       sentence_role=r["sentence_role"], builtin_demo=bool(r["builtin_demo"]),
                       sentence_id=r["sentence_id"], id=r["id"], created_at=r["created_at"],
                       updated_at=r["updated_at"])

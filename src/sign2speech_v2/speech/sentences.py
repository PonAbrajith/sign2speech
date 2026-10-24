"""Speech sentence library (workspace/studio.db, table `speech_sentences`).

A sentence is a speech item - separate from gestures. Gesture HELLO stays a gesture; the sentence
"I need some water." is its own record with its own manually selected speech style. The same
sentence may be stored several times with different styles (Neutral / Happy / Sad ...); each is
an independent row, so deleting one never removes another.

Future: a gesture (e.g. WATER) can be mapped to a sentence id. That mapping is deliberately not
created here - sentences are only spoken manually from the Speech page for now.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from sign2speech_v2.gestures.repository import SPEECH_STYLES

NAME_RE = re.compile(r"^[A-Z0-9_]{1,32}$")
MAX_SENTENCE = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS speech_sentences (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    sentence   TEXT    NOT NULL CHECK (length(sentence) > 0),
    style      TEXT    NOT NULL DEFAULT 'Neutral',
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    builtin_demo INTEGER NOT NULL DEFAULT 0 CHECK (builtin_demo IN (0, 1)),
    emotion    TEXT    NOT NULL DEFAULT '',
    icon       TEXT    NOT NULL DEFAULT '',
    meaning    TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    UNIQUE (name, sentence, style)
);
"""


class SentenceError(ValueError):
    pass


@dataclass(frozen=True)
class Sentence:
    name: str
    sentence: str
    style: str = "Neutral"
    enabled: bool = True
    builtin_demo: bool = False          # BUILT-IN SPEECH DEMO record (not typed by the user)
    # metadata describing the sentence (NOT detected from the user, NOT the voice style)
    emotion: str = ""                   # e.g. "Need / request"
    icon: str = ""                      # e.g. a water-drop emoji
    meaning: str = ""
    id: int | None = None
    created_at: str = ""
    updated_at: str = ""


def name_from_sentence(text: str) -> str:
    """'I need some water.' -> 'I_NEED_SOME_WATER' (used when no name is typed)."""
    words = re.findall(r"[A-Za-z0-9]+", text.upper())
    name = "_".join(words)[:32].rstrip("_")
    return name or "SENTENCE"


def normalize(s: Sentence) -> Sentence:
    text = " ".join(s.sentence.split())
    name = re.sub(r"\s+", "_", s.name.strip()).upper() or name_from_sentence(text)
    return replace(s, name=name, sentence=text)


def validate(s: Sentence) -> None:
    if not s.sentence:
        raise SentenceError("Type a sentence first.")
    if len(s.sentence) > MAX_SENTENCE:
        raise SentenceError(f"Sentence is longer than {MAX_SENTENCE} characters.")
    if not NAME_RE.match(s.name):
        raise SentenceError("Sentence name must be 1-32 characters: A-Z, 0-9 or _ (e.g. WATER_REQUEST).")
    if s.style not in SPEECH_STYLES:
        raise SentenceError(f"Unknown speech style {s.style!r}.")


class SentenceRepository:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")   # deleting a sentence unlinks gestures (SET NULL)
        self._conn.executescript(SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(speech_sentences)")}
        if "builtin_demo" not in cols:      # table created before the speech demo existed
            self._conn.execute("ALTER TABLE speech_sentences ADD COLUMN builtin_demo INTEGER NOT NULL DEFAULT 0")
        for col in ("emotion", "icon", "meaning"):   # table created before sentence metadata existed
            if col not in cols:
                self._conn.execute(f"ALTER TABLE speech_sentences ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def all(self) -> list[Sentence]:
        rows = self._conn.execute("SELECT * FROM speech_sentences ORDER BY name, sentence, style, id")
        return [self._row(r) for r in rows]

    def get(self, sentence_id: int) -> Sentence | None:
        r = self._conn.execute("SELECT * FROM speech_sentences WHERE id = ?", (sentence_id,)).fetchone()
        return self._row(r) if r else None

    def find(self, name: str, sentence: str, style: str) -> Sentence | None:
        r = self._conn.execute("SELECT * FROM speech_sentences WHERE name = ? AND sentence = ? AND style = ?",
                               (name, sentence, style)).fetchone()
        return self._row(r) if r else None

    def add(self, s: Sentence) -> Sentence:
        """Store a NEW record (never overwrites another sentence or style)."""
        s = normalize(s)
        validate(s)
        if self.find(s.name, s.sentence, s.style) is not None:
            raise SentenceError(f'"{s.sentence}" ({s.style}) is already saved as {s.name}.')
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO speech_sentences (name, sentence, style, enabled, builtin_demo, emotion, icon, meaning,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (s.name, s.sentence, s.style, int(s.enabled), int(s.builtin_demo), s.emotion.strip(), s.icon.strip(),
                 s.meaning.strip(), now, now))
        stored = self.get(int(cur.lastrowid))
        assert stored is not None
        return stored

    def delete(self, sentence_id: int) -> bool:
        with self._conn:
            return self._conn.execute("DELETE FROM speech_sentences WHERE id = ?", (sentence_id,)).rowcount == 1

    def set_enabled(self, sentence_id: int, enabled: bool) -> None:
        with self._conn:
            self._conn.execute("UPDATE speech_sentences SET enabled = ?, updated_at = ? WHERE id = ?",
                               (int(enabled), datetime.now().isoformat(timespec="seconds"), sentence_id))

    @staticmethod
    def _row(r: sqlite3.Row) -> Sentence:
        return Sentence(name=r["name"], sentence=r["sentence"], style=r["style"], enabled=bool(r["enabled"]),
                        builtin_demo=bool(r["builtin_demo"]), emotion=r["emotion"], icon=r["icon"],
                        meaning=r["meaning"], id=r["id"], created_at=r["created_at"], updated_at=r["updated_at"])

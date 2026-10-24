"""SQLite gesture repository and sample CSV store (temporary databases only)."""

import sqlite3

import pytest

from sign2speech_v2.core.frame import SensorFrame, Source
from sign2speech_v2.core.patterns import GesturePattern
from sign2speech_v2.gestures.repository import Gesture, GestureError, GestureRepository
from sign2speech_v2.gestures.samples import COLUMNS, SampleStore

FIELDS = {"id", "name", "display_text", "isl_description", "category", "enabled", "finger_pattern",
          "confidence_threshold", "speech_style", "sentence_role", "builtin_demo", "sentence_id", "created_at",
          "updated_at"}


@pytest.fixture
def repo(tmp_path):
    r = GestureRepository(tmp_path / "studio.db")
    yield r
    r.close()


def G(name="HELLO", pattern="OBOBO", **kw):
    return Gesture(name=name, display_text=kw.pop("display_text", name.title()),
                   finger_pattern=GesturePattern.parse(pattern), **kw)


def test_schema_has_required_fields(tmp_path, repo):
    con = sqlite3.connect(tmp_path / "studio.db")
    cols = {r[1] for r in con.execute("PRAGMA table_info(gestures)")}
    assert cols == FIELDS
    assert {r[1] for r in con.execute("PRAGMA table_info(meta)")} == {"key", "value"}


def test_save_and_read_back(repo):
    s = repo.save(G("hello", "O B X B O", isl_description="palm out", category="Greeting", speech_style="Happy"))
    assert s.id is not None and s.name == "HELLO"          # names normalized to labels
    assert str(s.finger_pattern) == "O B X B O"
    assert s.created_at and s.updated_at
    assert repo.get(s.id) == s


def test_multiple_gestures(repo):
    for i, p in enumerate(("OBOBO", "BOOOO", "BBBBB", "OOOOO")):
        repo.save(G(f"G{i}", p))
    assert [g.name for g in repo.all()] == ["G0", "G1", "G2", "G3"]


def test_duplicate_name_rejected(repo):
    repo.save(G("HELLO"))
    with pytest.raises(GestureError):
        repo.save(G("hello", "BBBBB"))


def test_update_existing(repo):
    s = repo.save(G("HELLO"))
    u = repo.save(Gesture(**{**s.__dict__, "display_text": "Hi there"}))
    assert u.id == s.id and u.display_text == "Hi there" and len(repo.all()) == 1


def test_delete(repo):
    a, b = repo.save(G("A", "OOOOO")), repo.save(G("B", "BBBBB"))
    assert repo.delete(a.id)
    assert [g.name for g in repo.all()] == ["B"]
    assert not repo.delete(a.id)


def test_enable_disable(repo):
    s = repo.save(G("HELLO"))
    repo.set_enabled(s.id, False)
    assert repo.enabled() == []
    repo.set_enabled(s.id, True)
    assert [g.name for g in repo.enabled()] == ["HELLO"]


@pytest.mark.parametrize("bad", [G("bad name!"), G("", "OBOBO"), G("ALLX", "XXXXX"),
                                 G("NOTEXT", display_text="  "), G("STYLE", speech_style="Sarcastic")])
def test_validation(repo, bad):
    with pytest.raises(GestureError):
        repo.save(bad)


def test_integrity_and_persistence(tmp_path):
    r = GestureRepository(tmp_path / "db.sqlite")
    r.save(G("HELLO"))
    r.close()
    r2 = GestureRepository(tmp_path / "db.sqlite")
    assert r2.integrity_ok() and [g.name for g in r2.all()] == ["HELLO"]
    r2.close()


def test_sample_csv(tmp_path):
    store = SampleStore(tmp_path / "samples")
    frames = [SensorFrame((1, 2, 3, 4, 5), (0.1, 0.2, 1.0), (1.0, 2.0, 3.0), 10.0 + i, Source.REAL)
              for i in range(3)]
    assert store.append("hello", frames) == 3
    path = tmp_path / "samples" / "HELLO.csv"
    lines = path.read_text().splitlines()
    assert lines[0].split(",") == list(COLUMNS)
    assert lines[1].split(",")[:7] == ["10.000", "HELLO", "1", "2", "3", "4", "5"]
    assert store.count("HELLO") == 3
    with pytest.raises(ValueError):
        store.path_for("../evil")

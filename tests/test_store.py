from leaktrace.store import Store


def test_first_seen_with_a_timezone_is_compared_in_utc(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.log_view("a", "lec", "play", at="2026-11-20T06:00:00+00:00")
    s.log_view("b", "lec", "play", at="2026-11-20T08:00:00+00:00")
    assert s.watched_by("lec", before="2026-11-20T12:00:00+05:30") == ["a"]  # 06:30 UTC
    assert s.watched_by("lec", before="2026-11-20T06:30:00Z") == ["a"]
    assert s.watched_by("lec", before="2026-11-20T09:00:00") == ["a", "b"]  # no zone given: UTC


V1_SCHEMA = """
CREATE TABLE students (id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE lectures (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, meta TEXT, created TEXT NOT NULL);
CREATE TABLE views (id INTEGER PRIMARY KEY AUTOINCREMENT, student TEXT NOT NULL, lecture TEXT NOT NULL,
                    kind TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX views_lecture ON views (lecture, at);
CREATE TABLE traces (id TEXT PRIMARY KEY, lecture TEXT, verdict TEXT, report TEXT NOT NULL, created TEXT NOT NULL);
INSERT INTO students VALUES ('s1', 'Asha', '2026-11-01T00:00:00+00:00');
INSERT INTO lectures VALUES ('ep1', 'Episode 1', 'ready', NULL, '2026-11-01T00:00:00+00:00');
INSERT INTO views (student, lecture, kind, at) VALUES ('s1', 'ep1', 'play', '2026-11-02T00:00:00+00:00');
INSERT INTO traces VALUES ('t1', 'ep1', 'identified', '{}', '2026-11-03T00:00:00+00:00');
"""


def test_a_v1_database_is_migrated_in_place(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript(V1_SCHEMA)
    s = Store(path)  # opening it upgrades it, keeping everything
    assert s.viewer_ids() == ["s1"] and s.watched_by("ep1") == ["s1"]
    assert [(t["id"], t["name"]) for t in s.titles()] == [("ep1", "Episode 1")]
    assert s.traces()[0]["title"] == "ep1"
    Store(path)  # and opening it again is a no-op


def test_a_half_upgraded_v1_database_still_keeps_its_rows(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:  # an upgrade that stopped after creating the new, empty tables
        db.executescript(V1_SCHEMA + "CREATE TABLE viewers (id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL);"
                         "CREATE TABLE titles (id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,"
                         " meta TEXT, created TEXT NOT NULL);")
    s = Store(path)
    assert s.viewer_ids() == ["s1"] and [t["id"] for t in s.titles()] == ["ep1"]

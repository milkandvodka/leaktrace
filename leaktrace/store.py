"""SQLite: students, lectures, who opened what (the view log that narrows suspects), jobs and trace reports."""
import json
import sqlite3
from contextlib import closing
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lectures (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
                                     meta TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS views (id INTEGER PRIMARY KEY AUTOINCREMENT, student TEXT NOT NULL, lecture TEXT NOT NULL,
                                  kind TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS views_lecture ON views (lecture, at);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL,
                                 detail TEXT, result TEXT, error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS traces (id TEXT PRIMARY KEY, lecture TEXT, verdict TEXT, report TEXT NOT NULL,
                                   created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watched (key TEXT PRIMARY KEY, trace TEXT, at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc(t: str) -> str:
    """Any ISO time (with or without a zone; no zone means UTC) in the one format stored, so text order is time order."""
    d = datetime.fromisoformat(t)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._db()) as db, db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _write(self, sql: str, args=()) -> None:
        with self._lock, closing(self._db()) as db, db:  # closing() closes; the inner `db` commits
            db.execute(sql, args)

    def _rows(self, sql: str, args=()) -> list[dict]:
        with closing(self._db()) as db:
            return [dict(r) for r in db.execute(sql, args)]

    # students
    def add_student(self, sid: str, name: str) -> None:
        self._write("INSERT INTO students (id, name, created) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                    (sid, name, now()))

    def students(self) -> list[dict]:
        return self._rows("SELECT id, name, created FROM students ORDER BY id")

    def student_ids(self) -> list[str]:
        return [r["id"] for r in self._rows("SELECT id FROM students ORDER BY id")]

    # lectures
    def add_lecture(self, lid: str, title: str, status: str = "processing", meta: dict | None = None) -> None:
        self._write("INSERT INTO lectures (id, title, status, meta, created) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title, status=excluded.status, meta=excluded.meta",
                    (lid, title, status, json.dumps(meta) if meta else None, now()))

    def set_lecture(self, lid: str, status: str, meta: dict | None = None) -> None:
        self._write("UPDATE lectures SET status=?, meta=COALESCE(?, meta) WHERE id=?",
                    (status, json.dumps(meta) if meta else None, lid))

    def lecture(self, lid: str) -> dict | None:
        rows = self._rows("SELECT * FROM lectures WHERE id=?", (lid,))
        return _decode(rows[0], "meta") if rows else None

    def lectures(self) -> list[dict]:
        return [_decode(r, "meta") for r in self._rows("SELECT * FROM lectures ORDER BY created DESC")]

    # views
    def log_view(self, student: str, lecture: str, kind: str, at: str | None = None) -> None:
        self._write("INSERT INTO views (student, lecture, kind, at) VALUES (?, ?, ?, ?)", (student, lecture, kind, utc(at) if at else now()))

    def viewers(self, lecture: str, before: str | None = None) -> list[str]:
        """Students who opened the lecture (before `before`, an ISO time, when given)."""
        sql = "SELECT DISTINCT student FROM views WHERE lecture=?" + (" AND at < ?" if before else "") + " ORDER BY student"
        return [r["student"] for r in self._rows(sql, (lecture, utc(before)) if before else (lecture,))]

    # jobs
    def create_job(self, jid: str, kind: str, detail: dict | None = None) -> None:
        t = now()
        self._write("INSERT INTO jobs (id, kind, status, progress, detail, created, updated) VALUES (?, ?, 'queued', 0, ?, ?, ?)",
                    (jid, kind, json.dumps(detail or {}), t, t))

    def update_job(self, jid: str, status: str | None = None, progress: float | None = None,
                   result: dict | None = None, error: str | None = None) -> None:
        self._write("UPDATE jobs SET status=COALESCE(?, status), progress=COALESCE(?, progress), "
                    "result=COALESCE(?, result), error=COALESCE(?, error), updated=? WHERE id=?",
                    (status, progress, json.dumps(result) if result is not None else None, error, now(), jid))

    def job(self, jid: str) -> dict | None:
        rows = self._rows("SELECT * FROM jobs WHERE id=?", (jid,))
        return _decode(_decode(rows[0], "detail"), "result") if rows else None

    def fail_unfinished_jobs(self) -> None:
        """Called at start-up: anything still 'running' died with the previous process."""
        self._write("UPDATE jobs SET status='failed', error='interrupted by a restart', updated=? "
                    "WHERE status IN ('queued', 'running')", (now(),))

    # traces
    def add_trace(self, tid: str, lecture: str | None, verdict: str, report: dict) -> None:
        self._write("INSERT INTO traces (id, lecture, verdict, report, created) VALUES (?, ?, ?, ?, ?)",
                    (tid, lecture, verdict, json.dumps(report), now()))

    def trace(self, tid: str) -> dict | None:
        rows = self._rows("SELECT * FROM traces WHERE id=?", (tid,))
        return _decode(rows[0], "report") if rows else None

    def traces(self) -> list[dict]:
        return self._rows("SELECT id, lecture, verdict, created FROM traces ORDER BY created DESC")

    # posts the Telegram watcher has handled
    def seen(self, key: str) -> bool:
        return bool(self._rows("SELECT 1 FROM watched WHERE key=?", (key,)))

    def mark_seen(self, key: str, trace_id: str | None) -> None:
        self._write("INSERT OR REPLACE INTO watched (key, trace, at) VALUES (?, ?, ?)", (key, trace_id, now()))


def _decode(row: dict, field: str) -> dict:
    if row.get(field):
        row[field] = json.loads(row[field])
    return row

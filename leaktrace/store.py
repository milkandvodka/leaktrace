"""SQLite: viewers, titles, who opened what (the view log that narrows suspects), jobs and trace reports."""
import json
import sqlite3
from contextlib import closing
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS viewers (id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS titles (id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
                                     meta TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS views (id INTEGER PRIMARY KEY AUTOINCREMENT, viewer TEXT NOT NULL, title TEXT NOT NULL,
                                  kind TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS views_title ON views (title, at);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL,
                                 detail TEXT, result TEXT, error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS traces (id TEXT PRIMARY KEY, title TEXT, verdict TEXT, report TEXT NOT NULL,
                                   created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watched (key TEXT PRIMARY KEY, trace TEXT, at TEXT NOT NULL);
"""


# v1 names (lectures and students) -> v2 (titles and viewers): (table, old column, new column) renames, in order.
_V1_RENAMES = [("views", "student", "viewer"), ("views", "lecture", "title"), ("traces", "lecture", "title"),
               ("lectures", "title", "name")]


def _migrate(db: sqlite3.Connection) -> None:
    """Upgrade a v1 database in place, keeping its data."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, old, new in _V1_RENAMES:
        if table in tables and old in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    db.execute("DROP INDEX IF EXISTS views_lecture")
    for old, new in (("students", "viewers"), ("lectures", "titles")):
        if old in tables and new in tables and not db.execute(f"SELECT 1 FROM {new} LIMIT 1").fetchone():
            db.execute(f"DROP TABLE {new}")  # an upgrade stopped after creating it: the old table has the rows
            tables.discard(new)
        if old in tables and new not in tables:
            db.execute(f"ALTER TABLE {old} RENAME TO {new}")


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
            _migrate(db)
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

    # viewers
    def add_viewer(self, sid: str, name: str) -> None:
        self._write("INSERT INTO viewers (id, name, created) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                    (sid, name, now()))

    def viewers(self) -> list[dict]:
        return self._rows("SELECT id, name, created FROM viewers ORDER BY id")

    def viewer_ids(self) -> list[str]:
        return [r["id"] for r in self._rows("SELECT id FROM viewers ORDER BY id")]

    # titles
    def add_title(self, lid: str, name: str, status: str = "processing", meta: dict | None = None) -> None:
        self._write("INSERT INTO titles (id, name, status, meta, created) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, status=excluded.status, meta=excluded.meta",
                    (lid, name, status, json.dumps(meta) if meta else None, now()))

    def set_title(self, lid: str, status: str, meta: dict | None = None) -> None:
        self._write("UPDATE titles SET status=?, meta=COALESCE(?, meta) WHERE id=?",
                    (status, json.dumps(meta) if meta else None, lid))

    def title(self, lid: str) -> dict | None:
        rows = self._rows("SELECT * FROM titles WHERE id=?", (lid,))
        return _decode(rows[0], "meta") if rows else None

    def titles(self) -> list[dict]:
        return [_decode(r, "meta") for r in self._rows("SELECT * FROM titles ORDER BY created DESC")]

    # views
    def log_view(self, viewer: str, title: str, kind: str, at: str | None = None) -> None:
        self._write("INSERT INTO views (viewer, title, kind, at) VALUES (?, ?, ?, ?)", (viewer, title, kind, utc(at) if at else now()))

    def watched_by(self, title: str, before: str | None = None) -> list[str]:
        """Viewers who opened the title (before `before`, an ISO time, when given)."""
        sql = "SELECT DISTINCT viewer FROM views WHERE title=?" + (" AND at < ?" if before else "") + " ORDER BY viewer"
        return [r["viewer"] for r in self._rows(sql, (title, utc(before)) if before else (title,))]

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
    def add_trace(self, tid: str, title: str | None, verdict: str, report: dict) -> None:
        self._write("INSERT INTO traces (id, title, verdict, report, created) VALUES (?, ?, ?, ?, ?)",
                    (tid, title, verdict, json.dumps(report), now()))

    def trace(self, tid: str) -> dict | None:
        rows = self._rows("SELECT * FROM traces WHERE id=?", (tid,))
        return _decode(rows[0], "report") if rows else None

    def traces(self) -> list[dict]:
        return self._rows("SELECT id, title, verdict, created FROM traces ORDER BY created DESC")

    # posts the Telegram watcher has handled
    def seen(self, key: str) -> bool:
        return bool(self._rows("SELECT 1 FROM watched WHERE key=?", (key,)))

    def mark_seen(self, key: str, trace_id: str | None) -> None:
        self._write("INSERT OR REPLACE INTO watched (key, trace, at) VALUES (?, ?, ?)", (key, trace_id, now()))


def _decode(row: dict, field: str) -> dict:
    if row.get(field):
        row[field] = json.loads(row[field])
    return row

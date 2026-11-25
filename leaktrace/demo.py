"""The public demo's audience and sample leaks: real leaks of real viewers' copies, made and traced once by this
server (the marks come from its own secret, so they can't be made anywhere else)."""
import json
import shutil
from pathlib import Path

from . import deliver
from .bench.attacks import ATTACKS, trim
from .config import settings
from .store import Store
from .trace import trace

TITLE = "demo-lecture"
VIEWERS = [("demo-v001", "Asha Rao"), ("demo-v002", "Ravi Kumar"), ("demo-v003", "Meera Iyer"),
           ("demo-v004", "Arjun Shah"), ("demo-v005", "Nisha Verma"), ("demo-v006", "Kabir Singh")]
POOL = 994  # with the six above, the same 1,000 accounts the bench traces among
OPENED_AT = "2026-01-01T00:00:00"
SAMPLES = [
    {"key": "telegram", "viewer": "demo-v004", "label": "Forwarded on Telegram",
     "blurb": "Re-compressed to 480p with thin sound, the way Telegram does it."},
    {"key": "camcord", "viewer": "demo-v002", "label": "Filmed with a phone",
     "blurb": "A phone pointed at a laptop: tilted, blurred, colour-shifted, with room sound."},
    {"key": "crop70", "viewer": "demo-v005", "label": "Cropped",
     "blurb": "The edges cut off, logo and all."},
    {"key": "screenrec", "viewer": "demo-v001", "label": "Screen-recorded",
     "blurb": "Recorded off the desktop while it played, heavily compressed."},
]


def folder() -> Path:
    return settings().data / "demo"


def audience() -> list[str]:
    return [v for v, _ in VIEWERS] + [f"demo-a{i:04d}" for i in range(1, POOL + 1)]


def add_audience(store: Store) -> None:
    """The six named viewers and the sample accounts, all of whom opened the title (once)."""
    if set(audience()) <= set(store.watched_by(TITLE)):
        return
    store.add_viewers(VIEWERS + [(f"demo-a{i:04d}", f"Sample account {i}") for i in range(1, POOL + 1)])
    store.log_views([(v, TITLE, "play", OPENED_AT) for v in audience()])


def done() -> list[dict]:
    """The samples made so far: key, label, blurb and the id of their trace."""
    p = folder() / "samples.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def missing() -> list[str]:
    have = {s["key"] for s in done() if (folder() / f"{s['key']}.mp4").exists()}
    return [s["key"] for s in SAMPLES if s["key"] not in have]


def make_samples(store: Store, keys: list[str], seconds: float = 120, progress=lambda p: None) -> list[dict]:
    """Leak an excerpt of each sample viewer's copy with the bench's attack of the same name, trace it among the
    audience, and record it (after each one, so a restart only redoes what's missing)."""
    work = folder() / "work"
    work.mkdir(parents=True, exist_ok=True)
    todo = [s for s in SAMPLES if s["key"] in keys]
    for i, s in enumerate(todo):
        leak = ATTACKS[s["key"]](trim(deliver.export(TITLE, s["viewer"]), work, seconds, seed=i), work)
        out = folder() / f"{s['key']}.mp4"
        shutil.move(leak, out)
        report = trace(out, store, candidates=audience())
        rows = {r["key"]: r for r in done()} | {s["key"]: {k: s[k] for k in ("key", "label", "blurb")} | {"trace": report.id}}
        (folder() / "samples.json").write_text(json.dumps([rows[x["key"]] for x in SAMPLES if x["key"] in rows], indent=1),
                                               encoding="utf-8")
        progress((i + 1) / len(todo))
    shutil.rmtree(work, ignore_errors=True)
    return done()

"""Command line: python -m leaktrace <command> ..."""
import argparse
import json
import secrets
import sys
from pathlib import Path


def _store():
    from .config import settings
    from .store import Store
    return Store(settings().data / "leaktrace.db")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="leaktrace", description="Per-student invisible marks in lectures, and leak tracing.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="print a new LEAKTRACE_SECRET")

    s = sub.add_parser("synth", help="make a synthetic lecture for testing")
    s.add_argument("out", type=Path)
    s.add_argument("--seconds", type=float, default=240)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--no-tts", action="store_true", help="speech-like noise instead of text-to-speech")

    s = sub.add_parser("ingest", help="package a lecture into marked renditions A and B")
    s.add_argument("master", type=Path)
    s.add_argument("--id", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--preset", default="medium", help="x264 preset for the renditions")

    s = sub.add_parser("student", help="add a student")
    s.add_argument("id")
    s.add_argument("name")

    s = sub.add_parser("export", help="a student's own copy of a lecture, as MP4")
    s.add_argument("lecture")
    s.add_argument("student")

    s = sub.add_parser("trace", help="trace a leaked file")
    s.add_argument("leak", type=Path)
    s.add_argument("--first-seen", help="ISO time the leak was first seen (narrows suspects)")

    s = sub.add_parser("watch", help="watch Telegram channels/groups and trace every new video")
    s.add_argument("chats", nargs="+", help="@username, t.me link or numeric id")
    s.add_argument("--backfill", type=int, default=20, help="also trace this many recent posts per chat")

    s = sub.add_parser("bench", help="attack bench on a packaged lecture")
    s.add_argument("lecture")
    s.add_argument("--students", type=int, default=2)
    s.add_argument("--clips", default="full,120,60", help="comma list of seconds, or 'full'")
    s.add_argument("--attacks", default="all")
    s.add_argument("--pool", type=int, default=1000)

    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        print(secrets.token_hex(32))
    elif a.cmd == "synth":
        from .synth import make_lecture
        print(make_lecture(a.out, a.seconds, a.seed, tts=not a.no_tts))
    elif a.cmd == "ingest":
        from .ingest import ingest
        st = _store()
        st.add_lecture(a.id, a.title)
        meta = ingest(a.master, a.id, a.title, progress=lambda p: print(f"\r{p:5.0%}", end="", file=sys.stderr),
                      preset=a.preset)
        st.set_lecture(a.id, "ready", {"segments": meta.n_segments, "duration": meta.duration})
        print(f"\npackaged {meta.id}: {meta.n_segments} segments, {meta.marked_segments} marked")
    elif a.cmd == "student":
        _store().add_student(a.id, a.name)
    elif a.cmd == "export":
        from .deliver import export
        _store().log_view(a.student, a.lecture, "download")
        print(export(a.lecture, a.student))
    elif a.cmd == "trace":
        from .trace import trace
        r = trace(a.leak, _store(), first_seen=a.first_seen,
                  progress=lambda p: print(f"\r{p:5.0%}", end="", file=sys.stderr))
        print("\n" + json.dumps({k: v for k, v in r.as_dict().items() if k != "segments"}, indent=1))
    elif a.cmd == "watch":
        from .watch import run_telegram
        run_telegram(a.chats, backfill=a.backfill)
    elif a.cmd == "bench":
        from .bench.attacks import ATTACKS
        from .bench.run import bench
        clips = tuple(None if c == "full" else float(c) for c in a.clips.split(","))
        attacks = list(ATTACKS) if a.attacks == "all" else a.attacks.split(",")
        bench(a.lecture, students=a.students, clips=clips, attacks=attacks, pool_size=a.pool)
    return 0

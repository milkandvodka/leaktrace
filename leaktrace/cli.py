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
    ap = argparse.ArgumentParser(prog="leaktrace", description="Per-viewer invisible marks in titles, and leak tracing.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="print a new LEAKTRACE_SECRET")

    s = sub.add_parser("synth", help="make a synthetic title for testing")
    s.add_argument("out", type=Path)
    s.add_argument("--seconds", type=float, default=240)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--no-tts", action="store_true", help="speech-like noise instead of text-to-speech")

    s = sub.add_parser("ingest", help="package a title into marked renditions A and B")
    s.add_argument("master", type=Path)
    s.add_argument("--id", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--preset", default="medium", help="x264 preset for the renditions")

    s = sub.add_parser("viewer", help="add a viewer")
    s.add_argument("id")
    s.add_argument("name")

    s = sub.add_parser("export", help="a viewer's own copy of a title, as MP4")
    s.add_argument("title")
    s.add_argument("viewer")

    s = sub.add_parser("trace", help="trace a leaked file")
    s.add_argument("leak", type=Path)
    s.add_argument("--first-seen", help="ISO time the leak was first seen (narrows suspects)")

    s = sub.add_parser("watch", help="watch Telegram channels/groups and trace every new video")
    s.add_argument("chats", nargs="+", help="@username, t.me link or numeric id")
    s.add_argument("--backfill", type=int, default=20, help="also trace this many recent posts per chat")

    s = sub.add_parser("bench", help="attack bench on a packaged title")
    s.add_argument("title")
    s.add_argument("--viewers", type=int, default=2)
    s.add_argument("--clips", default="full,120,60", help="comma list of seconds, or 'full'")
    s.add_argument("--attacks", default="all")
    s.add_argument("--pool", type=int, default=1000)

    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        print(secrets.token_hex(32))
    elif a.cmd == "synth":
        from .synth import make_title
        print(make_title(a.out, a.seconds, a.seed, tts=not a.no_tts))
    elif a.cmd == "ingest":
        from .ingest import ingest
        st = _store()
        st.add_title(a.id, a.name)
        meta = ingest(a.master, a.id, a.name, progress=lambda p: print(f"\r{p:5.0%}", end="", file=sys.stderr),
                      preset=a.preset)
        st.set_title(a.id, "ready", {"duration": meta.duration, "segments": meta.n_segments,
                                       "marked": meta.marked_segments, "has_audio": meta.has_audio})
        print(f"\npackaged {meta.id}: {meta.n_segments} segments, {meta.marked_segments} marked")
    elif a.cmd == "viewer":
        _store().add_viewer(a.id, a.name)
    elif a.cmd == "export":
        from .deliver import export
        _store().log_view(a.viewer, a.title, "download")
        print(export(a.title, a.viewer))
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
        bench(a.title, viewers=a.viewers, clips=clips, attacks=attacks, pool_size=a.pool)
    return 0

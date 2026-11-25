"""Command line: python -m leaktrace <command> ..."""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path


def _store():
    from .config import settings
    from .store import Store
    return Store(settings().data / "leaktrace.db")


def edge_bundle(edge_key_hex: str, naming_key_hex: str) -> str:
    """The CloudFront Function, ready to deploy: edge/core.js inlined and the keys filled in."""
    from .config import ROOT
    core = (ROOT / "edge" / "core.js").read_text(encoding="utf-8")
    fn = (ROOT / "edge" / "cloudfront-function.js").read_text(encoding="utf-8")
    for k, v in {"__LEAKTRACE_EDGE_KEY__": edge_key_hex, "__LEAKTRACE_NAMING_KEY__": naming_key_hex,
                 "__LEAKTRACE_EDGE_LAYOUT__": os.getenv("LEAKTRACE_EDGE_LAYOUT") or "cmaf",
                 "__LEAKTRACE_EDGE_FIRST_SEGMENT__": str(int(os.getenv("LEAKTRACE_EDGE_FIRST_SEGMENT") or 0))}.items():
        fn = fn.replace(k, v)
    return core + "\n" + fn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="leaktrace", description="Per-viewer invisible marks in titles, and leak tracing.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="print a new LEAKTRACE_SECRET")

    s = sub.add_parser("synth", help="make a synthetic title for testing")
    s.add_argument("out", type=Path)
    s.add_argument("--seconds", type=float, default=240)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--no-tts", action="store_true", help="speech-like noise instead of text-to-speech")

    s = sub.add_parser("ingest", help="package a title: marked masters A and B, their CMAF ladder and manifests")
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

    s = sub.add_parser("export-masters", help="the marked masters A and B and their grid, for your own pipeline")
    s.add_argument("title")
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser("edge-keys", help="write the CDN edge's two keys (derived from the secret) to a file")
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser("edge-bundle", help="write a ready-to-deploy edge function with the keys filled in")
    s.add_argument("target", choices=["cloudfront"])
    s.add_argument("--out", type=Path, required=True)

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
        st.set_title(a.id, "ready", meta.summary())
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
    elif a.cmd == "export-masters":
        import shutil
        from .ingest import TitleMeta, title_dir
        folder = title_dir(a.title)
        meta = TitleMeta.load(folder)
        a.out.mkdir(parents=True, exist_ok=True)
        for v in "AB":
            shutil.copyfile(folder / "masters" / f"{v}.mp4", a.out / f"{v}.mp4")
        grid = {"title": meta.id, **meta.grid.to_json(), "segment_seconds": meta.grid.seconds,
                "segments": meta.n_segments, "frames": meta.n_frames, "audio_sample_rate": 48000,
                "note": "Encode A and B with the same settings, a segment of seg_frames frames each starting with an "
                        "IDR frame (the masters have one there); package A into <title>/A/ and B into <title>/B/ "
                        "with the same file names; cut audio on the same boundaries."}
        (a.out / "grid.json").write_text(json.dumps(grid, indent=1), encoding="utf-8")
        print(f"wrote {a.out}: A.mp4, B.mp4, grid.json ({meta.n_segments} segments of {meta.grid.seg_frames} frames)")
    elif a.cmd in ("edge-keys", "edge-bundle"):
        from .config import settings
        from .edge import edge_key, naming_key
        ek, nk = edge_key(settings().secret).hex(), naming_key(settings().secret).hex()
        text = f"LEAKTRACE_EDGE_KEY={ek}\nLEAKTRACE_NAMING_KEY={nk}\n" if a.cmd == "edge-keys" else edge_bundle(ek, nk)
        a.out.write_text(text, encoding="utf-8")
        os.chmod(a.out, 0o600)
        print(f"wrote {a.out} (it holds the edge keys: keep it private and out of version control)")
    elif a.cmd == "bench":
        from .bench.attacks import ATTACKS
        from .bench.run import bench
        clips = tuple(None if c == "full" else float(c) for c in a.clips.split(","))
        attacks = list(ATTACKS) if a.attacks == "all" else a.attacks.split(",")
        bench(a.title, viewers=a.viewers, clips=clips, attacks=attacks, pool_size=a.pool)
    return 0

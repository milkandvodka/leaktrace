"""Command line: python -m leaktrace <command> ..."""
import argparse
import secrets
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="leaktrace", description="Per-student invisible marks in lectures, and leak tracing.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="print a new LEAKTRACE_SECRET")

    s = sub.add_parser("synth", help="make a synthetic lecture for testing")
    s.add_argument("out", type=Path)
    s.add_argument("--seconds", type=float, default=240)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--no-tts", action="store_true", help="speech-like noise instead of text-to-speech")

    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        print(secrets.token_hex(32))
    elif a.cmd == "synth":
        from .synth import make_lecture
        print(make_lecture(a.out, a.seconds, a.seed, tts=not a.no_tts))
    return 0

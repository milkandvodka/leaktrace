"""The bench: export real viewer copies, put them through every attack, cut them short, trace each one among a
pool of candidates, and count how often the right account comes out on top, gets named, or an innocent one does.
"""
import json
import random
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from .. import deliver, media
from ..config import HEIGHT, WIDTH, settings
from ..ingest import TitleMeta, title_dir
from ..store import Store, now
from ..trace import trace
from .attacks import ATTACKS, collude_average, collude_interleave, telegram, trim


def _psnr(title_id: str, samples: int = 5) -> float:
    """Marked master A against the unmarked analysis copy, luma PSNR over a few segments (the cost of marking;
    the ladder's own encoding loss is the platform's usual)."""
    folder = title_dir(title_id)
    meta = TitleMeta.load(folder)
    errs = []
    for seg in np.linspace(0, meta.marked_segments - 1, samples).round().astype(int):
        g = meta.grid  # both on the analysis canvas, so a 1080p title compares like a 720p one
        a = media.frames(folder / "masters" / "A.mp4", WIDTH, HEIGHT, g.fps, start=float(g.time(seg)))
        m = media.frames(folder / "mezzanine.mp4", WIDTH, HEIGHT, g.fps, start=float(g.time(seg)))
        for x, y, _ in zip(a, m, range(g.seg_frames)):
            errs.append(((x[:HEIGHT].astype(float) - y[:HEIGHT].astype(float)) ** 2).mean())
    return round(float(10 * np.log10(255 ** 2 / np.mean(errs))), 1)


def bench(title_id: str, viewers: int = 2, clips=(None, 120, 60), attacks: list[str] | None = None,
          pool_size: int = 1000, seed: int = 0, collusion: bool = True, log=print) -> dict:
    out_dir = settings().data / "bench" / time.strftime("%Y%m%d-%H%M%S")
    work = out_dir / "work"
    work.mkdir(parents=True)
    store = Store(out_dir / "bench.sqlite")
    pool = [f"bench{i:05d}" for i in range(pool_size)]
    for s in pool:
        store.add_viewer(s, s)
        store.log_view(s, title_id, "play", at="2026-01-01T00:00:00")
    rng = random.Random(seed)
    picks = rng.sample(pool, viewers)
    attacks = attacks or list(ATTACKS)
    rows = []

    def run(label, src, truth: set[str], clip, viewer):
        t0 = time.time()
        try:
            r = trace(src, store, candidates=pool)
        except Exception as e:  # one broken run is a row in the results, not the end of the bench
            rows.append({"attack": label, "clip": clip or "full", "viewer": viewer, "verdict": "error", "top": None,
                         "top_correct": False, "z": 0.0, "false_accusation": False, "segments": 0, "via": None,
                         "seconds": round(time.time() - t0, 1), "carriers": {}, "error": f"{type(e).__name__}: {e}"[:300]})
            log(f"{label:20s} {str(clip or 'full'):>5}  error        {rows[-1]['error'][:80]}")
            return
        top = r.suspects[0]["viewer"] if r.suspects else None
        named = [s["viewer"] for s in r.suspects if s["verdict"] == "identified"]
        rows.append({"attack": label, "clip": clip or "full", "viewer": viewer, "verdict": r.verdict,
                     "top": top, "top_correct": top in truth,
                     "z": round(r.suspects[0]["z"], 2) if r.suspects else 0.0,
                     "false_accusation": any(s not in truth for s in named), "segments": r.segments_used,
                     "via": (r.location or {}).get("via"), "seconds": round(time.time() - t0, 1),
                     "carriers": {k: v["mean_z"] for k, v in r.carriers.items()}})
        log(f"{label:20s} {str(clip or 'full'):>5}  {r.verdict:12s} top={'OK ' if top in truth else 'NO '} "
            f"z={rows[-1]['z']:5.1f} segs={r.segments_used:3d} ({rows[-1]['seconds']}s)")

    for viewer in picks:
        copy = deliver.export(title_id, viewer)
        for name in attacks:
            attacked = ATTACKS[name](copy, work)
            for clip in clips:
                src = trim(attacked, work, clip, seed=rng.randrange(10 ** 6)) if clip else attacked
                run(name, src, {viewer}, clip, viewer)
    if collusion:
        pair = rng.sample(pool, 2)
        mixed = collude_interleave(title_id, pair, work, seed=seed)
        run("collude_interleave", mixed, set(pair), None, "+".join(pair))
        run("collude_interleave+telegram", telegram(mixed, work), set(pair), None, "+".join(pair))
        avg = collude_average([deliver.export(title_id, s) for s in pair], work)
        run("collude_average", avg, set(pair), None, "+".join(pair))

    summary = defaultdict(lambda: {"n": 0, "identified": 0, "narrowed_or_better": 0, "top_correct": 0,
                                   "false_accusations": 0, "z": []})
    for r in rows:
        s = summary[(r["attack"], str(r["clip"]))]
        s["n"] += 1
        s["identified"] += r["verdict"] == "identified" and r["top_correct"]
        s["narrowed_or_better"] += r["verdict"] in ("identified", "narrowed") and r["top_correct"]
        s["top_correct"] += r["top_correct"]
        s["false_accusations"] += r["false_accusation"]
        s["z"].append(r["z"])
    table = [{"attack": a, "clip": c, **{k: v for k, v in s.items() if k != "z"}, "mean_z": round(float(np.mean(s["z"])), 2)}
             for (a, c), s in summary.items()]
    result = {"title": title_id, "created": now(), "candidates": pool_size, "viewers": picks,
              "psnr_db": _psnr(title_id), "rows": rows, "table": table}
    (out_dir / "result.json").write_text(json.dumps(result, indent=1))
    (out_dir / "result.md").write_text(to_markdown(result))
    (settings().data / "bench" / "latest.json").write_text(json.dumps(result, indent=1))
    log(f"\nResults: {out_dir / 'result.md'}")
    return result


def to_markdown(result: dict) -> str:
    lines = [f"# Attack bench: {result['title']} ({result['created']})", "",
             f"{result['candidates']} candidate accounts, picture quality of a marked copy vs the master: "
             f"{result['psnr_db']} dB PSNR.", "",
             "| Attack | Clip | Runs | Right account on top | Named (identified) | Named or narrowed | Innocent named | Mean z |",
             "|---|---|---|---|---|---|---|---|"]
    for t in result["table"]:
        n = t["n"]
        lines.append(f"| {t['attack']} | {t['clip']} | {n} | {t['top_correct']}/{n} | {t['identified']}/{n} | "
                     f"{t['narrowed_or_better']}/{n} | {t['false_accusations']} | {t['mean_z']} |")
    return "\n".join(lines) + "\n"

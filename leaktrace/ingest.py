"""Packaging: one master title in, two marked renditions (A and B) out, plus what tracing needs later.

The title keeps its own frame rate (as an exact fraction) and resolution (capped at LEAKTRACE_MAX_HEIGHT on the
short side, never upscaled); its segments are whole frames, about LEAKTRACE_SEGMENT_SECONDS long (grid.py).
Per segment, streaming:
1. Warp the audio both ways (timing carrier) and measure the shifts each warp really produced.
2. Add the audio carriers to each (every channel gets the same marks).
3. Remap and mark the segment's frames for A and for B (picture carriers), at the title's own size.
4. Feed both encoders.

Tracing works on an unmarked analysis copy (the mezzanine): the title fitted inside 1280x720 at its own frame
rate, mono. The final partial segment is left unmarked. Everything is written to a `.partial` folder and moved
into place only when it's complete, so a crash never leaves a half-packaged title that looks finished.
"""
import json
import math
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np

from . import media
from .carriers import audio as acar
from .carriers import picture as pcar
from .carriers import timing as tcar
from .config import BUDGET, HEIGHT, SR, WIDTH, settings
from .grid import V1, Grid
from .store import now

MARGIN = 0.1  # seconds of audio context each side of a segment
VERSION = 2
MAX_CHANNELS = 2  # surround is folded down to stereo


@dataclass
class TitleMeta:
    id: str
    name: str
    duration: float
    n_frames: int
    n_segments: int
    marked_segments: int
    box: list[int]                 # where the picture sits on the 1280x720 analysis canvas
    has_audio: bool
    timing: list = field(default_factory=list)  # per segment: [[A shifts x8], [B shifts x8]] (None = NaN), or None
    budget: dict = field(default_factory=dict)
    version: int = VERSION
    created: str = ""
    grid: Grid = V1                # titles packaged before v2 were all 25 fps, 1.92 s
    canvas: list[int] = field(default_factory=lambda: [WIDTH, HEIGHT])  # the renditions' own size
    channels: int = 1

    def as_dict(self) -> dict:
        return {**asdict(self), "grid": self.grid.to_json()}

    def save(self, folder: Path) -> None:
        (folder / "meta.json").write_text(json.dumps(self.as_dict()), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> "TitleMeta":
        d = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        return cls(**{**d, "grid": Grid.from_json(d["grid"]) if "grid" in d else V1})

    def real_shifts(self, seg: int) -> np.ndarray | None:
        t = self.timing[seg] if seg < len(self.timing) else None
        return None if t is None else np.array([[np.nan if v is None else v for v in row] for row in t], np.float64)


def title_dir(title_id: str) -> Path:
    return settings().data / "titles" / title_id


def max_height() -> int:
    return int(os.getenv("LEAKTRACE_MAX_HEIGHT") or 1080)


def canvas_size(p: media.Probe) -> tuple[int, int]:
    """The renditions' size: the source as displayed (rotation applied), scaled down so its short side is at most
    LEAKTRACE_MAX_HEIGHT, never up; even on both sides (4:2:0)."""
    w, h = (p.height, p.width) if abs(p.rotation) % 180 == 90 else (p.width, p.height)
    s = min(1.0, max_height() / min(w, h))
    return int(w * s) // 2 * 2, int(h * s) // 2 * 2


def _mezzanine(master: Path, out: Path, has_audio: bool, preset: str, fps: Fraction) -> None:
    """The unmarked analysis copy everything in tracing compares against: fitted inside 1280x720 at the title's
    frame rate, 48 kHz mono."""
    media.run(["-i", master, "-map", "0:v:0", *(["-map", "0:a:0"] if has_audio else []),
               "-vf", media._fit(WIDTH, HEIGHT, fps), "-c:v", "libx264", "-preset", preset, "-crf", 14,
               "-pix_fmt", "yuv420p", *(["-c:a", "aac", "-b:a", "192k", "-ar", SR, "-ac", 1] if has_audio else []),
               "-movflags", "+faststart", out])


def _shifts_json(real: np.ndarray) -> list:
    return [[None if np.isnan(v) else round(float(v), 6) for v in row] for row in real]


def _mono(x: np.ndarray) -> np.ndarray:
    return x if x.ndim == 1 else x.mean(axis=1)


def _mark_pair(chunk, map_a, map_b, k, n, box, key, title_id, seg):
    """Frame k of an n-frame segment for A and for B. When both show the same source frame (most of the time) the
    change is computed once and applied with opposite signs."""
    t = (k + 0.5) / n
    da = pcar.delta(chunk[map_a[k]], t, box, key, title_id, seg)
    db = da if map_a[k] == map_b[k] else pcar.delta(chunk[map_b[k]], t, box, key, title_id, seg)
    return pcar.apply(chunk[map_a[k]], da, +1), pcar.apply(chunk[map_b[k]], db, -1)


def ingest(master: Path, title_id: str, name: str, progress: Callable[[float], None] | None = None,
           preset: str = "medium") -> TitleMeta:
    key = settings().secret
    final = title_dir(title_id)
    work = final.with_name(final.name + ".partial")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    p = media.probe(master)
    if not p.has_video:
        raise ValueError("The file has no video stream.")
    grid = Grid.for_fps(p.rate or Fraction(25))
    cw, ch = canvas_size(p)
    channels = min(MAX_CHANNELS, p.channels) if p.has_audio else 0
    box = list(media.content_box(master))  # on the analysis canvas
    full = (0, 0, cw, ch)                  # on the renditions' own canvas: the whole picture
    mezz = work / "mezzanine.mp4"
    _mezzanine(master, mezz, p.has_audio, preset, grid.fps)
    expected = max(1, math.ceil(p.duration * grid.fps / grid.seg_frames))

    m = round(MARGIN * SR)
    reader = media.AudioReader(master, channels=max(1, channels))
    before = np.zeros((m, *reader.read(0).shape[1:]), np.float32)
    cur, nxt = reader.read(grid.samples(0)), reader.read(grid.samples(1))
    frames = media.frames(master, cw, ch, grid.fps)
    timing_meta, n_frames, seg, marked = [], 0, 0, 0
    n = grid.seg_frames
    enc_a = media.Encoder(work / "A", (cw, ch), grid.fps, n, channels=channels, preset=preset)
    enc_b = media.Encoder(work / "B", (cw, ch), grid.fps, n, channels=channels, preset=preset)
    # ponytail: one process, threads per frame (~37 min per hour of 720p on 16 cores, about twice that at 1080p);
    # split segments across processes if packaging throughput ever matters more than simplicity.
    pool = ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4))  # numpy and OpenCV release the GIL
    try:
        while True:
            chunk = [f for _, f in zip(range(n), frames)]
            if not chunk:
                break
            n_frames += len(chunk)
            if len(chunk) == n:
                x = np.concatenate([before, cur, nxt[:m]]).astype(np.float64)
                if p.has_audio:
                    disp = tcar.template(_mono(cur), SR)
                    wa, wb = tcar.warp_audio(x, SR, MARGIN, disp, +1), tcar.warp_audio(x, SR, MARGIN, disp, -1)
                    timing_meta.append(_shifts_json(tcar.realized(_mono(x), _mono(wa), _mono(wb), SR, MARGIN)))
                    aud_a = acar.embed(np.concatenate([before, wa, nxt[:m]]), SR, MARGIN, key, title_id, seg, +1)
                    aud_b = acar.embed(np.concatenate([before, wb, nxt[:m]]), SR, MARGIN, key, title_id, seg, -1)
                    map_a, map_b = tcar.frame_map(disp, +1, grid.fps, n), tcar.frame_map(disp, -1, grid.fps, n)
                else:
                    timing_meta.append(None)
                    aud_a = aud_b = None
                    map_a = map_b = np.arange(n)
                pcar.prepare(full, key, title_id, seg, (cw, ch))  # patterns once, here, not raced by every worker
                for fa, fb in pool.map(lambda k: _mark_pair(chunk, map_a, map_b, k, n, full, key, title_id, seg),
                                       range(n)):
                    enc_a.write_frame(fa)
                    enc_b.write_frame(fb)
                if p.has_audio:
                    enc_a.write_audio(aud_a)
                    enc_b.write_audio(aud_b)
                marked += 1
            else:  # the final partial segment: unmarked, identical in A and B
                timing_meta.append(None)
                tail = cur[: round(Fraction(len(chunk)) / grid.fps * SR)]
                for f in chunk:
                    enc_a.write_frame(f)
                    enc_b.write_frame(f)
                if p.has_audio:
                    enc_a.write_audio(tail)
                    enc_b.write_audio(tail)
            seg += 1
            before, cur, nxt = cur[-m:], nxt, reader.read(grid.samples(seg + 1))
            if progress:
                progress(min(0.99, seg / expected))
        enc_a.close()
        enc_b.close()
    except BaseException:
        enc_a.abort()
        enc_b.abort()
        raise
    finally:
        pool.shutdown()
        reader.close()
        frames.close()

    meta = TitleMeta(id=title_id, name=name, duration=float(n_frames / grid.fps), n_frames=n_frames, n_segments=seg,
                     marked_segments=marked, box=box, has_audio=p.has_audio, timing=timing_meta,
                     budget=asdict(BUDGET), created=now(), grid=grid, canvas=[cw, ch], channels=channels)
    meta.save(work)
    try:
        from . import index  # built here when available, so a packaged title is immediately traceable
        index.build(work)
    except ImportError:
        pass
    shutil.rmtree(final, ignore_errors=True)
    work.rename(final)
    if progress:
        progress(1.0)
    return meta

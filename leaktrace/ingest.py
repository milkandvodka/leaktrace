"""Packaging: one master title in, two marked renditions (A and B) out, plus what tracing needs later.

Per 1.92 s segment, streaming:
1. Warp the audio both ways (timing carrier) and measure the shifts each warp really produced.
2. Add the audio carriers to each.
3. Remap and mark the 48 frames for A and for B (picture carriers).
4. Feed both encoders.

The final partial segment is left unmarked. Everything is written to a `.partial` folder and moved into place
only when it's complete, so a crash never leaves a half-packaged title that looks finished.
"""
import json
import math
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import media
from .carriers import audio as acar
from .carriers import picture as pcar
from .carriers import timing as tcar
from .config import BUDGET, FPS, HEIGHT, SEG_FRAMES, SEG_SAMPLES, SR, WIDTH, settings
from .store import now

MARGIN = 0.1  # seconds of audio context each side of a segment
VERSION = 1


@dataclass
class TitleMeta:
    id: str
    name: str
    duration: float
    n_frames: int
    n_segments: int
    marked_segments: int
    box: list[int]
    has_audio: bool
    timing: list = field(default_factory=list)  # per segment: [[A shifts x8], [B shifts x8]] (None = NaN), or None
    budget: dict = field(default_factory=dict)
    version: int = VERSION
    created: str = ""

    def save(self, folder: Path) -> None:
        (folder / "meta.json").write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> "TitleMeta":
        return cls(**json.loads((folder / "meta.json").read_text(encoding="utf-8")))

    def real_shifts(self, seg: int) -> np.ndarray | None:
        t = self.timing[seg] if seg < len(self.timing) else None
        return None if t is None else np.array([[np.nan if v is None else v for v in row] for row in t], np.float64)


def title_dir(title_id: str) -> Path:
    return settings().data / "titles" / title_id


def _mezzanine(master: Path, out: Path, has_audio: bool, preset: str) -> None:
    """The unmarked reference everything else is made from: canonical size, constant 25 fps, 48 kHz mono."""
    media.run(["-i", master, "-map", "0:v:0", *(["-map", "0:a:0"] if has_audio else []),
               "-vf", media._fit(WIDTH, HEIGHT, FPS), "-c:v", "libx264", "-preset", preset, "-crf", 14,
               "-pix_fmt", "yuv420p", *(["-c:a", "aac", "-b:a", "192k", "-ar", SR, "-ac", 1] if has_audio else []),
               "-movflags", "+faststart", out])


def _shifts_json(real: np.ndarray) -> list:
    return [[None if np.isnan(v) else round(float(v), 6) for v in row] for row in real]


def _mark_pair(chunk, map_a, map_b, k, box, key, title_id, seg):
    """Frame k of a segment for A and for B. When both show the same source frame (most of the time) the
    change is computed once and applied with opposite signs."""
    t = (k + 0.5) / SEG_FRAMES
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
    box = list(media.content_box(master))
    mezz = work / "mezzanine.mp4"
    _mezzanine(master, mezz, p.has_audio, preset)
    expected = max(1, math.ceil(p.duration * FPS / SEG_FRAMES))

    m = round(MARGIN * SR)
    reader = media.AudioReader(mezz)
    before = np.zeros(m, np.float32)
    cur, nxt = reader.read(SEG_SAMPLES), reader.read(SEG_SAMPLES)
    frames = media.frames(mezz, WIDTH, HEIGHT, FPS)
    timing_meta, n_frames, seg, marked = [], 0, 0, 0
    enc_a = media.Encoder(work / "A", with_audio=p.has_audio, preset=preset)
    enc_b = media.Encoder(work / "B", with_audio=p.has_audio, preset=preset)
    # ponytail: one process, threads per frame (~37 min per hour of title on 16 cores); split segments across
    # processes if packaging throughput ever matters more than simplicity.
    pool = ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4))  # numpy and OpenCV release the GIL
    try:
        while True:
            chunk = [f for _, f in zip(range(SEG_FRAMES), frames)]
            if not chunk:
                break
            n_frames += len(chunk)
            if len(chunk) == SEG_FRAMES:
                x = np.concatenate([before, cur, nxt[:m]]).astype(np.float64)
                if p.has_audio:
                    disp = tcar.template(cur, SR)
                    wa, wb = tcar.warp_audio(x, SR, MARGIN, disp, +1), tcar.warp_audio(x, SR, MARGIN, disp, -1)
                    timing_meta.append(_shifts_json(tcar.realized(x, wa, wb, SR, MARGIN)))
                    aud_a = acar.embed(np.concatenate([before, wa, nxt[:m]]), SR, MARGIN, key, title_id, seg, +1)
                    aud_b = acar.embed(np.concatenate([before, wb, nxt[:m]]), SR, MARGIN, key, title_id, seg, -1)
                    map_a, map_b = tcar.frame_map(disp, +1), tcar.frame_map(disp, -1)
                else:
                    timing_meta.append(None)
                    aud_a = aud_b = None
                    map_a = map_b = np.arange(SEG_FRAMES)
                pcar.prepare(box, key, title_id, seg)  # patterns once, here, not raced by every worker
                for fa, fb in pool.map(lambda k: _mark_pair(chunk, map_a, map_b, k, box, key, title_id, seg),
                                       range(SEG_FRAMES)):
                    enc_a.write_frame(fa)
                    enc_b.write_frame(fb)
                if p.has_audio:
                    enc_a.write_audio(aud_a)
                    enc_b.write_audio(aud_b)
                marked += 1
            else:  # the final partial segment: unmarked, identical in A and B
                timing_meta.append(None)
                tail = cur[: len(chunk) * (SR // FPS)]
                for f in chunk:
                    enc_a.write_frame(f)
                    enc_b.write_frame(f)
                if p.has_audio:
                    enc_a.write_audio(tail)
                    enc_b.write_audio(tail)
            seg += 1
            before, cur, nxt = cur[-m:], nxt, reader.read(SEG_SAMPLES)
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

    meta = TitleMeta(id=title_id, name=name, duration=n_frames / FPS, n_frames=n_frames, n_segments=seg,
                       marked_segments=marked, box=box, has_audio=p.has_audio, timing=timing_meta,
                       budget=asdict(BUDGET), created=now())
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

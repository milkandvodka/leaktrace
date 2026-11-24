"""Tracing a leaked file back to an account.

1. Locate it: which lecture, offset, speed (audio landmarks; video keyframes for muted clips).
2. For every marked segment the clip fully covers:
   - timing: shifts measured against the unmarked master in 8 windows; their average also fine-aligns the rest;
   - audio: log band energies of leak vs master, on the rendition's own timeline;
   - picture: leak frames registered onto the master (ORB + homography) and compared at 320x180.
3. Fuse the nine carriers into one soft bit per segment, weighted by how well each agrees with the others.
4. Score every candidate (students who opened the lecture before the leak was first seen) with the Tardos
   score against simulated innocents; report a verdict only as strong as the family-wise bound allows.
"""
import os
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import media
from .carriers import audio as acar
from .carriers import picture as pcar
from .carriers import timing as tcar
from .codes import accuse
from .config import FPS, HEIGHT, SEG_FRAMES, SEG_SECONDS, WIDTH, settings
from .fuse import CARRIERS, fuse
from .ingest import MARGIN, LectureMeta
from .locate import Location, locate
from .store import Store, now

AUDIO_SR = 8000             # everything audio is read in the phone band
FRAME_STEP = 2              # picture: every 2nd frame of a segment (24 of 48)
QUICK_SEGMENTS = 240        # long leaks: an evenly spread first pass before reading everything
PICTURE_WORKERS = min(6, os.cpu_count() or 2)


@dataclass
class TraceReport:
    id: str
    leak: str
    created: str
    verdict: str                      # identified | narrowed | inconclusive | not_ours | no_candidates
    lecture: dict | None = None       # id, title
    location: dict | None = None
    segments_used: int = 0
    carriers: dict = field(default_factory=dict)   # name -> {"weight", "mean_z"}
    segments: list = field(default_factory=list)   # [{"seg", "y"}]
    suspects: list = field(default_factory=list)   # top suspects, best first
    coalition: list = field(default_factory=list)  # all identified accounts when more than one
    candidates: int = 0
    notice: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class _Frames:
    """Frames from a forward-only decoder, fetched by index in non-decreasing order."""

    def __init__(self, gen):
        self.gen, self.buf, self.next = gen, deque(), 0

    def get(self, idx: int) -> np.ndarray | None:
        while self.buf and self.buf[0][0] < idx - 30:
            self.buf.popleft()
        while self.next <= idx:
            f = next(self.gen, None)
            if f is None:
                break
            self.buf.append((self.next, f))
            self.next += 1
        for i, f in reversed(self.buf):
            if i <= idx:
                return f
        return self.buf[0][1] if self.buf else None

    def close(self):
        self.gen.close()


def _interp_frames(feat: np.ndarray, frame_pos: np.ndarray) -> np.ndarray:
    """Rows of feat sampled at fractional frame positions (linear), clamped at the ends."""
    pos = np.clip(frame_pos, 0, len(feat) - 1)
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, len(feat) - 1)
    f = (pos - lo)[:, None]
    return feat[lo] * (1 - f) + feat[hi] * f


def _feat_pos(t: np.ndarray) -> np.ndarray:  # time -> fractional index into timing.features rows
    return (t * AUDIO_SR - tcar.FEAT_FFT / 2) / tcar.FEAT_HOP


def _spec_pos(t: np.ndarray) -> np.ndarray:  # time -> fractional index into audio.spec rows
    return (t - acar.SPEC_WIN_S / 2) / acar.SPEC_HOP_S


def _notice(meta: LectureMeta, loc: Location, leak_name: str) -> str:
    start, end = loc.offset, loc.offset + loc.speed * loc.duration
    return (f"Copyright notice\n\nThe video \"{leak_name}\" reproduces our copyrighted lecture \"{meta.title}\" "
            f"(from {start:.0f} s to {end:.0f} s of the original) without permission. It was distributed from an "
            f"account licensed only for personal viewing, identified through forensic marks in the copy. "
            f"Please remove it and any copies.\n")


def _measure_segment(i, meta, loc, key, leak_feat, mezz_feat, leak_spec, mezz_spec, lf, mf, box, has_leak_audio,
                     has_leak_video, state) -> np.ndarray:
    z = np.zeros(len(CARRIERS))
    T = SEG_SECONDS
    seg0 = i * T
    residual, shift_at = 0.0, None
    real = meta.real_shifts(i)
    if has_leak_audio and meta.has_audio and real is not None:
        hop = tcar.FEAT_HOP / AUDIO_SR
        n = int(round((T + 2 * MARGIN) / hop)) + tcar.FEAT_FFT // tcar.FEAT_HOP
        master_t = seg0 - MARGIN + tcar.FEAT_FFT / 2 / AUDIO_SR + hop * np.arange(n)
        mz = _interp_frames(mezz_feat, _feat_pos(master_t))
        lk = _interp_frames(leak_feat, _feat_pos(loc.clip_time(master_t)))
        est, se = tcar.measure(lk, mz, real, MARGIN)
        z[0] = est / se if np.isfinite(se) else 0.0
        e = tcar.shifts(lk, mz, MARGIN)
        ok = ~np.isnan(e) & ~np.isnan(real).any(axis=0)
        if ok.sum() >= 2:
            residual = float(np.clip(np.median(e[ok] - real.mean(axis=0)[ok]), -0.06, 0.06))
            shift_at = lambda t, c=tcar.CENTRES[ok], v=e[ok]: np.interp(t, c, v)  # noqa: E731
        # audio carriers on the rendition timeline: leak where the rendition put it, master where the content is
        rho = acar.SPEC_WIN_S / 2 + acar.SPEC_HOP_S * np.arange(int(T / acar.SPEC_HOP_S) - 3)
        content = rho - (shift_at(rho) - residual if shift_at else 0.0)
        lk_spec = _interp_frames(leak_spec, _spec_pos(loc.clip_time(seg0 + rho + residual)))
        mz_spec = _interp_frames(mezz_spec, _spec_pos(seg0 + content))
        got = acar.measure(lk_spec, mz_spec, key, meta.id, i)
        for name, (est, se) in got.items():
            z[CARRIERS.index(name)] = est / se if np.isfinite(se) else 0.0

    if has_leak_video:
        ks = np.arange(0, SEG_FRAMES, FRAME_STEP)
        rho = (ks + 0.5) / FPS
        content = rho - (shift_at(rho) - residual if shift_at else 0.0)
        leak_idx = np.round(loc.clip_time(seg0 + rho + residual) * FPS - 0.5).astype(int)
        mezz_idx = np.round((seg0 + content) * FPS - 0.5).astype(int)
        leak_frames = [lf.get(int(j)) for j in leak_idx]
        mezz_frames = [mf.get(int(j)) for j in mezz_idx]
        if all(f is not None for f in leak_frames + mezz_frames):
            mid = len(ks) // 2
            reg = pcar.register(pcar.gray_small(leak_frames[mid]), pcar.gray_small(mezz_frames[mid]))
            reg = reg or state.get("reg")  # registration stays in order: a failed frame reuses the last geometry
            if reg is not None:
                state["reg"] = reg
                H, valid = reg
                t_fracs = list((ks + 0.5) / SEG_FRAMES)
                return z, lambda: _picture(leak_frames, mezz_frames, H, valid, t_fracs, key, meta.id, i, box)
    return z, None


def _picture(leak_frames, mezz_frames, H, valid, t_fracs, key, lecture, seg, box) -> dict:
    """The picture carriers of one segment; runs on a worker thread (numpy and OpenCV release the GIL)."""
    la = [pcar.warp(pcar.to_analysis(f), H) for f in leak_frames]
    ma = [pcar.to_analysis(f) for f in mezz_frames]
    return pcar.measure(la, ma, t_fracs, valid, key, lecture, seg, box)


def _collect(pending: deque, z_rows: dict) -> None:
    i, fut = pending.popleft()
    for name, (est, se) in fut.result().items():
        z_rows[i][CARRIERS.index(name)] = est / se if np.isfinite(se) else 0.0


def _lecture_folders() -> list[Path]:
    root = settings().data / "lectures"
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.endswith(".partial")) if root.exists() else []


def trace(leak: Path, store: Store, first_seen: str | None = None, candidates: list[str] | None = None,
          progress: Callable[[float], None] | None = None) -> TraceReport:
    leak = Path(leak)
    report = TraceReport(id=uuid.uuid4().hex[:12], leak=leak.name, created=now(), verdict="not_ours")
    loc = locate(leak, _lecture_folders())
    if loc is None or not loc.segments:
        store.add_trace(report.id, None, report.verdict, report.as_dict())
        return report
    folder = settings().data / "lectures" / loc.lecture_id
    meta = LectureMeta.load(folder)
    key = settings().secret
    report.lecture = {"id": meta.id, "title": meta.title}
    report.location = loc.as_dict()

    p = media.probe(leak)
    has_audio, has_video = p.has_audio, p.has_video
    mezz = folder / "mezzanine.mp4"
    leak_feat = mezz_feat = leak_spec = mezz_spec = None
    if has_audio and meta.has_audio:
        leak_audio, mezz_audio = media.audio(leak, AUDIO_SR), media.audio(mezz, AUDIO_SR)
        leak_feat, mezz_feat = tcar.features(leak_audio, AUDIO_SR), tcar.features(mezz_audio, AUDIO_SR)
        leak_spec, mezz_spec = acar.spec(leak_audio, AUDIO_SR), acar.spec(mezz_audio, AUDIO_SR)

    segs = loc.segments
    order = segs if len(segs) <= QUICK_SEGMENTS else sorted(set(np.linspace(0, len(segs) - 1, QUICK_SEGMENTS).round().astype(int)))
    passes = [sorted(segs[j] for j in order)] if len(segs) > QUICK_SEGMENTS else [segs]
    if len(segs) > QUICK_SEGMENTS:
        passes.append([s for s in segs if s not in set(passes[0])])

    pool = candidates if candidates is not None else (store.viewers(meta.id, before=first_seen) or store.student_ids())
    report.candidates = len(pool)
    z_rows: dict[int, np.ndarray] = {}
    done = 0
    for pass_segs in passes:
        lf = _Frames(media.frames(leak, WIDTH, HEIGHT, FPS)) if has_video else None
        mf = _Frames(media.frames(mezz, WIDTH, HEIGHT, FPS)) if has_video else None
        state: dict = {}
        pending: deque = deque()
        try:
            with ThreadPoolExecutor(max_workers=PICTURE_WORKERS) as workers:
                for i in pass_segs:
                    z_rows[i], job = _measure_segment(i, meta, loc, key, leak_feat, mezz_feat, leak_spec, mezz_spec,
                                                      lf, mf, tuple(meta.box), has_audio, has_video, state)
                    if job:
                        pending.append((i, workers.submit(job)))
                    while len(pending) > 2 * PICTURE_WORKERS:  # bound the frames held in memory
                        _collect(pending, z_rows)
                    done += 1
                    if progress:
                        progress(0.95 * done / len(segs))
                while pending:
                    _collect(pending, z_rows)
        finally:
            if lf:
                lf.close()
                mf.close()
        if _summarise(report, meta, key, z_rows, pool):
            break
    store.add_trace(report.id, meta.id, report.verdict, report.as_dict())
    if progress:
        progress(1.0)
    return report


def _summarise(report: TraceReport, meta: LectureMeta, key: bytes, z_rows: dict, pool: list[str]) -> bool:
    """Fill the report from the segments read so far; True when the verdict can't get any better."""
    segs = sorted(z_rows)
    Z = np.stack([z_rows[s] for s in segs])
    y_seen, weights = fuse(Z)
    y = np.zeros(meta.n_segments)
    y[segs] = y_seen
    report.segments_used = len(segs)
    report.carriers = {c: {"weight": round(weights[c], 3), "mean_z": round(float(np.mean(np.abs(Z[:, k]))), 2)}
                       for k, c in enumerate(CARRIERS)}
    report.segments = [{"seg": int(s), "y": round(float(v), 3)} for s, v in zip(segs, y_seen)]
    if not pool:
        report.verdict, report.suspects = "no_candidates", []
        return False
    suspects = accuse(key, meta.id, y, pool)
    report.suspects = [s.as_dict() for s in suspects[:10]]
    identified = [s.student for s in suspects if s.verdict == "identified"]
    report.coalition = identified if len(identified) > 1 else []
    report.verdict = suspects[0].verdict
    if report.verdict != "inconclusive" and report.location:
        report.notice = _notice(meta, Location(**report.location), report.leak)
    return report.verdict == "identified"

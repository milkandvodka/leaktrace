"""Which lecture a leaked clip comes from, and where: master time = offset + speed * clip time.

Audio first: landmark hashes vote for (lecture, offset) at each candidate speed, the winner is refit on its
consistent matches. Clips with no usable audio fall back to video: ORB keyframe matching gives a rough place,
then the clip's motion trace is slid against the lecture's to pin it to the frame.
"""
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import index, media
from .carriers.picture import gray_small
from .config import FP_SR, FPS, HEIGHT, SEG_SECONDS, WIDTH
from .ingest import LectureMeta

# A 0.02 grid from 0.75x to 2x, plus the speeds players actually offer; the best is then refined in 0.005 steps.
SPEEDS = np.array(sorted(set(np.round(np.arange(0.76, 2.0001, 0.02), 2)) | {0.75, 1.25, 1.75}))
MIN_VOTES = 12


@dataclass
class Location:
    lecture_id: str
    offset: float      # master seconds at clip time 0
    speed: float       # master seconds per clip second
    votes: int
    via: str           # "audio" or "video"
    duration: float    # clip seconds
    segments: list[int] = field(default_factory=list)  # marked segments the clip covers completely

    def master_time(self, clip_t):
        return self.offset + self.speed * np.asarray(clip_t)

    def clip_time(self, master_t):
        return (np.asarray(master_t) - self.offset) / self.speed

    def as_dict(self) -> dict:
        return asdict(self)


def _covered(meta: LectureMeta, offset: float, speed: float, duration: float) -> list[int]:
    start, end = offset, offset + speed * duration
    first = max(0, int(np.ceil(start / SEG_SECONDS - 1e-6)))
    last = min(meta.marked_segments, int(np.floor(end / SEG_SECONDS + 1e-6)))
    return list(range(first, last))


def _by_audio(leak: Path, lectures: list[Path], duration: float):
    t, f = index.peaks(media.audio(leak, FP_SR))
    if len(t) < 20:
        return None
    best = _vote(t, f, lectures, SPEEDS)
    if best is not None:  # refine the speed around the winner
        best = _vote(t, f, [best[2]], np.round(best[3] + np.arange(-0.015, 0.0151, 0.005), 3), best)
    if best is None or best[0] < MIN_VOTES or best[0] < 3 * best[1]:
        return None
    votes, _runner, folder, scale, q_t, m_t = best
    if len(np.unique(q_t)) >= 3:  # refit speed and offset on the consistent matches
        b, a = np.polyfit(q_t.astype(float), m_t.astype(float), 1)
    else:
        b, a = scale, float(np.median(m_t - q_t * scale))
    meta = LectureMeta.load(folder)
    offset, speed = a * index.FRAME_S, float(b)
    return Location(meta.id, offset, speed, votes, "audio", duration, _covered(meta, offset, speed, duration))


def _vote(t, f, lectures, speeds, best=None):
    """The (votes, runner-up votes, lecture, speed, clip frames, master frames) with the most consistent hits."""
    for scale in speeds:
        qh, qt = index.pairs(t, f, scale)
        if not len(qh):
            continue
        q_master = np.round(qt * scale).astype(np.int64)
        for folder in lectures:
            idx = index.get(folder)
            H, T = idx["hashes"], idx["times"]
            lo, hi = np.searchsorted(H, qh, "left"), np.searchsorted(H, qh, "right")
            counts = hi - lo
            total = int(counts.sum())
            if total < MIN_VOTES:
                continue
            starts = np.repeat(lo, counts) + (np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts))
            offs = T[starts] - np.repeat(q_master, counts)
            base = offs.min()
            hist = np.convolve(np.bincount(offs - base), [1, 1, 1], "same")
            k = int(np.argmax(hist))
            votes = int(hist[k])
            if best is None or votes > best[0]:
                rest = hist.copy()
                rest[max(0, k - 3): k + 4] = 0
                inl = np.abs(offs - (k + base)) <= 2
                best = (votes, int(rest.max(initial=0)), folder, float(scale),
                        np.repeat(qt, counts)[inl], T[starts][inl])
    return best


def _flann():
    return cv2.FlannBasedMatcher(dict(algorithm=6, table_number=6, key_size=12, multi_probe_level=1), dict(checks=64))


def _by_video(leak: Path, lectures: list[Path], duration: float):
    probe_fps = 2.0
    samples = [(n / probe_fps, gray_small(f)) for n, f in enumerate(media.frames(leak, WIDTH, HEIGHT, probe_fps))][:120]
    best = None
    for folder in lectures:
        idx = index.get(folder)
        if not len(idx["desc"]):
            continue
        matcher = _flann()
        matcher.add([idx["desc"]])
        matcher.train()
        hits = []  # (clip time, master time)
        for t, gray in samples:
            kp, d = index.orb().detectAndCompute(gray, None)
            if d is None or len(kp) < 12:
                continue
            good = [m[0] for m in matcher.knnMatch(d, k=2) if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
            if len(good) < 12:
                continue
            segs = idx["seg"][[m.trainIdx for m in good]]
            for s in np.bincount(segs).argsort()[::-1][:3]:
                sel = [m for m, sg in zip(good, segs) if sg == s]
                if len(sel) < 12:
                    continue
                src = np.float32([kp[m.queryIdx].pt for m in sel])
                dst = idx["pts"][[m.trainIdx for m in sel]]
                H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
                if H is not None and int(inl.sum()) >= 15:
                    hits.append((t, (s + 0.5) * SEG_SECONDS))
                    break
        if len(hits) >= 3:
            rough = float(np.median([m - c for c, m in hits]))
            if best is None or len(hits) > best[0]:
                best = (len(hits), folder, rough)
    if best is None:
        return None
    votes, folder, rough = best
    meta = LectureMeta.load(folder)
    offset = _refine_by_motion(leak, index.get(folder)["motion"], rough, duration)
    return Location(meta.id, offset, 1.0, votes, "video", duration, _covered(meta, offset, 1.0, duration))


def _refine_by_motion(leak: Path, master_motion: np.ndarray, rough: float, duration: float, search: float = 12.0) -> float:
    """Slide the clip's frame-to-frame motion against the lecture's and take the best frame offset."""
    clip, prev = [], None
    for n, f in enumerate(media.frames(leak, WIDTH, HEIGHT, FPS, dur=min(duration, 90.0))):
        mv, prev = index.motion(f[:HEIGHT], prev)
        clip.append(mv)
    c = np.float32(clip[1:])
    if len(c) < FPS or c.std() < 1e-3:
        return rough
    c = (c - c.mean()) / c.std()
    best, best_score = rough, -np.inf
    lo = int(round((rough - search) * FPS))
    for start in range(max(1, lo), int(round((rough + search) * FPS)) + 1):
        m = master_motion[start: start + len(c)]
        if len(m) < len(c) or m.std() < 1e-3:
            continue
        score = float(np.dot(c, (m - m.mean()) / m.std())) / len(c)
        if score > best_score:
            best, best_score = (start - 1) / FPS, score
    return best if best_score > 0.3 else rough


def locate(leak: Path, lectures: list[Path]) -> Location | None:
    """Find the clip among the packaged lectures (folders with fp.npz / kf.npz), or None if it isn't one."""
    lectures = [Path(f) for f in lectures if (Path(f) / "fp.npz").exists()]
    p = media.probe(leak)
    loc = _by_audio(leak, lectures, p.duration) if p.has_audio else None
    if loc is None and p.has_video:
        loc = _by_video(leak, lectures, p.duration)
    return loc

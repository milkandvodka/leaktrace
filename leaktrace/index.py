"""What leaks are looked up in, built from a lecture's unmarked mezzanine:

- fp.npz: audio landmarks (pairs of spectral peaks, Shazam-style) at 8 kHz, which survive phone microphones,
  room echo and voice codecs. Each pair hashes to (f1, f2 - f1, dt); dt is what a speed change scales.
- kf.npz: ORB features of each segment's middle frame, plus a per-frame motion trace, for clips with no audio.
"""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import maximum_filter

from . import media
from .carriers.picture import gray_small
from .config import FP_SR, FPS, HEIGHT, SEG_FRAMES, WIDTH

N_FFT, HOP = 512, 128
FRAME_S = HOP / FP_SR  # 16 ms per landmark frame
FAN, MAX_DT, MAX_DF = 3, 63, 63
PEAKS_PER_S = 25
F_LO, F_HI = 8, 220  # bins: ~125 Hz to ~3.4 kHz, the band a phone keeps
MOTION_W, MOTION_H = 80, 45
THUMB_W, THUMB_H = 64, 36   # sequence-matching thumbnails for clips with no audio
THUMB_FPS = 5               # one thumbnail every 0.2 s (every 5th frame at 25 fps)


def thumb(gray: np.ndarray) -> np.ndarray:
    """A small high-passed grey thumbnail (local contrast, where text lines live; lighting gradients drop out),
    unit length. Lecture-wide structure is removed later by subtracting the lecture's mean thumbnail."""
    t = cv2.resize(gray, (THUMB_W, THUMB_H), interpolation=cv2.INTER_AREA).astype(np.float32)
    t = (t - cv2.GaussianBlur(t, (0, 0), 2.0)).ravel()
    return t / (np.linalg.norm(t) + 1e-6)


def centred(thumbs: np.ndarray, mean: np.ndarray) -> np.ndarray:
    """Thumbnails minus the lecture's mean thumbnail (its shared template), back to unit length."""
    x = thumbs - mean
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-6)


def peaks(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Spectral peaks of 8 kHz mono audio: (frame index, frequency bin), sorted by frame."""
    x = np.asarray(x, np.float32)
    if len(x) < N_FFT:
        return np.zeros(0, int), np.zeros(0, int)
    frames = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP] * np.hanning(N_FFT).astype(np.float32)
    S = 20 * np.log10(np.abs(np.fft.rfft(frames, axis=1)) + 1e-6)
    is_peak = (maximum_filter(S, size=(9, 21), mode="constant", cval=-1e9) == S) & (S > np.percentile(S, 70))
    is_peak[:, :F_LO] = False
    is_peak[:, F_HI:] = False
    t, f = np.nonzero(is_peak)
    if len(t) == 0:
        return t, f
    strength = S[t, f]
    per_window = round(1 / FRAME_S)  # keep the strongest PEAKS_PER_S in every second
    keep = np.zeros(len(t), bool)
    for w in np.unique(t // per_window):
        idx = np.flatnonzero(t // per_window == w)
        keep[idx[np.argsort(-strength[idx])[:PEAKS_PER_S]]] = True
    order = np.lexsort((f[keep], t[keep]))
    return t[keep][order], f[keep][order]


def pairs(t: np.ndarray, f: np.ndarray, scale: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Landmark hashes and anchor frames. scale converts this clip's time to the master's (speed change):
    dt in the hash is the master-time distance, so a 1.25x clip still hashes like the original."""
    n = len(t)
    if n < 2:
        return np.zeros(0, np.uint32), np.zeros(0, np.int64)
    taken = np.zeros(n, int)
    hs, ts = [], []
    for k in range(1, 60):
        i = np.arange(n - k)
        j = i + k
        dt = np.round((t[j] - t[i]) * scale).astype(int)
        df = f[j] - f[i]
        ok = (dt >= 1) & (dt <= MAX_DT) & (np.abs(df) <= MAX_DF) & (taken[i] < FAN)
        if not ok.any():
            if (dt[: max(1, len(dt))] > MAX_DT).all():
                break
            continue
        ii = i[ok]
        taken[ii] += 1
        hs.append((f[ii].astype(np.uint32) << 13) | ((df[ok] + 64).astype(np.uint32) << 6) | dt[ok].astype(np.uint32))
        ts.append(t[ii])
    if not hs:
        return np.zeros(0, np.uint32), np.zeros(0, np.int64)
    return np.concatenate(hs), np.concatenate(ts).astype(np.int64)


_ORB = None


def orb():
    global _ORB
    _ORB = _ORB or cv2.ORB_create(nfeatures=500, fastThreshold=10)
    return _ORB


def motion(gray: np.ndarray, prev: np.ndarray | None) -> tuple[float, np.ndarray]:
    small = cv2.resize(gray, (MOTION_W, MOTION_H), interpolation=cv2.INTER_AREA).astype(np.float32)
    return (0.0 if prev is None else float(np.abs(small - prev).mean())), small


def build(folder: Path) -> None:
    folder = Path(folder)
    mezz = folder / "mezzanine.mp4"
    hashes, times = pairs(*peaks(media.audio(mezz, FP_SR)))
    order = np.argsort(hashes, kind="stable")
    np.savez_compressed(folder / "fp.npz", hashes=hashes[order], times=times[order])

    desc, seg_of, pts, moves, thumbs, prev = [], [], [], [], [], None
    for n, f in enumerate(media.frames(mezz, WIDTH, HEIGHT, FPS)):
        gray = gray_small(f)
        mv, prev = motion(gray, prev)
        moves.append(mv)
        if n % (FPS // THUMB_FPS) == 0:
            thumbs.append(thumb(gray))
        if n % SEG_FRAMES == SEG_FRAMES // 2:
            kp, d = orb().detectAndCompute(gray, None)
            if d is not None:
                desc.append(d)
                seg_of.append(np.full(len(d), n // SEG_FRAMES, np.int32))
                pts.append(np.float32([k.pt for k in kp]))
    np.savez_compressed(folder / "kf.npz",
                        desc=np.concatenate(desc) if desc else np.zeros((0, 32), np.uint8),
                        seg=np.concatenate(seg_of) if seg_of else np.zeros(0, np.int32),
                        pts=np.concatenate(pts) if pts else np.zeros((0, 2), np.float32),
                        motion=np.float32(moves),
                        thumbs=np.stack(thumbs).astype(np.float16) if thumbs else np.zeros((0, THUMB_W * THUMB_H), np.float16))


@lru_cache(maxsize=256)
def load(folder: str, stamp: float) -> dict:
    """A lecture's index, cached until its files change (stamp = modification time)."""
    fp, kf = np.load(Path(folder) / "fp.npz"), np.load(Path(folder) / "kf.npz")
    thumbs = kf["thumbs"].astype(np.float32)
    mean = thumbs.mean(axis=0) if len(thumbs) else np.zeros(THUMB_W * THUMB_H, np.float32)
    return {"hashes": fp["hashes"], "times": fp["times"], "desc": kf["desc"], "seg": kf["seg"], "pts": kf["pts"],
            "motion": kf["motion"], "thumbs": centred(thumbs, mean) if len(thumbs) else thumbs, "thumb_mean": mean}


def get(folder: Path) -> dict:
    return load(str(folder), (Path(folder) / "fp.npz").stat().st_mtime)

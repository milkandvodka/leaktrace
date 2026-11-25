"""The timing carrier: inside each segment, content is shifted later (A) or earlier (B) by up to 30 ms.

The shift is zero at both segment ends, so every segment keeps its exact length. It rises over the first half and
falls over the second, and the speed change that produces it is put mostly into pauses (up to 15 %), staying at
or under 5 % while someone is talking. Audio is re-timed with WSOLA (pitch unchanged); video frames are remapped.

Timing survives everything that wrecks the picture or the sound quality: cropping, blur, low resolution, a phone
filming the screen, an 8 kHz voice codec. It is read by aligning the leak against the unmarked master in short
windows and fitting the template to the measured shifts.
"""
from math import gcd

import numpy as np
from scipy.signal import resample_poly

from ..config import BUDGET

STEP = 0.01  # template resolution, seconds (about: a segment's points are spread evenly over its length)
FEAT_SR, FEAT_FFT, FEAT_HOP = 8000, 256, 16  # alignment features: 2 ms hop at 8 kHz
BANDS = np.geomspace(200, 3600, 17)
SILENCE_SLOPE = 0.15
WINDOWS, WINDOW_S, MAX_LAG_S = 8, 0.24, 0.06


def points(seg_s: float) -> int:
    """Template points for a segment of seg_s seconds, both ends included: about one per 10 ms, an odd count so
    the rise and the fall get the same number of steps (193 for 1.92 s, 201 for 2.0 and 2.002 s)."""
    return 2 * max(1, round(seg_s / STEP / 2)) + 1


def speech_mask(seg: np.ndarray, sr: int) -> np.ndarray:
    """True at each template point where someone is talking."""
    t = np.linspace(0, len(seg) / sr, points(len(seg) / sr))
    half = int(0.01 * sr)
    idx = np.clip((t * sr).astype(int), 0, len(seg) - 1)
    e = np.array([np.mean(seg[max(0, i - half): i + half] ** 2) for i in idx]) + 1e-12
    db = 10 * np.log10(e)
    if db.max() < -60:  # a silent segment: everything is pause
        return np.zeros(len(t), bool)
    return db > np.percentile(db, 95) - 30


def template(seg: np.ndarray, sr: int) -> np.ndarray:
    """Displacement in seconds at the segment's template points (both ends included): 0 at the ends, +30 ms peak."""
    talking = speech_mask(seg, sr)[:-1]
    cap = np.where(talking, BUDGET.max_speech_slope, SILENCE_SLOPE)
    half = len(talking) // 2
    u = (np.arange(half) + 0.5) / half
    taper = np.ones(half)  # Tukey taper: the speed change starts and ends gently
    edge = u < 0.125
    taper[edge] = 0.5 * (1 - np.cos(np.pi * u[edge] / 0.125))
    edge = u > 0.875
    taper[edge] = 0.5 * (1 - np.cos(np.pi * (1 - u[edge]) / 0.125))
    w = cap * np.tile(taper, 2)
    rise = BUDGET.warp_s * w[:half] / w[:half].sum()
    fall = -BUDGET.warp_s * w[half:] / w[half:].sum()
    return np.concatenate([[0.0], np.cumsum(np.concatenate([rise, fall]))])


def _content_time(disp: np.ndarray, sign: int, seg_s: float):
    """Map output time -> content (master) time inside the segment, for content shifted by sign * disp."""
    t = np.linspace(0, seg_s, len(disp))
    tau = t + sign * disp
    return lambda out_t: np.interp(out_t, tau, t)


def warp_audio(x: np.ndarray, sr: int, margin: float, disp: np.ndarray, sign: int) -> np.ndarray:
    """Re-time one segment. x holds the segment plus `margin` seconds of context each side, mono (n,) or with
    channels (n, c); returns the segment. Splice points are chosen on the mono mix and applied to every channel,
    so the channels stay locked together."""
    x = np.asarray(x, np.float64)
    mono = x if x.ndim == 1 else x.mean(axis=1)
    m = round(margin * sr)
    n = len(x) - 2 * m
    seg_s = n / sr
    to_content = _content_time(disp, sign, seg_s)
    length = round(0.030 * sr)
    hop = length // 2
    search = round(0.005 * sr)
    dec = max(1, sr // 12000)
    win = np.hanning(length + 1)[:-1]  # periodic Hann: overlapping halves sum to exactly 1
    if x.ndim > 1:
        win = win[:, None]
    y = np.zeros((len(x) + length, *x.shape[1:]))
    start, prev = m - length, None
    for j in range((n + 2 * length) // hop + 1):
        o = start + j * hop
        centre = (o + length / 2 - m) / sr
        src = to_content(centre) * sr + m if 0 < centre < seg_s else o + length / 2
        pos = int(round(src - length / 2))
        if prev is not None and pos != prev + hop:
            natural = min(max(prev + hop, 0), len(x) - length)
            lo, hi = max(0, pos - search), min(len(x) - length, pos + search)
            ref = mono[natural: natural + length: dec]
            region = mono[lo: hi + length]
            cands = np.lib.stride_tricks.sliding_window_view(region, length)[:: dec, :: dec]
            norms = np.linalg.norm(cands, axis=1) * (np.linalg.norm(ref) + 1e-12) + 1e-12
            pos = lo + int(np.argmax(cands @ ref / norms)) * dec
        pos = min(max(pos, 0), len(x) - length)
        y[o: o + length] += x[pos: pos + length] * win
        prev = pos
    return y[m: m + n]


def frame_map(disp: np.ndarray, sign: int, fps, seg_frames: int) -> np.ndarray:
    """For each output frame of a segment, the source frame to show (content shifted by sign * disp)."""
    fps = float(fps)
    to_content = _content_time(disp, sign, seg_frames / fps)
    t = to_content((np.arange(seg_frames) + 0.5) / fps)
    return np.clip(np.round(t * fps - 0.5).astype(int), 0, seg_frames - 1)


def features(x: np.ndarray, sr: int) -> np.ndarray:
    """Onset-like features for alignment: 16 log band energies (200-3600 Hz) every 2 ms at 8 kHz, with slow
    level changes removed and each band scaled to unit variance, so equalisers, gain and codecs matter little."""
    g = gcd(FEAT_SR, sr)
    z = resample_poly(np.asarray(x, np.float64), FEAT_SR // g, sr // g)
    if len(z) < FEAT_FFT:
        z = np.pad(z, (0, FEAT_FFT - len(z)))
    frames = np.lib.stride_tricks.sliding_window_view(z, FEAT_FFT)[::FEAT_HOP] * np.hanning(FEAT_FFT)
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(FEAT_FFT, 1 / FEAT_SR)
    bands = np.stack([power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) for lo, hi in zip(BANDS[:-1], BANDS[1:])], 1)
    logb = np.log10(bands + 1e-10)
    k = 100  # 0.2 s moving average removed: keeps onsets, drops slow level and EQ changes
    kernel = np.ones(k) / k
    trend = np.stack([np.convolve(np.pad(b, (k // 2, k - 1 - k // 2), mode="edge"), kernel, "valid") for b in logb.T], 1)
    hp = logb - trend
    return hp / (hp.std(axis=0, keepdims=True) + 1e-9)


def _frame(t: float) -> int:
    return int(round((t * FEAT_SR - FEAT_FFT / 2) / FEAT_HOP))


def centres(seg_s: float) -> np.ndarray:
    """Where in the segment (seconds) the alignment windows sit."""
    return (np.arange(WINDOWS) + 0.5) * seg_s / WINDOWS


def shifts(leak_feat: np.ndarray, mezz_feat: np.ndarray, margin: float, seg_s: float) -> np.ndarray:
    """How far the leak's content sits from the master's at each window centre, in seconds (NaN where the
    window can't be aligned: silence, or the leak doesn't cover it)."""
    hop = FEAT_HOP / FEAT_SR
    width, max_lag = round(WINDOW_S / hop), round(MAX_LAG_S / hop)
    out = np.full(WINDOWS, np.nan)
    for k, c in enumerate(centres(seg_s)):
        f0 = _frame(margin + c - WINDOW_S / 2)
        ref = mezz_feat[f0: f0 + width]
        if f0 - max_lag < 0 or f0 + width + max_lag > len(leak_feat) or len(ref) < width:
            continue
        ref_norm = np.linalg.norm(ref, axis=0)
        scores = np.array([np.mean(np.sum(ref * w, axis=0) / (ref_norm * np.linalg.norm(w, axis=0) + 1e-9))
                           for w in (leak_feat[f0 + lag: f0 + lag + width] for lag in range(-max_lag, max_lag + 1))])
        i = int(np.argmax(scores))
        if scores[i] < 0.35 or i in (0, len(scores) - 1):
            continue
        a, b, cc = scores[i - 1], scores[i], scores[i + 1]
        frac = 0.5 * (a - cc) / (a - 2 * b + cc) if (a - 2 * b + cc) != 0 else 0.0
        out[k] = (i - max_lag + frac) * hop
    return out


def realized(x: np.ndarray, seg_a: np.ndarray, seg_b: np.ndarray, sr: int, margin: float) -> np.ndarray:
    """The shifts renditions A and B really contain (WSOLA follows the template only to within a few ms),
    measured at packaging time on clean audio: shape (2, WINDOWS). Detection compares a leak against these."""
    m = round(margin * sr)
    mezz = features(x, sr)
    return np.stack([shifts(features(np.concatenate([x[:m], seg, x[len(x) - m:]]), sr), mezz, margin, len(seg_a) / sr)
                     for seg in (seg_a, seg_b)])


def measure(leak_feat: np.ndarray, mezz_feat: np.ndarray, real: np.ndarray, margin: float,
            seg_s: float) -> tuple[float, float]:
    """Which rendition the leak's timing matches. Returns (estimate, standard error): about +1 for A, -1 for B.

    Fits shift = a + b * t + mid + beta * half over the windows, with mid/half the average and half-difference of
    A's and B's real shifts; a and b soak up the leftover offset and speed error of the rough alignment. Huber
    reweighting stops one badly aligned window from dragging the answer."""
    e = shifts(leak_feat, mezz_feat, margin, seg_s)
    ok = ~np.isnan(e) & ~np.isnan(real).any(axis=0)
    if ok.sum() < 4:
        return 0.0, float("inf")
    c, mid, half = centres(seg_s)[ok], real.mean(axis=0)[ok], (real[0] - real[1])[ok] / 2
    X = np.stack([np.ones_like(c), c, half], 1)
    y = e[ok] - mid
    w = np.ones(len(y))
    for _ in range(3):
        sw = np.sqrt(w)
        beta, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
        r = y - X @ beta
        scale = max(1.4826 * float(np.median(np.abs(r))), 0.0015)
        w = np.minimum(1.0, 1.5 * scale / (np.abs(r) + 1e-12))
    var = max(float(np.sum(w * r ** 2)) / max(1.0, float(w.sum()) - 3), 0.0015 ** 2)
    cov = var * np.linalg.pinv((X * w[:, None]).T @ X)
    return float(beta[2]), float(np.sqrt(cov[2, 2]))

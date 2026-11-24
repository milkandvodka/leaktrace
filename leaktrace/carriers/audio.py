"""Audio carriers, all in the speech band so they survive phone microphones and 8 kHz voice codecs:

- patchwork: a keyed +/- pattern of small gains on 80 ms x 200 Hz tiles between 300 and 3300 Hz;
- loudness: the whole segment slowly swells and dips by a fraction of a dB, following the temporal code;
- tilt: highs up and lows down (or the reverse), following the temporal code.

They are applied after the timing warp, on the rendition's own timeline. They're measured on log band energies
of the leak minus those of the unmarked master, once the master has been lined up with the leak.
"""
import numpy as np
from scipy.signal import istft, stft

from ..config import BUDGET, SEG_SECONDS
from .patterns import code, rng, tukey

TILE_S, BAND_HZ, LO_HZ, HI_HZ = 0.08, 200, 300, 3300
N_TILES = round(SEG_SECONDS / TILE_S)  # 24
N_BANDS = (HI_HZ - LO_HZ) // BAND_HZ   # 15
SPEC_WIN_S, SPEC_HOP_S = 0.032, 0.010
DECOYS = 16
TILT_HZ = 900
_CENTRES = LO_HZ + BAND_HZ * (np.arange(N_BANDS) + 0.5)
_LOW, _HIGH = _CENTRES < TILT_HZ, _CENTRES > TILT_HZ


def _pattern(key: bytes, lecture: str, seg: int, decoy: int | None = None) -> np.ndarray:
    """+/-1 per tile, balanced so that in every time tile the low bands sum to 0 and the high bands sum to 0
    (a group with an odd count gets one 0). Patchwork then adds nothing to the loudness or tilt measurements."""
    g = rng(key, *(("patch", lecture, seg) if decoy is None else ("patch-decoy", lecture, seg, decoy)))
    W = np.zeros((N_TILES, N_BANDS))
    for group in (np.flatnonzero(_LOW), np.flatnonzero(_HIGH)):
        k = len(group)
        base = np.array([1.0] * (k // 2) + [-1.0] * (k // 2) + [0.0] * (k % 2))
        for t in range(N_TILES):
            W[t, group] = g.permutation(base)
    return W


def _flat_top(frac: np.ndarray) -> np.ndarray:
    """Weight of the next tile between two tile centres: 0 until 30 % of the way, 1 after 70 %, smooth between."""
    r = np.clip((frac - 0.3) / 0.4, 0, 1)
    return 0.5 - 0.5 * np.cos(np.pi * r)


def embed(x: np.ndarray, sr: int, margin: float, key: bytes, lecture: str, seg: int, sign: int) -> np.ndarray:
    """Apply all three carriers to the segment in x (which has `margin` seconds of context each side)."""
    m = round(margin * sr)
    n = len(x) - 2 * m
    nfft = 2048 if sr >= 32000 else 512
    hop = nfft // 4
    f, t, Z = stft(x, sr, window="hann", nperseg=nfft, noverlap=nfft - hop, boundary="even", padded=True)
    u = (t - margin) / SEG_SECONDS  # 0..1 across the segment
    inside = (u > 0) & (u < 1)

    # patchwork: tile signs on the STFT grid, flat across each tile with a short crossfade at tile edges
    W = _pattern(key, lecture, seg)
    ti = np.clip(u * N_TILES - 0.5, 0, N_TILES - 1)
    bi = (f - LO_HZ) / BAND_HZ - 0.5
    in_band = (f >= LO_HZ - BAND_HZ / 2) & (f <= HI_HZ + BAND_HZ / 2)
    bi = np.clip(bi, 0, N_BANDS - 1)
    t0, b0 = np.floor(ti).astype(int), np.floor(bi).astype(int)
    t1, b1 = np.minimum(t0 + 1, N_TILES - 1), np.minimum(b0 + 1, N_BANDS - 1)
    ft, fb = _flat_top(ti - t0), _flat_top(bi - b0)
    smooth = ((1 - fb)[:, None] * ((1 - ft) * W[t0][:, b0].T + ft * W[t1][:, b0].T)
              + fb[:, None] * ((1 - ft) * W[t0][:, b1].T + ft * W[t1][:, b1].T))
    db = BUDGET.patchwork_db * smooth * in_band[:, None] * (tukey(u) * inside)[None, :]

    # tilt: +half above 900 Hz, -half below, following the temporal code
    shape = np.tanh((f - TILT_HZ) / 150)[:, None]
    db = db + (BUDGET.tilt_db / 2) * shape * (code(u) * inside)[None, :]

    _, y = istft(Z * 10 ** (sign * db / 20), sr, window="hann", nperseg=nfft, noverlap=nfft - hop, boundary=True)
    y = np.asarray(y[: len(x)], np.float64)
    if len(y) < len(x):
        y = np.pad(y, (0, len(x) - len(y)))
    seg_y = y[m: m + n]

    # loudness: a slow swell and dip over the segment
    gain = 10 ** (sign * BUDGET.loudness_db * code((np.arange(n) + 0.5) / n) / 20)
    return seg_y * gain


def spec(x: np.ndarray, sr: int) -> np.ndarray:
    """Log energies (dB) in the 15 bands between 300 and 3300 Hz, every 10 ms. Defined in seconds and hertz,
    so a leak at 8 kHz lines up with a master at 48 kHz."""
    win, hop = round(SPEC_WIN_S * sr), round(SPEC_HOP_S * sr)
    nfft = int(2 ** np.ceil(np.log2(win)))
    x = np.asarray(x, np.float64)
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop] * np.hanning(win)
    power = np.abs(np.fft.rfft(frames, n=nfft, axis=1)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    edges = LO_HZ + BAND_HZ * np.arange(N_BANDS + 1)
    bands = np.stack([power[:, (freqs >= a) & (freqs < b)].sum(axis=1) for a, b in zip(edges[:-1], edges[1:])], 1)
    return 10 * np.log10(bands + 1e-12)


def _frame_times(n: int) -> np.ndarray:
    return SPEC_WIN_S / 2 + SPEC_HOP_S * np.arange(n)


def _fit_code(v: np.ndarray, u: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    """Weighted fit v = a + b u + beta * code(u); returns (beta, standard error)."""
    X = np.stack([np.ones_like(u), u, code(u)], 1)
    sw = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], v * sw, rcond=None)
    r = v - X @ beta
    var = float(np.sum(w * r ** 2)) / max(1.0, float(w.sum()) - 3)
    cov = max(var, 1e-6) * np.linalg.pinv((X * w[:, None]).T @ X)
    return float(beta[2]), float(np.sqrt(cov[2, 2]))


def measure(leak_spec: np.ndarray, mezz_spec: np.ndarray, key: bytes, lecture: str, seg: int) -> dict:
    """Evidence from each audio carrier as (estimate, standard error); estimate / error is a z-score, positive
    for rendition A. Both spectra cover just this segment and are already on the same timeline."""
    n = min(len(leak_spec), len(mezz_spec))
    mz = mezz_spec[:n]
    # Only cells where the master has real energy count (digital silence makes log differences explode), and
    # each cell's difference is clamped so one transient or codec glitch can't outweigh the rest.
    cell = np.clip((mz - (mz.max() - 45)) / 20, 0, 1)  # 0 below -45 dB, full weight from -25 dB up
    D = np.clip(leak_spec[:n] - mz, -6, 6) * cell
    u = _frame_times(n) / SEG_SECONDS
    per_frame = cell.sum(axis=1)
    w = (per_frame >= N_BANDS // 2).astype(np.float64)  # frames where someone is talking across most bands
    if w.sum() < 10:
        return {name: (0.0, float("inf")) for name in ("patchwork", "loudness", "tilt")}

    # patchwork: average D per tile, remove each tile row's and column's mean, correlate with the keyed pattern
    tile = np.clip((u * N_TILES).astype(int), 0, N_TILES - 1)
    sums, counts = np.zeros((N_TILES, N_BANDS)), np.zeros((N_TILES, N_BANDS))
    np.add.at(sums, tile, D)
    np.add.at(counts, tile, cell)
    valid = counts.min(axis=1) > 0
    T = sums[valid] / counts[valid]
    T = T - T.mean(axis=0, keepdims=True) - T.mean(axis=1, keepdims=True) + T.mean()
    env = tukey((np.arange(N_TILES) + 0.5) / N_TILES)[valid][:, None]
    P = _pattern(key, lecture, seg)[valid] * env
    stat = float(np.sum(T * P))
    # Noise comes from decoy patterns (keys never embedded) correlated with what's left once the real pattern's
    # fitted contribution is removed; otherwise a strong mark inflates its own noise estimate.
    resid = T - stat / max(float(np.sum(P * P)), 1e-12) * P
    decoys = np.array([float(np.sum(resid * _pattern(key, lecture, seg, j)[valid] * env)) for j in range(DECOYS)])
    patch = (stat - decoys.mean(), float(decoys.std(ddof=1)) or float("inf"))

    def band_mean(cols):
        return D[:, cols].sum(axis=1) / np.maximum(cell[:, cols].sum(axis=1), 1)

    # low and high groups weigh equally, so the (symmetric) tilt cancels out of the loudness reading
    loud_b, loud_e = _fit_code((band_mean(_LOW) + band_mean(_HIGH)) / 2, u, w)
    tilt_b, tilt_e = _fit_code(band_mean(_HIGH) - band_mean(_LOW), u, w)
    return {"patchwork": patch,
            "loudness": (loud_b / BUDGET.loudness_db, loud_e / BUDGET.loudness_db),
            "tilt": (tilt_b / BUDGET.tilt_db, tilt_e / BUDGET.tilt_db)}

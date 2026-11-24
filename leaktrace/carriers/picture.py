"""Picture carriers. Each is a smooth keyed pattern across the content area (12 low-frequency cosine products, so
it survives blur, downscaling and a phone camera), scaled over the segment by the temporal code:

- brightness: luma + pattern;
- contrast:   luma moves away from or towards mid-grey, by pattern (local contrast);
- tint:       Cb and Cr + their own patterns (hue and saturation shift everywhere, grey slides included);
- saturation: chroma radius x (1 + pattern), where there is colour;
- hue:        chroma angle + pattern, where there is colour.

Luma never changes by more than 2 code values. Letterbox and pillarbox bars are never touched.

Measuring works at 320x180. The leak's frames are first warped onto the master's geometry (register/warp). A
per-segment colour model (each leak channel against all three master channels, plus linear drift over the
segment) removes the camera's gain, offset, colour cast and slow auto-exposure. The temporal code is symmetric
and zero-mean, so none of that touches the carriers. What remains is correlated with each carrier's pattern.
"""
from functools import lru_cache

import cv2
import numpy as np

from ..config import ANALYSIS_H, ANALYSIS_W, BUDGET, HEIGHT, WIDTH
from .patterns import code, rng

NAMES = ("brightness", "tint", "saturation", "hue", "contrast")
TERMS, DECOYS, MAX_DY = 12, 16, 2.0
_REF = 64  # patterns are normalised on a fixed 64x64 grid, so every resolution gets the same amplitude


def _params(key: bytes, name: str, lecture: str, seg: int, decoy: int | None):
    # Decoys only need to be independent of the real marks, so one set serves every segment of a lecture
    # (generated once per trace instead of per segment).
    g = rng(key, "pic", name, lecture, seg) if decoy is None else rng(key, "pic-decoy", name, lecture, decoy)
    return (g.integers(1, 7, TERMS), g.integers(1, 7, TERMS), g.uniform(0, 2 * np.pi, TERMS),
            g.uniform(0, 2 * np.pi, TERMS), g.choice([-1.0, 1.0], TERMS))


def _eval(params, xn: np.ndarray, yn: np.ndarray) -> np.ndarray:
    kx, ky, px, py, a = params
    cx = np.cos(np.pi * kx[:, None] * xn[None, :] + px[:, None])  # (TERMS, w)
    cy = np.cos(np.pi * ky[:, None] * yn[None, :] + py[:, None])  # (TERMS, h)
    return np.einsum("t,th,tw->hw", a, cy, cx)


@lru_cache(maxsize=512)
def pattern(key: bytes, name: str, lecture: str, seg: int, decoy: int | None, w: int, h: int,
            box: tuple[int, int, int, int]) -> np.ndarray:
    """A carrier's pattern on a w x h grid of the WIDTH x HEIGHT canvas, in [-1, 1] inside the content box and
    0 outside it. The same pattern at any resolution, because it is defined on the content's own coordinates."""
    params = _params(key, name, lecture, seg, decoy)
    ref = np.linspace(0, 1, _REF)
    peak = np.abs(_eval(params, ref, ref)).max() or 1.0
    bx, by, bw, bh = box
    xn = ((np.arange(w) + 0.5) * WIDTH / w - bx) / bw
    yn = ((np.arange(h) + 0.5) * HEIGHT / h - by) / bh
    p = _eval(params, xn, yn) / peak
    p *= ((yn >= 0) & (yn <= 1))[:, None] & ((xn >= 0) & (xn <= 1))[None, :]
    p = p.astype(np.float32)
    p.setflags(write=False)
    return p


def _planes(yuv: np.ndarray):
    h, w = HEIGHT, WIDTH
    return (yuv[:h], yuv[h: h + h // 4].reshape(h // 2, w // 2), yuv[h + h // 4:].reshape(h // 2, w // 2))


@lru_cache(maxsize=64)
def _gains(key: bytes, lecture: str, seg: int, box: tuple[int, int, int, int]):
    """Each carrier's per-pixel strength for one segment: brightness and contrast on the luma grid; saturation,
    hue and tint on the chroma grid. Computed once per segment, used for every frame."""
    lp = lambda n: pattern(key, n, lecture, seg, None, WIDTH, HEIGHT, box)  # noqa: E731
    cp = lambda n: pattern(key, n, lecture, seg, None, WIDTH // 2, HEIGHT // 2, box)  # noqa: E731
    return (BUDGET.brightness * lp("brightness"), BUDGET.contrast * lp("contrast"), BUDGET.saturation * cp("saturation"),
            np.float32(np.deg2rad(BUDGET.hue_deg)) * cp("hue"), BUDGET.tint * cp("tint_u"), BUDGET.tint * cp("tint_v"))


def prepare(box, key: bytes, lecture: str, seg: int) -> None:
    """Compute a segment's patterns ahead of marking its frames in parallel."""
    _gains(key, lecture, seg, tuple(int(v) for v in box))


def delta(yuv: np.ndarray, t_frac: float, box, key: bytes, lecture: str, seg: int) -> np.ndarray | None:
    """The change rendition A applies to this frame (B applies the opposite), as a float32 array shaped like the
    frame; None at a point in the segment where the temporal code is zero. Saturation and hue are linearised: with
    at most 3 % and 1.5 degrees the dropped second-order terms are below 0.05 code values."""
    s = np.float32(code(t_frac))
    if s == 0:
        return None
    kb, kc, ks, kh, ku, kv = _gains(key, lecture, seg, tuple(int(v) for v in box))
    Y, U, V = _planes(yuv)
    h, w = HEIGHT, WIDTH
    d = np.empty(yuv.shape, np.float32)
    dY, dU, dV = d[:h], d[h: h + h // 4].reshape(h // 2, w // 2), d[h + h // 4:].reshape(h // 2, w // 2)
    np.subtract(Y, np.float32(128), out=dY, dtype=np.float32)  # in place: a frame is 1.4 M values
    dY *= kc
    dY += kb
    dY *= s
    np.clip(dY, -MAX_DY, MAX_DY, out=dY)
    uc = np.subtract(U, np.float32(128), dtype=np.float32)
    vc = np.subtract(V, np.float32(128), dtype=np.float32)
    np.multiply(ks, uc, out=dU)
    dU -= kh * vc
    dU += ku
    dU *= s
    np.multiply(ks, vc, out=dV)
    dV += kh * uc
    dV += kv
    dV *= s
    return d


def apply(yuv: np.ndarray, d: np.ndarray | None, sign: int) -> np.ndarray:
    """Frame + d (A) or frame - d (B), rounded and saturated to 0..255 in one vectorised pass."""
    if d is None:
        return yuv.copy()
    op = cv2.add if sign > 0 else cv2.subtract
    return op(yuv, d, dtype=cv2.CV_8U)


def embed(yuv: np.ndarray, t_frac: float, box, key: bytes, lecture: str, seg: int, sign: int) -> np.ndarray:
    """One frame (YUV420, WIDTH x HEIGHT) at position t_frac (0..1) inside segment `seg`, marked for rendition
    A (sign +1) or B (sign -1). Returns a new array."""
    return apply(yuv, delta(yuv, t_frac, box, key, lecture, seg), sign)


def to_analysis(yuv: np.ndarray) -> np.ndarray:
    """(3, ANALYSIS_H, ANALYSIS_W) float32: Y, U, V scaled down to the measuring size."""
    return np.stack([cv2.resize(p, (ANALYSIS_W, ANALYSIS_H), interpolation=cv2.INTER_AREA).astype(np.float32)
                     for p in _planes(yuv)])


def gray_small(yuv: np.ndarray) -> np.ndarray:
    """Luma at half size (640x360) for feature-based registration."""
    return cv2.resize(yuv[:HEIGHT], (WIDTH // 2, HEIGHT // 2), interpolation=cv2.INTER_AREA)


_ORB = None


def register(leak_gray: np.ndarray, mezz_gray: np.ndarray):
    """Homography taking the leak's analysis-size frame onto the master's, and the mask of master pixels the
    leak covers. None when the frames can't be matched (fewer than 12 consistent features)."""
    global _ORB
    _ORB = _ORB or cv2.ORB_create(nfeatures=3000, fastThreshold=10)
    k1, d1 = _ORB.detectAndCompute(leak_gray, None)
    k2, d2 = _ORB.detectAndCompute(mezz_gray, None)
    if d1 is None or d2 is None or len(k1) < 12 or len(k2) < 12:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [m for m, *rest in pairs if rest and m.distance < 0.75 * rest[0].distance]
    if len(good) < 12:
        return None
    src = np.float32([k1[m.queryIdx].pt for m in good])
    dst = np.float32([k2[m.trainIdx].pt for m in good])
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if H is None or int(inl.sum()) < 12:
        return None
    scale = np.diag([ANALYSIS_W / leak_gray.shape[1], ANALYSIS_H / leak_gray.shape[0], 1.0])
    scale_m = np.diag([ANALYSIS_W / mezz_gray.shape[1], ANALYSIS_H / mezz_gray.shape[0], 1.0])
    Ha = scale_m @ H @ np.linalg.inv(scale)
    valid = cv2.warpPerspective(np.ones((ANALYSIS_H, ANALYSIS_W), np.float32), Ha, (ANALYSIS_W, ANALYSIS_H)) > 0.999
    valid = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    return Ha, valid


def warp(frame: np.ndarray, H: np.ndarray) -> np.ndarray:
    """An analysis-size (3, h, w) frame warped by H onto the master's geometry."""
    return np.stack([cv2.warpPerspective(c, H, (ANALYSIS_W, ANALYSIS_H), flags=cv2.INTER_LINEAR) for c in frame])


def _box_mask(box) -> np.ndarray:
    bx, by, bw, bh = box
    xs = (np.arange(ANALYSIS_W) + 0.5) * WIDTH / ANALYSIS_W
    ys = (np.arange(ANALYSIS_H) + 0.5) * HEIGHT / ANALYSIS_H
    inner = 6  # canvas pixels kept away from the box edge
    return (((ys > by + inner) & (ys < by + bh - inner))[:, None]) & (((xs > bx + inner) & (xs < bx + bw - inner))[None, :])


def _photometric_residual(L: np.ndarray, M: np.ndarray, tau: np.ndarray) -> np.ndarray:
    """Leak minus what the master predicts through a per-segment colour model (each channel from all three
    master channels plus an offset, each allowed to drift linearly over the segment). L, M: (n, 3, m)."""
    n, _, m = L.shape
    reg = np.concatenate([M, np.ones((n, 1, m), np.float32)], axis=1)  # (n, 4, m)
    X = np.concatenate([reg, reg * tau[:, None, None]], axis=1)       # (n, 8, m)
    sub = slice(None, None, 3)
    Xs = X[:, :, sub].transpose(1, 0, 2).reshape(8, -1).astype(np.float64)
    G = Xs @ Xs.T + 1e-6 * np.eye(8)
    R = np.empty_like(L)
    for c in range(3):
        beta = np.linalg.solve(G, Xs @ L[:, c, sub].reshape(-1).astype(np.float64))
        R[:, c] = L[:, c] - np.einsum("k,nkm->nm", beta.astype(np.float32), X)
    return R


def measure(leak: list[np.ndarray], mezz: list[np.ndarray], t_fracs: list[float], valid: np.ndarray,
            key: bytes, lecture: str, seg: int, box) -> dict:
    """Evidence from each picture carrier as (estimate, standard error), estimate ~ +1 for A and -1 for B.
    leak and mezz are analysis-size (3, h, w) frames already on the master's geometry; t_fracs their positions
    in the segment; valid the master pixels the leak really covers."""
    box = tuple(int(v) for v in box)
    keep = np.flatnonzero((valid & _box_mask(box)).ravel())
    if len(keep) < 500 or len(leak) < 6:
        return {n: (0.0, float("inf")) for n in NAMES}
    L = np.stack(leak).reshape(len(leak), 3, -1)[:, :, keep].astype(np.float32)
    M = np.stack(mezz).reshape(len(mezz), 3, -1)[:, :, keep].astype(np.float32)
    t = np.asarray(t_fracs, np.float32)
    s = code(t).astype(np.float32)
    R = _photometric_residual(L, M, t - 0.5)
    my, mu, mv = M[:, 0] - 128, M[:, 1] - 128, M[:, 2] - 128
    # Every template is s(t) * P(x) * (content factor), so all inner products collapse to per-pixel maps summed
    # over the segment's frames; decoys then cost a few dot products each.
    s1, s2 = s[:, None], (s ** 2)[:, None]
    mp = {
        "S0": np.full(len(keep), float(s2.sum()), np.float32), "Smy": (s2 * my).sum(0), "Smy2": (s2 * my ** 2).sum(0),
        "Smu": (s2 * mu).sum(0), "Smv": (s2 * mv).sum(0), "Sc2": (s2 * (mu ** 2 + mv ** 2)).sum(0),
        "AY": (s1 * R[:, 0]).sum(0), "AYm": (s1 * R[:, 0] * my).sum(0), "AU": (s1 * R[:, 1]).sum(0),
        "AV": (s1 * R[:, 2]).sum(0), "AS": (s1 * (R[:, 1] * mu + R[:, 2] * mv)).sum(0),
        "AH": (s1 * (R[:, 2] * mu - R[:, 1] * mv)).sum(0),
    }

    def pat(name, decoy=None):
        return pattern(key, name, lecture, seg if decoy is None else -1, decoy, ANALYSIS_W, ANALYSIS_H, box).ravel()[keep]

    def pats(carrier, decoy=None):
        return (pat("tint_u", decoy), pat("tint_v", decoy)) if carrier == "tint" else (pat(carrier, decoy),)

    def dot(*arrays):
        out = arrays[0]
        for a in arrays[1:]:
            out = out * a
        return float(out.sum())

    def rhs(k, P):
        if k == "brightness":
            return dot(P[0], mp["AY"])
        if k == "contrast":
            return dot(P[0], mp["AYm"])
        if k == "tint":
            return dot(P[0], mp["AU"]) + dot(P[1], mp["AV"])
        return dot(P[0], mp["AS" if k == "saturation" else "AH"])

    def ip(k, Pk, l, Pl):  # <template k with patterns Pk, template l with patterns Pl>
        a, b = sorted([(k, Pk), (l, Pl)], key=lambda e: NAMES.index(e[0]))
        (k, Pk), (l, Pl) = a, b
        luma, chroma = {"brightness", "contrast"}, {"tint", "saturation", "hue"}
        if (k in luma) != (l in luma):
            return 0.0
        if k == l == "brightness":
            return dot(Pk[0], Pl[0], mp["S0"])
        if (k, l) == ("brightness", "contrast"):
            return dot(Pk[0], Pl[0], mp["Smy"])
        if k == l == "contrast":
            return dot(Pk[0], Pl[0], mp["Smy2"])
        if k == l == "tint":
            return dot(Pk[0], Pl[0], mp["S0"]) + dot(Pk[1], Pl[1], mp["S0"])
        if (k, l) == ("tint", "saturation"):
            return dot(Pl[0], Pk[0], mp["Smu"]) + dot(Pl[0], Pk[1], mp["Smv"])
        if (k, l) == ("tint", "hue"):
            return dot(Pl[0], Pk[1], mp["Smu"]) - dot(Pl[0], Pk[0], mp["Smv"])
        if k == l:  # saturation or hue
            return dot(Pk[0], Pl[0], mp["Sc2"])
        return 0.0  # saturation vs hue are orthogonal by construction

    alpha = {"brightness": BUDGET.brightness, "contrast": BUDGET.contrast, "tint": BUDGET.tint,
             "saturation": BUDGET.saturation, "hue": np.deg2rad(BUDGET.hue_deg)}
    out = {}
    for group in (("brightness", "contrast"), ("tint", "saturation", "hue")):
        P = {k: pats(k) for k in group}
        G = np.array([[ip(k, P[k], l, P[l]) for l in group] for k in group])
        r = np.array([rhs(k, P[k]) for k in group])
        a_hat = np.linalg.pinv(G, rcond=1e-9) @ r  # all carriers sharing these channels, fitted together
        for i, k in enumerate(group):
            self_energy = G[i, i]
            if self_energy < 1e-6 * max(1.0, float(np.abs(G).max())):
                out[k] = (0.0, float("inf"))  # nothing to carry it here (e.g. saturation on a grey slide)
                continue
            decoys = []
            for j in range(DECOYS):
                Pd = pats(k, j)
                resid = rhs(k, Pd) - sum(a_hat[m] * ip(l, P[l], k, Pd) for m, l in enumerate(group))
                decoys.append(resid / ip(k, Pd, k, Pd) / alpha[k])
            d = np.array(decoys)
            sd = float(d.std(ddof=1))
            out[k] = (float(a_hat[i]) / alpha[k] - float(d.mean()), sd if sd > 0 else float("inf"))
    return out

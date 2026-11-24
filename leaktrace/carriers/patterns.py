"""Shared building blocks: keyed random generators and the temporal code."""
import hashlib
import hmac

import numpy as np


def rng(key: bytes, *parts) -> np.random.Generator:
    """A generator only someone holding the key can reproduce, distinct for every (part, part, ...) tuple."""
    digest = hmac.new(key, "\x1f".join(map(str, parts)).encode(), hashlib.sha256).digest()
    return np.random.Generator(np.random.Philox(key=int.from_bytes(digest[:16], "big")))


_dense = np.linspace(0, np.pi, 100_001)
_PEAK = float(np.abs(np.sin(3 * _dense) - np.sin(_dense) / 3).max())


def code(u: np.ndarray) -> np.ndarray:
    """The in-segment temporal code s(x) = sin 3x - (sin x)/3 at x = pi * u, u in [0, 1] across the segment,
    scaled to a peak of 1. It is zero at both segment ends (A and B splice cleanly), has zero mean (constant
    offsets cancel) and is symmetric about the middle (slow linear drifts such as auto-exposure cancel)."""
    x = np.pi * np.clip(np.asarray(u, np.float64), 0, 1)
    return (np.sin(3 * x) - np.sin(x) / 3) / _PEAK


def temporal(n: int) -> np.ndarray:
    """code() sampled at n centred points."""
    return code((np.arange(n) + 0.5) / n)


def tukey(u: np.ndarray, edge: float = 0.125) -> np.ndarray:
    """1 in the middle of the segment, easing to 0 over the first and last `edge` of it."""
    u = np.clip(np.asarray(u, np.float64), 0, 1)
    out = np.ones_like(u)
    lo, hi = u < edge, u > 1 - edge
    out[lo] = 0.5 * (1 - np.cos(np.pi * u[lo] / edge))
    out[hi] = 0.5 * (1 - np.cos(np.pi * (1 - u[hi]) / edge))
    return out

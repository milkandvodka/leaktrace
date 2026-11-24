"""Shared building blocks: keyed random generators and the temporal code."""
import hashlib
import hmac

import numpy as np


def rng(key: bytes, *parts) -> np.random.Generator:
    """A generator only someone holding the key can reproduce, distinct for every (part, part, ...) tuple."""
    digest = hmac.new(key, "\x1f".join(map(str, parts)).encode(), hashlib.sha256).digest()
    return np.random.Generator(np.random.Philox(key=int.from_bytes(digest[:16], "big")))


def temporal(n: int) -> np.ndarray:
    """The in-segment temporal code s(x) = sin 3x - (sin x)/3, x = pi t / T, sampled at n centred points and
    scaled to a peak of 1. It is zero at both segment ends (A and B splice cleanly), has zero mean (constant
    offsets cancel) and is symmetric about the middle (slow linear drifts such as auto-exposure cancel)."""
    x = np.pi * (np.arange(n) + 0.5) / n
    s = np.sin(3 * x) - np.sin(x) / 3
    return s / np.abs(s).max()

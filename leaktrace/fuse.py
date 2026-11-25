"""Fusing nine carriers into one soft bit per segment.

Each carrier gives a z-score per segment (positive = rendition A). How much each carrier can be trusted differs
per leak: a phone recording kills fine picture detail but keeps timing; a muted clip has no audio at all. Weights
therefore come from the leak itself: a carrier's weight is how strongly it agrees, on average, with what all the
OTHER carriers say (leave-one-out, so a carrier can't vote for itself). Carriers that agree get weight; ones that
carry nothing get ~0. None of this looks at any viewer's codeword, so the accusation's innocent null stays valid.
"""
import numpy as np

CARRIERS = ("timing", "patchwork", "loudness", "tilt", "brightness", "tint", "saturation", "hue", "contrast")


def fuse(z: np.ndarray, iterations: int = 4) -> tuple[np.ndarray, dict]:
    """z: (segments, carriers) z-scores, 0 where a carrier saw nothing. Returns (y in [-1, 1] per segment,
    {carrier: weight})."""
    z = np.clip(np.nan_to_num(np.asarray(z, np.float64)), -30, 30)
    n, k = z.shape
    w = np.ones(k)
    for _ in range(iterations):
        mu = np.zeros(k)
        for j in range(k):
            others = np.delete(np.arange(k), j)
            vote = np.sign(z[:, others] @ w[others])
            mu[j] = float(np.mean(z[:, j] * vote)) if n else 0.0
        w = np.maximum(mu, 0.0)
        if not w.any():
            w = np.ones(k)
            break
    norm = float(np.sqrt(np.sum(w ** 2))) or 1.0
    combined = z @ w / norm
    return np.tanh(combined / 2), dict(zip(CARRIERS[:k], map(float, w)))

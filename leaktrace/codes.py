"""Who gets rendition A and who gets B for each segment, and how a leak's evidence points back to an account.

Codes are a Tardos code with cutoff t = 0.1: segment i of lecture L has a secret bias p_i in [t, 1 - t] drawn
from the arcsine distribution, and a student's bit is 1 (rendition A) with probability p_i. Everything is
derived from the secret key, so nothing is stored per student. Biased codes keep pointing at a member of a small
group that mixes their copies segment by segment, which a plain 50/50 code doesn't.

Scoring is Skoric's symmetric Tardos score with soft observations y_i in [-1, 1] (0 = segment not seen):
    term_i = y_i * sqrt((1 - p) / p)  if the student's bit is 1
    term_i = -y_i * sqrt(p / (1 - p)) if the student's bit is 0
For an innocent student every term has mean 0 and variance y_i^2, so z = score / sqrt(sum y_i^2) is standard
normal; a leaker averages about 0.86 per clean segment. The null is checked by simulating innocent students.
"""
import hashlib
import hmac
import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import norm

from .config import BUDGET

IDENTIFIED, NARROWED = 1e-6, 1e-2  # family-wise false-accusation bounds for the verdicts


def _rng(key: bytes, *parts: str) -> np.random.Generator:
    digest = hmac.new(key, "\x1f".join(parts).encode(), hashlib.sha256).digest()
    return np.random.Generator(np.random.Philox(key=int.from_bytes(digest[:16], "big")))


def bias(key: bytes, lecture: str, n: int) -> np.ndarray:
    t = BUDGET.bias_cutoff
    lo, hi = math.asin(math.sqrt(t)), math.asin(math.sqrt(1 - t))
    u = _rng(key, "bias", lecture).random(n)
    return np.sin(lo + u * (hi - lo)) ** 2


def codeword(key: bytes, lecture: str, student: str, n: int, p: np.ndarray | None = None) -> np.ndarray:
    """True = rendition A for that segment. Pass p (this lecture's biases) to skip recomputing them."""
    return _rng(key, "bit", lecture, student).random(n) < (bias(key, lecture, n) if p is None else p)


def _weights(code: np.ndarray, p: np.ndarray) -> np.ndarray:
    return np.where(code, np.sqrt((1 - p) / p), -np.sqrt(p / (1 - p)))


def score(y: np.ndarray, code: np.ndarray, p: np.ndarray) -> float:
    return float(np.dot(_weights(code, p), y))


@dataclass
class Suspect:
    student: str
    z: float
    p: float
    p_family: float
    verdict: str  # identified | narrowed | inconclusive

    def as_dict(self) -> dict:
        return asdict(self)


def chernoff_tail(s: float, y: np.ndarray, p: np.ndarray) -> float:
    """A guaranteed upper bound on the chance that an innocent student scores s or more:
    P(S >= s) <= min over lambda > 0 of exp(-lambda s) * prod_i E[exp(lambda * term_i)], where each term is
    y_i * sqrt((1-p)/p) with probability p and -y_i * sqrt(p/(1-p)) otherwise. Exact for any number of segments,
    no normal approximation: this is the number a false accusation is bounded by."""
    a, b = y * np.sqrt((1 - p) / p), -y * np.sqrt(p / (1 - p))
    lp, lq = np.log(p), np.log1p(-p)

    def log_bound(lam: float) -> float:
        return float(-lam * s + np.sum(np.logaddexp(lp + lam * a, lq + lam * b)))

    best = minimize_scalar(log_bound, bounds=(0.0, 60.0), method="bounded", options={"xatol": 1e-4})
    return float(np.exp(min(0.0, best.fun, log_bound(0.0))))


def null_scores(y: np.ndarray, p: np.ndarray, n_null: int = 20_000, chunk: int = 2_000) -> np.ndarray:
    """Scores of simulated innocent students (independent codes with the same biases)."""
    rng = np.random.default_rng(0)
    out = []
    for start in range(0, n_null, chunk):
        codes = rng.random((min(chunk, n_null - start), len(p))) < p
        out.append(_weights(codes, p) @ y)
    return np.concatenate(out)


def accuse(key: bytes, lecture: str, y: np.ndarray, candidates: list[str], n_null: int = 20_000) -> list[Suspect]:
    """Every candidate ranked by evidence, with a family-wise false-accusation bound and a verdict."""
    y = np.asarray(y, dtype=np.float64)
    seen = np.flatnonzero(y)
    p_all = bias(key, lecture, len(y))
    p = p_all[seen]
    ys = y[seen]
    energy = math.sqrt(float(np.sum(ys ** 2)))
    if energy == 0 or not candidates:
        return [Suspect(s, 0.0, 1.0, 1.0, "inconclusive") for s in candidates]
    null = null_scores(ys, p, n_null) / energy  # a check on the analytic null; z is reported on its scale
    mu, sd = float(null.mean()), float(null.std()) or 1.0
    n = len(candidates)
    out = []
    for s in candidates:
        raw = score(ys, codeword(key, lecture, s, len(y), p_all)[seen], p)
        z = (raw / energy - mu) / sd
        # Anyone with a real chance of a verdict gets the exact bound; the rest only need a ranking.
        pv = chernoff_tail(raw, ys, p) if z > 2 else float(norm.sf(z))
        fam = min(1.0, n * pv)
        verdict = "identified" if fam < IDENTIFIED else "narrowed" if fam < NARROWED else "inconclusive"
        out.append(Suspect(s, float(z), pv, fam, verdict))
    return sorted(out, key=lambda x: -x.z)

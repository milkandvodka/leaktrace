import math

import numpy as np

from leaktrace.codes import accuse, bias, codeword, score

KEY = bytes(range(32))


def test_codeword_is_deterministic_and_keyed():
    a = codeword(KEY, "lec1", "student-7", 4000)
    assert np.array_equal(a, codeword(KEY, "lec1", "student-7", 4000))
    other = codeword(bytes(32), "lec1", "student-7", 4000)
    assert 0.45 < np.mean(a != other) < 0.55
    assert not np.array_equal(a, codeword(KEY, "lec1", "student-8", 4000))


def test_bias_follows_truncated_arcsine():
    p = bias(KEY, "lec1", 20000)
    assert p.min() >= 0.1 and p.max() <= 0.9
    assert abs(p.mean() - 0.5) < 0.01
    lo, hi = math.asin(math.sqrt(0.1)), math.asin(math.sqrt(0.9))
    expected = (math.asin(math.sqrt(0.2)) - lo) / (hi - lo)  # CDF at 0.2 = 0.153
    assert abs(np.mean(p < 0.2) - expected) < 0.015


def _students(n):
    return [f"s{i:05d}" for i in range(n)]


def test_single_leaker_identified_from_150_clean_segments():
    n = 150
    leaker = "s00042"
    y = np.where(codeword(KEY, "lec1", leaker, n), 1.0, -1.0)
    top = accuse(KEY, "lec1", y, _students(5000))[0]
    assert top.student == leaker and top.z > 7 and top.verdict == "identified"


def test_score_matches_accuse_for_one_student():
    n = 300
    y = np.random.default_rng(3).uniform(-1, 1, n)
    c = codeword(KEY, "lec1", "s00001", n)
    s = score(y, c, bias(KEY, "lec1", n))
    assert abs(s / np.sqrt(np.sum(y ** 2)) - next(x.z for x in accuse(KEY, "lec1", y, ["s00001"]))) < 0.05


def test_innocents_never_identified():
    rng = np.random.default_rng(11)
    students = _students(2000)
    for _ in range(50):
        y = rng.uniform(-1, 1, 300) * (rng.random(300) < 0.9)  # some segments unobserved
        assert all(s.verdict != "identified" for s in accuse(KEY, "lec1", y, students))


def test_interleaved_pair_points_to_a_colluder():
    n = 600
    a, b = codeword(KEY, "lec1", "s00010", n), codeword(KEY, "lec1", "s00020", n)
    pick = np.random.default_rng(5).random(n) < 0.5
    y = np.where(np.where(pick, a, b), 1.0, -1.0)
    top2 = accuse(KEY, "lec1", y, _students(1000))[:2]
    assert {"s00010", "s00020"} & {s.student for s in top2}
    assert top2[0].p_family < 1e-6


def test_chernoff_bound_holds_for_short_codes():
    """The bound must sit above the real innocent tail even with very few segments (no normal approximation)."""
    from leaktrace.codes import _weights, chernoff_tail, null_scores
    m = 20
    p = bias(KEY, "lec1", m)
    y = np.random.default_rng(2).uniform(0.3, 1.0, m) * np.random.default_rng(3).choice([-1, 1], m)
    sims = null_scores(y, p, n_null=400_000)
    for s in np.quantile(sims, [0.99, 0.999, 0.9999]):
        assert chernoff_tail(s, y, p) >= np.mean(sims >= s) * 0.9  # an upper bound (allowing sampling noise)
    leaker = codeword(KEY, "lec1", "s1", m)  # a leak of s1's copy: the observations follow s1's bits
    y_leak = np.where(leaker, 1.0, -1.0) * np.abs(y)
    assert chernoff_tail(float(_weights(leaker, p) @ y_leak), y_leak, p) < 0.05


def test_one_minute_of_clean_marks_is_not_inconclusive():
    n = 31  # one minute of 1.92 s segments
    y = np.where(codeword(KEY, "lec1", "s00042", n), 1.0, -1.0)
    top = accuse(KEY, "lec1", y, _students(1000))[0]
    assert top.student == "s00042" and top.verdict in ("narrowed", "identified"), top


def test_unobserved_everything_is_inconclusive():
    top = accuse(KEY, "lec1", np.zeros(100), _students(10))[0]
    assert top.verdict == "inconclusive"

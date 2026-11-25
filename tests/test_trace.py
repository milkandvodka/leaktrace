import numpy as np
import pytest

from conftest import CACHE, _code_hash
from leaktrace import deliver, media
from leaktrace.fuse import CARRIERS, fuse
from leaktrace.store import Store
from leaktrace.trace import trace

STUDENTS = [f"s{i:05d}" for i in range(50)]
LEAKER = "s00007"


def test_fuse_trusts_carriers_that_agree_and_ignores_noise():
    rng = np.random.default_rng(0)
    bits = rng.choice([-1.0, 1.0], 200)
    z = rng.normal(0, 1, (200, len(CARRIERS)))
    z[:, 0] += 4 * bits          # timing carries the bit
    z[:, 5] += 3 * bits          # tint carries it
    z[:, 7] -= 0 * bits          # hue: noise only
    y, w = fuse(z)
    assert w["timing"] > 1 and w["tint"] > 1 and w["hue"] < 0.5 and w["saturation"] < 0.5
    assert np.mean(np.sign(y) == bits) > 0.97


def test_fuse_on_pure_noise_stays_weak():
    y, _w = fuse(np.random.default_rng(1).normal(0, 1, (200, len(CARRIERS))))
    assert np.mean(np.abs(y)) < 0.6


@pytest.fixture(scope="module")
def lec150(tmp_path_factory):
    """A 150 s lecture (78 marked segments) packaged once and cached, plus a store of 50 students who all
    opened it."""
    import os
    from leaktrace.ingest import ingest
    from leaktrace.synth import make_lecture
    data = CACHE / f"data150-{_code_hash()}"
    os.environ["LEAKTRACE_DATA"] = str(data)
    master = CACHE / "lecture_150s.mp4"
    if not master.exists():
        make_lecture(master, seconds=150, seed=5, tts=False)
    if not (data / "lectures" / "lec150" / "meta.json").exists():
        ingest(master, "lec150", "Pharmacology 1", preset="veryfast")
    store = Store(tmp_path_factory.mktemp("db") / "db.sqlite")
    for s in STUDENTS:
        store.add_student(s, s)
        store.log_view(s, "lec150", "play", at="2026-11-01T00:00:00")
    return store


@pytest.mark.slow
def test_export_is_traced_to_its_student(lec150):
    r = trace(deliver.export("lec150", LEAKER), lec150)
    assert r.verdict == "identified" and r.suspects[0]["student"] == LEAKER, (r.verdict, r.suspects[:3])
    assert r.lecture["id"] == "lec150" and r.segments_used >= 70 and "Copyright notice" in r.notice
    assert all(s["verdict"] != "identified" for s in r.suspects[1:])


@pytest.mark.slow
def test_trimmed_60s_narrows_to_student(lec150, tmp_path):
    clip = tmp_path / "clip.mp4"
    media.run(["-ss", 40, "-i", deliver.export("lec150", LEAKER), "-t", 60, "-c:v", "libx264", "-preset", "veryfast",
               "-crf", 26, "-c:a", "aac", "-b:a", "64k", clip])
    r = trace(clip, lec150)
    assert r.suspects[0]["student"] == LEAKER and r.verdict in ("narrowed", "identified"), (r.verdict, r.suspects[:3])


@pytest.mark.slow
def test_innocent_student_not_accused(lec150):
    others = [s for s in STUDENTS if s != LEAKER]  # the real leaker isn't among the candidates
    r = trace(deliver.export("lec150", LEAKER), lec150, candidates=others)
    assert r.verdict != "identified", r.suspects[:3]


@pytest.mark.slow
def test_foreign_video_not_ours(lec150, tmp_path):
    other = tmp_path / "other.mp4"
    media.run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=12", "-f", "lavfi",
               "-i", "sine=frequency=440:duration=12", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", other])
    assert trace(other, lec150).verdict == "not_ours"


@pytest.mark.slow
def test_muted_export_traced_by_picture(lec150, tmp_path):
    muted = tmp_path / "muted.mp4"
    media.run(["-i", deliver.export("lec150", LEAKER), "-an", "-c:v", "copy", muted])
    r = trace(muted, lec150)
    assert r.suspects[0]["student"] == LEAKER and r.verdict in ("narrowed", "identified"), (r.verdict, r.suspects[:3])
    assert r.carriers["timing"]["weight"] == 0 or r.carriers["timing"]["mean_z"] == 0


@pytest.fixture(scope="module")
def rep80(lec150, lecture_40s):
    """A lecture that repeats itself (a recap, a shared intro): the 40 s lecture played twice, in lec150's library."""
    import os
    from pathlib import Path
    from leaktrace.ingest import ingest
    data = Path(os.environ["LEAKTRACE_DATA"])
    if not (data / "lectures" / "rep80" / "meta.json").exists():
        master = CACHE / "lecture_40s_twice.mp4"
        if not master.exists():
            media.run(["-stream_loop", "1", "-i", lecture_40s, "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                       "-c:a", "aac", master])
        ingest(master, "rep80", "Recap", preset="veryfast")
    return lec150


@pytest.mark.slow
def test_clip_of_a_repeated_passage_is_read_at_the_right_copy(rep80, tmp_path):
    clip = tmp_path / "clip.mp4"
    media.run(["-ss", "44", "-i", deliver.export("rep80", LEAKER), "-t", "34", "-c:v", "libx264", "-preset", "veryfast",
               "-c:a", "aac", clip])
    r = trace(clip, rep80, candidates=STUDENTS)
    assert r.location is not None, r.verdict  # the passage plays at 4 s and at 44 s: both match equally
    assert r.location["offset"] == pytest.approx(44, abs=0.15)
    assert r.suspects[0]["student"] == LEAKER, r.suspects[:3]


@pytest.mark.slow
def test_clip_partly_overlapping_a_repeat_is_still_located(rep80, tmp_path):
    clip = tmp_path / "clip.mp4"  # 35-65 s: its last 25 s also play at 0-25 s (a 0.71x second match)
    media.run(["-ss", "35", "-i", deliver.export("rep80", LEAKER), "-t", "30", "-c:v", "libx264", "-preset", "veryfast",
               "-c:a", "aac", clip])
    r = trace(clip, rep80, candidates=STUDENTS)
    assert r.location is not None, r.verdict
    assert r.location["offset"] == pytest.approx(35, abs=0.15)
    assert r.suspects[0]["student"] == LEAKER, r.suspects[:3]

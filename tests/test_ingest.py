import json
import subprocess

import numpy as np
import pytest

from conftest import CACHE
from leaktrace import media
from leaktrace.config import HEIGHT, SEG_FRAMES, WIDTH
from leaktrace.ingest import ingest
from leaktrace.store import Store
from leaktrace.synth import make_title


@pytest.fixture(scope="module")
def short_master():
    out = CACHE / "title_5p5s.mp4"  # 2 full segments + a partial one
    if not out.exists():
        make_title(out, seconds=5.5, seed=3, tts=False)
    return out


@pytest.fixture(scope="module")
def ingested(short_master, tmp_path_factory):
    import os
    data = tmp_path_factory.mktemp("data")
    os.environ["LEAKTRACE_DATA"] = str(data)
    meta = ingest(short_master, "lec-short", "Short title", preset="veryfast")
    return data / "titles" / "lec-short", meta


def _segments(d):
    lines = (d / "index.m3u8").read_text().splitlines()
    return [l for l in lines if l.startswith("#EXTINF")], [l for l in lines if l.endswith(".ts")]


def _start(ts):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", str(ts)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def test_ingest_makes_twin_renditions(ingested):
    d, meta = ingested
    (ea, sa), (eb, sb) = _segments(d / "A"), _segments(d / "B")
    assert ea == eb and sa == sb and len(sa) == meta.n_segments == 3 and meta.marked_segments == 2
    for name in sa:
        assert abs(_start(d / "A" / name) - _start(d / "B" / name)) < 1e-6
    for name in sa[:2]:
        assert (d / "A" / name).read_bytes() != (d / "B" / name).read_bytes()
    saved = json.loads((d / "meta.json").read_text())
    assert saved["id"] == "lec-short" and len(saved["timing"]) == 3 and saved["timing"][2] is None
    assert (d / "mezzanine.mp4").exists()


def _decode(path, start, n):
    return [f.astype(np.int16) for _, f in zip(range(n), media.frames(path, WIDTH, HEIGHT, 25, start=start))]


def test_partial_tail_unmarked(ingested):
    d, _ = ingested
    a = _decode(d / "A" / "00002.ts", 0, 10)
    b = _decode(d / "B" / "00002.ts", 0, 10)
    assert np.mean([np.abs(x - y).mean() for x, y in zip(a, b)]) < 0.5


def test_imperceptible(ingested):
    d, _ = ingested
    a = media.frames(d / "A" / "00001.ts", WIDTH, HEIGHT, 25)
    m = media.frames(d / "mezzanine.mp4", WIDTH, HEIGHT, 25, start=1.92)
    err = [((x[:HEIGHT].astype(float) - y[:HEIGHT].astype(float)) ** 2).mean() for x, y, _ in zip(a, m, range(SEG_FRAMES))]
    assert 10 * np.log10(255 ** 2 / np.mean(err)) > 42


def test_silent_master_ingests_picture_only(short_master, tmp_path, monkeypatch):
    monkeypatch.setenv("LEAKTRACE_DATA", str(tmp_path))
    muted = tmp_path / "muted.mp4"
    media.run(["-i", short_master, "-an", "-c:v", "copy", muted])
    meta = ingest(muted, "lec-muted", "Muted", preset="veryfast")
    d = tmp_path / "titles" / "lec-muted"
    assert not meta.has_audio and meta.marked_segments >= 1 and meta.timing[0] is None
    assert not media.probe(d / "A" / "00000.ts").has_audio
    assert (d / "A" / "00000.ts").read_bytes() != (d / "B" / "00000.ts").read_bytes()


def test_store_views_and_viewers(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.add_viewer("s1", "Asha")
    s.add_viewer("s2", "Ravi")
    s.log_view("s1", "lec", "play", at="2026-11-20T10:00:00")
    s.log_view("s2", "lec", "download", at="2026-11-22T10:00:00")
    s.log_view("s1", "other", "play", at="2026-11-20T10:00:00")
    assert s.watched_by("lec") == ["s1", "s2"]
    assert s.watched_by("lec", before="2026-11-21T00:00:00") == ["s1"]
    assert [x["id"] for x in s.viewers()] == ["s1", "s2"]

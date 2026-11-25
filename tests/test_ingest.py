import json
import subprocess
from fractions import Fraction

import numpy as np
import pytest

from conftest import CACHE
from leaktrace import media
from leaktrace.config import HEIGHT, WIDTH
from leaktrace.grid import Grid
from leaktrace.ingest import canvas_size, ingest
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
    d, meta = ingested
    a = media.frames(d / "A" / "00001.ts", WIDTH, HEIGHT, 25)
    m = media.frames(d / "mezzanine.mp4", WIDTH, HEIGHT, 25, start=meta.grid.seconds)
    err = [((x[:HEIGHT].astype(float) - y[:HEIGHT].astype(float)) ** 2).mean()
           for x, y, _ in zip(a, m, range(meta.grid.seg_frames))]
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


NTSC = Fraction(30000, 1001)


def _probe(w, h, rotation=0):
    return media.Probe(duration=1.0, has_audio=True, has_video=True, width=w, height=h, rate=Fraction(25),
                       rotation=rotation, channels=2)


def test_canvas_keeps_the_source_size_up_to_the_cap(monkeypatch):
    monkeypatch.setenv("LEAKTRACE_MAX_HEIGHT", "1080")
    assert canvas_size(_probe(3840, 2160)) == (1920, 1080)
    assert canvas_size(_probe(640, 360)) == (640, 360)  # never upscaled
    assert canvas_size(_probe(1080, 1920)) == (1080, 1920)  # portrait: the short side is what's capped
    assert canvas_size(_probe(1920, 1080, rotation=90)) == (1080, 1920)  # a phone's rotation flag applied
    assert canvas_size(_probe(853, 480)) == (852, 480)  # 4:2:0 needs even sizes


def _frames_in(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return int(out.stdout.split()[0])  # TS files list the stream once per program


def _package(title_40s, tmp_path_factory, name, vf, channels, seconds):
    d = tmp_path_factory.mktemp(name)
    src = d / "src.mp4"
    media.run(["-t", seconds, "-i", title_40s, "-vf", vf, "-ac", channels, "-c:v", "libx264", "-preset", "ultrafast",
               "-crf", 16, "-c:a", "aac", "-b:a", "192k", src])
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LEAKTRACE_DATA", str(d / "data"))
        meta = ingest(src, name, name, preset="ultrafast")
    return d / "data" / "titles" / name, meta


@pytest.fixture(scope="module")
def ntsc_title(title_40s, tmp_path_factory):
    """7 s of the test title as a 29.97 fps 1080p stereo source, packaged."""
    return _package(title_40s, tmp_path_factory, "ntsc", "scale=1920:1080,fps=30000/1001", 2, 7)


def test_a_2997_fps_1080p_stereo_title_is_packaged_on_its_own_grid(ntsc_title):
    folder, meta = ntsc_title
    assert meta.grid == Grid(NTSC, 60) and meta.canvas == [1920, 1080] and meta.channels == 2
    seg = folder / "A" / "00001.ts"
    p = media.probe(seg)
    assert (p.width, p.height, p.rate, p.channels) == (1920, 1080, NTSC, 2)
    assert _frames_in(seg) == 60
    assert meta.n_segments == -(-meta.n_frames // 60) == len(meta.timing) and meta.marked_segments == meta.n_frames // 60
    assert (folder / "A" / "00000.ts").read_bytes() != (folder / "B" / "00000.ts").read_bytes()


def test_a_24_fps_title_smaller_than_the_cap_keeps_its_size(title_40s, tmp_path_factory):
    _folder, meta = _package(title_40s, tmp_path_factory, "film", "scale=640:360,fps=24", 1, 4.5)
    assert meta.grid == Grid(Fraction(24), 48) and meta.canvas == [640, 360] and meta.channels == 1
    assert meta.marked_segments == 2

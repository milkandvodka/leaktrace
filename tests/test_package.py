"""CMAF output: a quality ladder in variants A and B, cut on the title's grid, behind one set of manifests."""
import json
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from leaktrace import bmff, media
from leaktrace.edge import naming_key, object_name
from leaktrace.config import settings
from leaktrace.grid import Grid
from leaktrace.ingest import ingest
from leaktrace.package import ladder

NTSC = Fraction(30000, 1001)
MPD = "{urn:mpeg:dash:schema:mpd:2011}"


def test_ladder_never_upscales_and_keeps_the_source_size_on_top():
    spec = "1080:5000,720:3000,480:1500,360:800"
    assert [r.name for r in ladder((1920, 1080), spec)] == ["v1080", "v720", "v480", "v360"]
    assert [(r.name, r.width, r.height, r.kbps) for r in ladder((960, 540), spec)] == [
        ("v540", 960, 540, 3000), ("v480", 854, 480, 1500), ("v360", 640, 360, 800)]
    assert [(r.width, r.height) for r in ladder((1080, 1920), "720:3000")] == [(720, 1280)]  # portrait: short side
    assert [r.name for r in ladder((320, 180), spec)] == ["v180"]


@pytest.fixture(scope="module")
def clip(title_40s, tmp_path_factory):
    """7 s of the test title as a 29.97 fps 640x360 stereo source, packaged with a ladder that starts above it."""
    d = tmp_path_factory.mktemp("cmaf")
    src = d / "src.mp4"
    media.run(["-t", 7, "-i", title_40s, "-vf", "scale=640:360,fps=30000/1001", "-ac", 2, "-c:v", "libx264",
               "-preset", "ultrafast", "-crf", 16, "-c:a", "aac", "-b:a", "192k", src])
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LEAKTRACE_DATA", str(d / "data"))
        mp.setenv("LEAKTRACE_LADDER", "720:3000,360:800,240:400")
        meta = ingest(src, "clip", "Clip", preset="ultrafast")
        folder = d / "data" / "titles" / "clip"
        yield folder, meta, json.loads((folder / "cmaf" / "tracks.json").read_text())


def _track(tracks, tid):
    return next(t for t in tracks["tracks"] if t["id"] == tid)


def _obj(folder, meta, track, n, variant):
    return folder / "cmaf" / f"{object_name(naming_key(settings().secret), meta.id, track, n, variant)}.m4s"


def test_rungs_above_the_source_are_skipped(clip):
    _folder, meta, tracks = clip
    video = [t for t in tracks["tracks"] if t["kind"] == "video"]
    assert [(t["id"], t["width"], t["height"]) for t in video] == [("v360", 640, 360), ("v240", 426, 240)]
    assert meta.grid == Grid(NTSC, 60)


def test_every_video_segment_is_whole_frames_on_the_grid(clip):
    folder, meta, tracks = clip
    for tid in ("v360", "v240"):
        t = _track(tracks, tid)
        frame = t["timescale"] * NTSC.denominator // NTSC.numerator  # timescale is a multiple of the frame rate
        assert len(t["segments"]) == meta.n_segments
        for n, s in enumerate(t["segments"]):
            assert s["t"] - t["segments"][0]["t"] == n * 60 * frame
            assert s["frames"] == (60 if n < meta.marked_segments else meta.n_frames - 60 * n)
        assert t["media_time"] == 0 and t["segments"][0]["t"] == 0  # no B-frame delay, no edit list to honour
        for v in "AB":  # what's on disk is what the index says
            info = bmff.segment_info(_obj(folder, meta, tid, 1, v).read_bytes())
            assert (info.start, info.samples) == (t["segments"][1]["t"], 60)


def test_audio_segments_follow_the_grid_without_drift(clip):
    _folder, meta, tracks = clip
    a = _track(tracks, "audio")
    assert a["timescale"] == 48000 and a["channels"] == 2
    assert a["segments"][0]["t"] == 0 and a["media_time"] == 0  # priming dropped: content starts at 0 everywhere
    for n, s in enumerate(a["segments"]):
        assert abs(s["t"] - meta.grid.sample(n)) <= 512  # the AAC frame nearest the exact boundary, no drift


def test_the_index_doesnt_say_which_size_belongs_to_which_variant(clip):
    _folder, _meta, tracks = clip  # the origin may serve tracks.json; a viewer could match their segment sizes
    assert all(isinstance(s["bytes"], int) for t in tracks["tracks"] for s in t["segments"])


def test_variants_differ_only_where_marked_and_names_reveal_nothing(clip):
    folder, meta, _ = clip
    a, b = _obj(folder, meta, "v360", 0, "A"), _obj(folder, meta, "v360", 0, "B")
    assert a.read_bytes() != b.read_bytes() and len(a.stem) == len(b.stem) == 32
    assert not (folder / "cmaf" / "v360").exists() and not (folder / "work").exists()  # no numbered copies left
    assert (folder / "cmaf" / "v360-init.mp4").exists() and (folder / "cmaf" / "audio-init.mp4").exists()


def _concat(folder, meta, track, variants, out):
    with open(out, "wb") as f:
        f.write((folder / "cmaf" / f"{track}-init.mp4").read_bytes())
        for n, v in enumerate(variants):
            f.write(_obj(folder, meta, track, n, v).read_bytes())
    return out


def test_a_mixed_a_b_sequence_decodes_cleanly(clip, tmp_path):
    folder, meta, _ = clip
    mix = ["A" if n % 2 == 0 else "B" for n in range(meta.n_segments)]  # a switch at every boundary
    for track in ("v360", "v240", "audio"):
        f = _concat(folder, meta, track, mix, tmp_path / f"{track}.mp4")
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-f", "null", "-"], capture_output=True, text=True)
        assert r.returncode == 0 and r.stderr.strip() == "", (track, r.stderr[-500:])
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(tmp_path / "v360.mp4")],
                         capture_output=True, text=True, check=True).stdout
    assert int(out.split()[0]) == meta.n_frames


def test_hls_manifests_list_every_rung_and_the_audio(clip):
    folder, meta, tracks = clip
    master = (folder / "cmaf" / "master.m3u8").read_text()
    assert '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio"' in master and 'CHANNELS="2"' in master
    assert "RESOLUTION=640x360" in master and "RESOLUTION=426x240" in master and "FRAME-RATE=29.970" in master
    assert 'CODECS="avc1.' in master and "mp4a.40.2" in master and master.index("v360.m3u8") < master.index("v240.m3u8")
    media_pl = (folder / "cmaf" / "v360.m3u8").read_text().splitlines()
    assert '#EXT-X-MAP:URI="v360/init.mp4"' in media_pl and media_pl[-1] == "#EXT-X-ENDLIST"
    segs = [l for l in media_pl if l.endswith(".m4s")]
    assert segs == [f"v360/{n:05d}.m4s" for n in range(meta.n_segments)]
    assert sum(float(l[8:-1]) for l in media_pl if l.startswith("#EXTINF:")) == pytest.approx(meta.duration, abs=1e-3)


def test_dash_manifest_has_the_same_timeline(clip):
    folder, meta, tracks = clip
    root = ET.parse(folder / "cmaf" / "manifest.mpd").getroot()
    assert root.get("type") == "static"
    sets = root.findall(f"{MPD}Period/{MPD}AdaptationSet")
    video = next(s for s in sets if s.get("contentType") == "video")
    assert [r.get("id") for r in video.findall(f"{MPD}Representation")] == ["v360", "v240"]
    tmpl = video.find(f"{MPD}SegmentTemplate")
    assert tmpl.get("media") == "$RepresentationID$/$Number%05d$.m4s" and tmpl.get("startNumber") == "0"
    total = sum(int(s.get("d")) * (1 + int(s.get("r", 0))) for s in tmpl.find(f"{MPD}SegmentTimeline"))
    assert total / int(tmpl.get("timescale")) == pytest.approx(meta.duration, abs=1e-3)
    assert any(s.get("contentType") == "audio" for s in sets)

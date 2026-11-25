"""Per-viewer manifests (V5): for CDNs without edge code, the manifests themselves list that viewer's objects."""
import xml.etree.ElementTree as ET

import pytest

from leaktrace import deliver, edge
from leaktrace.codes import codeword
from leaktrace.config import settings

MPD = "{urn:mpeg:dash:schema:mpd:2011}"
MEDIA = "https://media.example.com/titles"


@pytest.fixture()
def viewer(packaged, monkeypatch):
    monkeypatch.setenv("LEAKTRACE_MEDIA_URL", MEDIA + "/")
    _, meta = packaged
    code = codeword(settings().secret, "lec40", "s00007", meta.n_segments)
    nk = edge.naming_key(settings().secret)
    want = lambda track, n: f"{MEDIA}/lec40/{edge.object_name(nk, 'lec40', track, n, 'A' if code[n] else 'B')}.m4s"  # noqa: E731
    return meta, deliver.token("lec40", "s00007"), want


def test_hls_media_playlists_list_the_viewers_own_objects(viewer):
    meta, t, want = viewer
    for track in meta.ladder + ["audio"]:
        text, mime = deliver.viewer_manifest(f"{t}/lec40/{track}.m3u8")
        assert mime == "application/vnd.apple.mpegurl"
        assert [l for l in text.splitlines() if l.endswith(".m4s")] == [want(track, n) for n in range(meta.n_segments)]
        assert f'#EXT-X-MAP:URI="{MEDIA}/lec40/{track}-init.mp4"' in text and t not in text


def test_hls_master_points_at_the_viewers_media_playlists(viewer):
    meta, t, _ = viewer
    text, _ = deliver.viewer_manifest(f"{t}/lec40/master.m3u8")
    assert [l for l in text.splitlines() if l.endswith(".m3u8")] == [f"{r}.m3u8" for r in meta.ladder]  # relative
    assert 'URI="audio.m3u8"' in text and t not in text


def test_dash_lists_every_segment_url_for_the_viewer(viewer):
    meta, t, want = viewer
    text, mime = deliver.viewer_manifest(f"{t}/lec40/manifest.mpd")
    assert mime == "application/dash+xml" and t not in text
    root = ET.fromstring(text)
    for rep in root.iter(f"{MPD}Representation"):
        urls = [u.get("media") for u in rep.iter(f"{MPD}SegmentURL")]
        assert urls == [want(rep.get("id"), n) for n in range(meta.n_segments)]


def test_bad_tokens_and_unknown_files_are_refused(viewer):
    _meta, t, _ = viewer
    with pytest.raises(edge.Denied) as e:
        deliver.viewer_manifest(f"{deliver.token('lec40', 's00007', ttl=-1)}/lec40/master.m3u8")
    assert e.value.status == 403
    for f in ("meta.json", "v720/00000.m4s", "../x.m3u8", "nope.m3u8"):
        with pytest.raises(edge.Denied) as e:
            deliver.viewer_manifest(f"{t}/lec40/{f}")
        assert e.value.status == 404, f

import numpy as np
import pytest

from leaktrace import deliver, edge, media
from leaktrace.codes import codeword
from leaktrace.config import HEIGHT, WIDTH, settings


def test_session_points_at_the_shared_manifests_with_the_viewers_token(packaged):
    s = deliver.session("lec40", "s00007")
    assert s["hls"] == f"/wm/{s['token']}/lec40/master.m3u8" and s["dash"] == f"/wm/{s['token']}/lec40/manifest.mpd"
    assert "s00007" not in s["token"]  # the token carries the A/B decisions, not who they belong to


def test_each_segment_resolves_to_the_viewers_variant_in_every_track(packaged):
    _, meta = packaged
    code = codeword(settings().secret, "lec40", "s00007", meta.n_segments)
    t = deliver.token("lec40", "s00007")
    nk = edge.naming_key(settings().secret)
    for track in meta.ladder + ["audio"]:
        for n in range(meta.n_segments):
            f = deliver.resolve(f"{t}/lec40/{track}/{n:05d}.m4s")
            assert f.exists() and f.stem == edge.object_name(nk, "lec40", track, n, "A" if code[n] else "B")


def test_expired_or_altered_tokens_are_refused(packaged):
    t = deliver.token("lec40", "s00007", ttl=-1)
    with pytest.raises(edge.Denied):
        deliver.resolve(f"{t}/lec40/master.m3u8")
    good = deliver.token("lec40", "s00007")
    exp, bits, sig = good.split(".")
    flipped = bits[:-1] + ("A" if bits[-1] != "A" else "B")
    with pytest.raises(edge.Denied):
        deliver.resolve(f"{exp}.{flipped}.{sig}/lec40/v720/00000.m4s")


def test_export_is_the_viewers_segments_in_order(packaged):
    _, meta = packaged
    out = deliver.export("lec40", "s00007")
    p = media.probe(out)
    assert abs(p.duration - meta.duration) < 0.15 and (p.width, p.height) == tuple(meta.canvas) and p.has_audio
    seg = deliver.resolve(f"{deliver.token('lec40', 's00007')}/lec40/{meta.ladder[0]}/00001.m4s")
    init = seg.parent / f"{meta.ladder[0]}-init.mp4"
    one = out.with_name("one.mp4")
    one.write_bytes(init.read_bytes() + seg.read_bytes())
    a = next(media.frames(one, WIDTH, HEIGHT, meta.grid.fps))
    b = next(media.frames(out, WIDTH, HEIGHT, meta.grid.fps, start=meta.grid.seconds))
    assert np.abs(a.astype(int) - b.astype(int)).mean() < 0.5
    assert deliver.export("lec40", "s00007") == out  # cached

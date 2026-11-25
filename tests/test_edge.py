"""Per-viewer switching at the CDN edge (V4): a signed token carries the viewer's A/B decisions; the edge maps each
request on the shared manifests to the shared object for that viewer's variant. Python and the JS edge agree."""
import json
import subprocess
import time

import pytest

from conftest import ROOT
from leaktrace import edge

EK, NK = bytes(range(32)), bytes(range(32, 64))
BITS = [True, False, False, True, True, False, True, False, False, True]  # A B B A A B A B B A


def _tok(exp=None, title="ep3", bits=BITS):
    return edge.make_token(EK, title, bits, int(time.time()) + 3600 if exp is None else exp)


def test_each_segment_maps_to_the_viewers_variant():
    t = _tok()
    for n, a in enumerate(BITS):
        key = edge.route(f"{t}/ep3/v720/{n:05d}.m4s", EK, NK, time.time())
        assert key == f"ep3/{edge.object_name(NK, 'ep3', 'v720', n, 'A' if a else 'B')}.m4s"


def test_the_same_viewer_gets_the_same_variant_in_every_rung_and_the_audio():
    t = _tok()
    for track in ("v1080", "v360", "audio"):
        key = edge.route(f"{t}/ep3/{track}/00003.m4s", EK, NK, time.time())
        assert key == f"ep3/{edge.object_name(NK, 'ep3', track, 3, 'A')}.m4s"  # bit 3 is A


def test_manifests_and_init_segments_are_shared():
    t = _tok()
    assert edge.route(f"{t}/ep3/master.m3u8", EK, NK, time.time()) == "ep3/master.m3u8"
    assert edge.route(f"{t}/ep3/v720.m3u8", EK, NK, time.time()) == "ep3/v720.m3u8"
    assert edge.route(f"{t}/ep3/manifest.mpd", EK, NK, time.time()) == "ep3/manifest.mpd"
    assert edge.route(f"{t}/ep3/v720/init.mp4", EK, NK, time.time()) == "ep3/v720-init.mp4"


@pytest.mark.parametrize("path", [
    lambda: f"{_tok(exp=int(time.time()) - 1)}/ep3/v720/00000.m4s",             # expired
    lambda: f"{_tok()}/ep4/v720/00000.m4s",                                       # another title
    lambda: f"{_tok().replace('.', '.A', 1)}/ep3/v720/00000.m4s",                 # bits altered
    lambda: f"{_tok()[:-2]}xx/ep3/v720/00000.m4s",                                # signature altered
    lambda: "garbage/ep3/v720/00000.m4s",
])
def test_bad_tokens_are_refused_never_served_a_default(path):
    with pytest.raises(edge.Denied) as e:
        edge.route(path(), EK, NK, time.time())
    assert e.value.status == 403


def test_unknown_files_are_not_found():
    for f in ("ep3/v720/99999.m4s", "ep3/../secret", "ep3/meta.json", "ep3/mezzanine.mp4", "ep3/v720/1.m4s"):
        with pytest.raises(edge.Denied) as e:
            edge.route(f"{_tok()}/{f}", EK, NK, time.time())
        assert e.value.status == 404, f


def test_keys_are_derived_and_distinct():
    s = bytes(32)
    assert edge.edge_key(s) != edge.naming_key(s) and len(edge.edge_key(s)) == 32
    assert edge.edge_key(s) != s


def test_javascript_edge_core_agrees_with_python():
    now = time.time()
    paths = [f"{_tok()}/ep3/v720/{n:05d}.m4s" for n in range(len(BITS))] + [
        f"{_tok()}/ep3/audio/00004.m4s", f"{_tok()}/ep3/master.m3u8", f"{_tok()}/ep3/v720/init.mp4",
        f"{_tok(exp=int(now) - 5)}/ep3/v720/00000.m4s", f"{_tok()}/ep4/v720/00000.m4s", f"{_tok()}/ep3/meta.json",
        f"{_tok()[:-2]}xx/ep3/v720/00001.m4s"]
    want = []
    for p in paths:
        try:
            want.append({"status": 200, "key": edge.route(p, EK, NK, now)})
        except edge.Denied as e:
            want.append({"status": e.status})
    job = json.dumps({"edgeKey": EK.hex(), "namingKey": NK.hex(), "now": now, "paths": paths})
    r = subprocess.run(["node", str(ROOT / "tests" / "edge_parity.mjs")], input=job, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == want


def test_cloudfront_bundle_rewrites_requests_like_python(tmp_path):
    from leaktrace.cli import main
    from leaktrace.config import settings
    out = tmp_path / "cf.js"
    assert main(["edge-bundle", "cloudfront", "--out", str(out)]) == 0
    assert "__LEAKTRACE_" not in out.read_text()
    ek, nk = edge.edge_key(settings().secret), edge.naming_key(settings().secret)
    t = edge.make_token(ek, "ep3", BITS, int(time.time()) + 3600)
    paths = [f"{t}/ep3/v720/{n:05d}.m4s" for n in range(len(BITS))] + [f"{t}/ep3/master.m3u8", f"{t}/ep4/master.m3u8"]
    want = []
    for p in paths:
        try:
            want.append("/" + edge.route(p, ek, nk, time.time()))
        except edge.Denied as e:
            want.append(e.status)
    r = subprocess.run(["node", str(ROOT / "tests" / "edge_parity_cf.mjs"), str(out)], input=json.dumps(paths),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == want


def test_edge_keys_go_to_a_file_not_the_terminal(tmp_path, capsys):
    from leaktrace.cli import main
    from leaktrace.config import settings
    out = tmp_path / "edge.env"
    main(["edge-keys", "--out", str(out)])
    ek = edge.edge_key(settings().secret).hex()
    assert f"LEAKTRACE_EDGE_KEY={ek}" in out.read_text() and ek not in capsys.readouterr().out


def _folders(f, first=1, tok=None):
    return edge.route(f"{tok or _tok()}/ep3/{f}", EK, NK, time.time(), layout="folders", first=first)


def test_folders_layout_switches_a_platforms_own_packaging():
    # the platform packaged masters A and B into ep3/A/ and ep3/B/ with its own tools, numbering segments from 1
    assert _folders("hls/720p.m3u8") == "ep3/A/hls/720p.m3u8"
    assert _folders("dash/stream.mpd") == "ep3/A/dash/stream.mpd"
    assert _folders("hls/720p_init.mp4") == "ep3/A/hls/720p_init.mp4"
    assert _folders("hls/720p_00001.m4s") == "ep3/A/hls/720p_00001.m4s"  # segment 0: A
    assert _folders("hls/720p_00002.ts") == "ep3/B/hls/720p_00002.ts"    # segment 1: B
    assert _folders("audio/seg-4.m4s") == "ep3/A/audio/seg-4.m4s"          # segment 3: A, in every track


def test_folders_layout_refuses_paths_out_of_the_title_and_unknown_files():
    for bad in ("hls/../../other/x.m3u8", "hls/720p_00000.m4s", "hls/720p_00099.m4s", "notes.txt", "a//b.m3u8"):
        with pytest.raises(edge.Denied) as e:
            _folders(bad)
        assert e.value.status == 404, bad


def test_javascript_folders_layout_agrees_with_python():
    files = ["hls/720p.m3u8", "hls/720p_init.mp4", "hls/720p_00001.m4s", "hls/720p_00002.ts", "audio/seg-4.m4s",
             "hls/../../x.m3u8", "hls/720p_00000.m4s", "notes.txt"]
    t = _tok()
    want = []
    for f in files:
        try:
            want.append({"status": 200, "key": _folders(f, tok=t)})
        except edge.Denied as e:
            want.append({"status": e.status})
    job = json.dumps({"edgeKey": EK.hex(), "namingKey": NK.hex(), "now": time.time(), "layout": "folders", "first": 1,
                      "paths": [f"{t}/ep3/{f}" for f in files]})
    r = subprocess.run(["node", str(ROOT / "tests" / "edge_parity.mjs")], input=job, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == want

import numpy as np
import pytest

from leaktrace import deliver, media
from leaktrace.codes import codeword
from leaktrace.config import HEIGHT, WIDTH, settings


def _tokens(text):
    return [l.rsplit("/s/", 1)[1] for l in text.splitlines() if "/s/" in l]


def test_playlist_follows_codeword(packaged):
    _, meta = packaged
    text = deliver.playlist("lec40", "s00007", "https://cdn.example")
    assert text.startswith("#EXTM3U") and text.rstrip().endswith("#EXT-X-ENDLIST")
    tokens = _tokens(text)
    assert len(tokens) == meta.n_segments
    code = codeword(settings().secret, "lec40", "s00007", meta.n_segments)
    for i, tok in enumerate(tokens):
        path = deliver.resolve(tok)
        assert path.name == f"{i:05d}.ts"
        if i < meta.marked_segments:
            assert path.parent.name == ("A" if code[i] else "B")
    # the variant never appears in a URL: the token carries only title, segment, viewer and expiry
    assert "/A/" not in text and "/B/" not in text
    payload = deliver._unb64(tokens[0].split(".")[0]).decode().split("\x1f")
    assert payload[:3] == ["lec40", "0", "s00007"] and len(payload) == 4


def test_tampered_token_rejected(packaged):
    tok = _tokens(deliver.playlist("lec40", "s00007", ""))[3]
    bad = tok[:-2] + ("A" if tok[-2] != "A" else "B") + tok[-1]
    with pytest.raises(deliver.TokenError):
        deliver.resolve(bad)
    with pytest.raises(deliver.TokenError):
        deliver.resolve("not-a-token")


def test_expired_token_rejected(packaged):
    tok = _tokens(deliver.playlist("lec40", "s00007", "", ttl=-1))[0]
    with pytest.raises(deliver.TokenError):
        deliver.resolve(tok)


def test_export_matches_playlist_segments(packaged):
    _, meta = packaged
    out = deliver.export("lec40", "s00007")
    assert out.exists() and abs(media.probe(out).duration - meta.duration) < 0.15
    seg = deliver.resolve(_tokens(deliver.playlist("lec40", "s00007", ""))[1])
    a = next(media.frames(seg, WIDTH, HEIGHT, 25))
    b = next(media.frames(out, WIDTH, HEIGHT, 25, start=meta.grid.seconds))
    assert np.abs(a.astype(int) - b.astype(int)).mean() < 0.5
    assert deliver.export("lec40", "s00007") == out  # cached

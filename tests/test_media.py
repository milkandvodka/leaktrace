import subprocess

import numpy as np

from leaktrace import media
from leaktrace.config import HEIGHT, SEG_FRAMES, SEG_SAMPLES, SR, WIDTH


def test_frames_and_audio_roundtrip(title_40s):
    n = 0
    for f in media.frames(title_40s, WIDTH, HEIGHT, 25):
        assert f.shape == (HEIGHT * 3 // 2, WIDTH) and f.dtype == np.uint8
        n += 1
    assert n == 1000
    x = media.audio(title_40s, SR)
    assert x.dtype == np.float32 and abs(len(x) - 1_920_000) <= 1024 and np.abs(x).max() > 0.05
    assert len(media.audio(title_40s, SR, start=10, dur=2)) == 2 * SR


def test_probe(title_40s):
    p = media.probe(title_40s)
    assert p.has_audio and p.has_video and (p.width, p.height) == (1280, 720) and p.fps == 25 and p.rotation == 0
    assert abs(p.duration - 40) < 0.1


def test_encoder_makes_aligned_segments(tmp_path):
    rng = np.random.default_rng(0)
    with media.Encoder(tmp_path / "A", with_audio=True) as enc:
        for i in range(4 * SEG_FRAMES):
            enc.write_frame(np.full((HEIGHT * 3 // 2, WIDTH), 60 + i % 100, np.uint8))
        enc.write_audio((0.1 * rng.standard_normal(4 * SEG_SAMPLES)).astype(np.float32))
    lines = (tmp_path / "A" / "index.m3u8").read_text().splitlines()
    assert [l for l in lines if l.startswith("#EXTINF")] == ["#EXTINF:1.920000,"] * 4
    assert [l for l in lines if l.endswith(".ts")] == ["00000.ts", "00001.ts", "00002.ts", "00003.ts"]


def test_portrait_rotated_input_is_padded_not_stretched(tmp_path, title_40s):
    clip = tmp_path / "phone.mp4"
    media.run(["-t", "2", "-i", str(title_40s), "-c:v", "libx264", "-c:a", "aac", str(tmp_path / "flat.mp4")])
    media.run(["-display_rotation:v:0", "90", "-i", str(tmp_path / "flat.mp4"), "-c", "copy", str(clip)])
    assert media.probe(clip).rotation in (90, -90, 270)
    x, y, w, h = media.content_box(clip)
    assert w < h and (x, y) != (0, 0)
    f = next(media.frames(clip, WIDTH, HEIGHT, 25))[:HEIGHT]
    assert f[:, : x - 2].max() <= 20 and f[:, x + w + 2:].max() <= 20  # padding is black
    assert f[:, x + 4: x + w - 4].mean() > 60  # content is not


def test_missing_audio_is_silence(tmp_path, title_40s):
    muted = tmp_path / "muted.mp4"
    media.run(["-t", "3", "-i", str(title_40s), "-an", "-c:v", "copy", str(muted)])
    p = media.probe(muted)  # stream copy cuts on a keyframe, so this is a little over 3 s
    assert not p.has_audio
    x = media.audio(muted, SR)
    assert abs(len(x) - p.duration * SR) <= 1 and not x.any()

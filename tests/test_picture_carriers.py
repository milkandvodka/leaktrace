import cv2
import numpy as np
import pytest

from leaktrace import media
from leaktrace.carriers import picture
from leaktrace.config import HEIGHT, SEG_FRAMES, WIDTH

KEY = bytes(range(32))
BOX = (0, 0, WIDTH, HEIGHT)
NAMES = {"brightness", "tint", "saturation", "hue", "contrast"}


@pytest.fixture(scope="module")
def frames(lecture_40s):
    out = []
    for i, f in enumerate(media.frames(lecture_40s, WIDTH, HEIGHT, 25, start=4.0)):
        out.append(f.copy())
        if i + 1 == SEG_FRAMES:
            return out


def _t(i):
    return (i + 0.5) / SEG_FRAMES


def _mark(frames, sign, box=BOX):
    return [picture.embed(f, _t(i), box, KEY, "lec", 3, sign) for i, f in enumerate(frames)]


def _z(leak, mezz, valid=None, box=BOX, key=KEY):
    la = [picture.to_analysis(f) for f in leak]
    ma = [picture.to_analysis(f) for f in mezz]
    valid = np.ones(la[0].shape[1:], bool) if valid is None else valid
    got = picture.measure(la, ma, [_t(i) for i in range(len(la))], valid, key, "lec", 3, box)
    return {k: s / e for k, (s, e) in got.items()}


def _colourful(frames):
    """The same frames with strong, smoothly varying colour everywhere (like camera footage of a teacher)."""
    yy, xx = np.mgrid[0:HEIGHT // 2, 0:WIDTH // 2] / np.array([HEIGHT // 2, WIDTH // 2])[:, None, None]
    u = 128 + 45 * np.sin(2 * np.pi * (xx + 0.3 * yy))
    v = 128 + 45 * np.cos(2 * np.pi * (0.6 * xx - yy))
    out = []
    for f in frames:
        g = f.copy()
        g[HEIGHT:] = np.concatenate([u.ravel(), v.ravel()]).round().astype(np.uint8).reshape(HEIGHT // 2, WIDTH)
        out.append(g)
    return out


@pytest.mark.parametrize("sign", [+1, -1])
def test_carriers_recover_sign_clean(frames, sign):
    z = _z(_mark(frames, sign), frames)
    assert set(z) == NAMES
    for name in ("brightness", "tint", "contrast"):  # on the slides themselves
        assert np.sign(z[name]) == sign and abs(z[name]) > 6, (name, z[name])
    colour = _colourful(frames)
    zc = _z(_mark(colour, sign), colour)
    for name in ("saturation", "hue"):  # where there is colour to work with
        assert np.sign(zc[name]) == sign and abs(zc[name]) > 6, (name, zc[name])


def _camcord(yuv, rng):
    """A phone filming the screen: keystone, blur, gamma, colour cast, sensor noise."""
    h, w = HEIGHT, WIDTH
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[40, 30], [w - 25, 10], [w - 60, h - 20], [15, h - 45]])
    M = cv2.getPerspectiveTransform(src, dst)
    Y = yuv[:h].astype(np.float32)
    U = yuv[h: h + h // 4].reshape(h // 2, w // 2).astype(np.float32)
    V = yuv[h + h // 4:].reshape(h // 2, w // 2).astype(np.float32)
    Y = 255 * (Y / 255) ** 1.1
    U, V = U + 5, V - 3
    Y = cv2.warpPerspective(Y, M, (w, h))
    half = np.diag([0.5, 0.5, 1.0])
    Mc = half @ M @ np.linalg.inv(half)
    U, V = (cv2.warpPerspective(c, Mc, (w // 2, h // 2), borderValue=128) for c in (U, V))
    Y = cv2.GaussianBlur(Y, (0, 0), 1.5) + rng.normal(0, 8, Y.shape)
    U, V = (cv2.GaussianBlur(c, (0, 0), 0.75) + rng.normal(0, 4, c.shape) for c in (U, V))
    out = np.concatenate([Y.ravel(), U.ravel(), V.ravel()])
    return np.clip(np.round(out), 0, 255).astype(np.uint8).reshape(h * 3 // 2, w)


def test_survives_camcord_sim(frames):
    rng = np.random.default_rng(0)
    leak = [_camcord(f, rng) for f in _mark(frames, +1)]
    lg, mg = picture.gray_small(leak[SEG_FRAMES // 2]), picture.gray_small(frames[SEG_FRAMES // 2])
    reg = picture.register(lg, mg)
    assert reg is not None
    H, valid = reg
    warped = [picture.warp(picture.to_analysis(f), H) for f in leak]
    got = picture.measure(warped, [picture.to_analysis(f) for f in frames], [_t(i) for i in range(SEG_FRAMES)],
                          valid, KEY, "lec", 3, BOX)
    z = {k: s / e for k, (s, e) in got.items()}
    assert z["brightness"] > 3 and z["tint"] > 3, z


def test_grey_slide_saturation_is_silent(frames):
    grey = [f.copy() for f in frames]
    for f in grey:
        f[HEIGHT:] = 128  # no colour anywhere
    z = _z(_mark(grey, +1), grey)
    assert abs(z["saturation"]) < 3.5 and abs(z["hue"]) < 3.5 and z["brightness"] > 6


def test_budget(frames):
    marked = _mark(frames, +1)
    dy = np.concatenate([(m[:HEIGHT].astype(int) - f[:HEIGHT].astype(int)).ravel() for m, f in zip(marked, frames)])
    assert np.abs(dy).max() <= 2
    assert 20 * np.log10(255 / np.sqrt(np.mean(dy.astype(float) ** 2))) > 45


def test_letterbox_untouched(frames):
    box = (160, 0, 960, 720)  # a 4:3 lecture pillarboxed in the 16:9 canvas
    mid = range(6, 10)  # near the segment's start the temporal code is ~0 and rounding hides the marks
    marked = [picture.embed(frames[i], _t(i), box, KEY, "lec", 3, +1) for i in mid]
    for m, f in zip(marked, [frames[i] for i in mid]):
        assert np.array_equal(m[:HEIGHT, :160], f[:HEIGHT, :160]) and np.array_equal(m[:HEIGHT, 1120:], f[:HEIGHT, 1120:])
        assert not np.array_equal(m[:HEIGHT, 160:1120], f[:HEIGHT, 160:1120])


def test_wrong_key_is_noise(frames):
    z = _z(_mark(frames, +1), frames, key=bytes(32))
    assert all(abs(z[n]) < 3.5 for n in ("brightness", "tint", "saturation", "hue", "contrast")), z

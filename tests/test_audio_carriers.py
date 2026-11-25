import numpy as np
import pytest
from scipy.io import wavfile

from leaktrace import media
from leaktrace.carriers import audio
from leaktrace.config import SR
from leaktrace.grid import V1
from leaktrace.synth import _babble
from conftest import needs_amr

KEY = bytes(range(32))
M = 0.1
SEG_SAMPLES, T = V1.samples(0), V1.seconds
MS = int(M * SR)


def _speech(seed, n=SEG_SAMPLES):
    x = _babble(np.random.default_rng(seed), ["one two three four five", "six seven eight", "nine ten eleven"])
    return x[int(0.3 * SR): int(0.3 * SR) + n + 2 * MS]


@pytest.fixture(scope="module")
def speech():
    return _speech(4)


def _z(leak_seg, mezz_seg, key=KEY, sr_leak=SR):
    got = audio.measure(audio.spec(leak_seg, sr_leak), audio.spec(mezz_seg, SR), key, "lec", 7, T)
    return {k: s / e for k, (s, e) in got.items()}


def _roundtrip(seg_with_margin, tmp_path, args, ext):
    wav, out = tmp_path / "in.wav", tmp_path / f"out.{ext}"
    wavfile.write(wav, SR, (np.clip(seg_with_margin, -1, 1) * 32767).astype(np.int16))
    media.run(["-i", wav, *args, out])
    back = media.audio(out, SR)
    back = np.pad(back, (0, max(0, len(seg_with_margin) - len(back))))[: len(seg_with_margin)]
    return back[MS:-MS]


@pytest.mark.parametrize("sign", [+1, -1])
def test_each_audio_carrier_recovers_sign_clean(speech, sign):
    marked = audio.embed(speech, SR, M, KEY, "lec", 7, sign)
    assert len(marked) == SEG_SAMPLES
    z = _z(marked, speech[MS:-MS])
    assert set(z) == {"patchwork", "loudness", "tilt"}
    for name, v in z.items():
        assert np.sign(v) == sign and abs(v) > 6, (name, v)


def test_patchwork_survives_aac_64k(speech, tmp_path):
    marked = audio.embed(speech, SR, M, KEY, "lec", 7, +1)
    back = _roundtrip(np.concatenate([speech[:MS], marked, speech[-MS:]]), tmp_path,
                      ["-c:a", "aac", "-b:a", "64k", "-ac", 1], "m4a")
    assert _z(back, speech[MS:-MS])["patchwork"] > 3


@needs_amr
def test_loudness_survives_amr_nb(speech, tmp_path):
    marked = audio.embed(speech, SR, M, KEY, "lec", 7, -1)
    back = _roundtrip(np.concatenate([speech[:MS], marked, speech[-MS:]]), tmp_path,
                      ["-ar", 8000, "-ac", 1, "-c:a", "libopencore_amrnb", "-b:a", "12.2k"], "amr")
    assert _z(back, speech[MS:-MS])["loudness"] < -2


def test_wrong_key_is_noise():
    for seed in range(20):
        sp = _speech(100 + seed)
        marked = audio.embed(sp, SR, M, KEY, "lec", 7, +1)
        assert abs(_z(marked, sp[MS:-MS], key=bytes(32))["patchwork"]) < 3.5


def test_snr_budget(speech):
    marked = audio.embed(speech, SR, M, KEY, "lec", 7, +1)
    seg = speech[MS:-MS]
    assert 10 * np.log10(np.sum(seg ** 2) / np.sum((marked - seg) ** 2)) > 20


def test_a_2002_s_segment_is_marked_and_read_with_its_own_tiles():
    sp = _speech(4, 96096)
    marked = audio.embed(sp, SR, M, KEY, "lec", 7, -1)
    assert len(marked) == 96096
    got = audio.measure(audio.spec(marked, SR), audio.spec(sp[MS:-MS], SR), KEY, "lec", 7, 2.002)
    for name, (s, e) in got.items():
        assert s / e < -6, (name, s / e)


def test_stereo_channels_carry_the_same_marks(speech):
    y = audio.embed(np.stack([speech, 0.7 * speech], 1), SR, M, KEY, "lec", 7, +1)
    assert y.shape == (SEG_SAMPLES, 2)
    np.testing.assert_allclose(y[:, 0], audio.embed(speech, SR, M, KEY, "lec", 7, +1), atol=1e-9)
    np.testing.assert_allclose(y[:, 1], 0.7 * y[:, 0], atol=1e-6)

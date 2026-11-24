import numpy as np
import pytest
from scipy.io import wavfile

from leaktrace import media
from leaktrace.carriers import timing
from leaktrace.config import SEG_SAMPLES, SR
from leaktrace.synth import _babble

M = 0.1  # margin, seconds
MS = int(M * SR)


@pytest.fixture(scope="module")
def speech():
    """2.12 s of speech-like audio: one segment plus a 0.1 s margin each side, with pauses inside."""
    x = _babble(np.random.default_rng(4), ["one two three four", "five six seven", "eight nine ten eleven"])
    return x[int(0.3 * SR): int(0.3 * SR) + SEG_SAMPLES + 2 * MS]


def test_template_shape_and_budget(speech):
    d = timing.template(speech[MS:-MS], SR)
    assert len(d) == 193 and d[0] == 0 and abs(d[-1]) < 1e-9
    assert abs(d.max() - 0.030) < 0.001
    slope = np.abs(np.diff(d)) / 0.01
    talking = timing.speech_mask(speech[MS:-MS], SR)[:-1]
    assert slope[talking].max() <= 0.05 + 1e-9 and slope.max() <= 0.15 + 1e-9


def test_warp_preserves_length_and_identity_at_edges(speech):
    d = timing.template(speech[MS:-MS], SR)
    y = timing.warp_audio(speech, SR, M, d, +1)
    seg = speech[MS:-MS]
    edge = int(0.005 * SR)
    assert len(y) == SEG_SAMPLES
    assert np.sqrt(np.mean((y[:edge] - seg[:edge]) ** 2)) < 1e-3
    assert np.sqrt(np.mean((y[-edge:] - seg[-edge:]) ** 2)) < 1e-3
    assert not np.allclose(y, seg)  # the middle really moved


def test_frame_map_monotonic(speech):
    d = timing.template(speech[MS:-MS], SR)
    for sign in (+1, -1):
        m = timing.frame_map(d, sign)
        assert len(m) == 48 and m[0] == 0 and m[-1] == 47 and np.all(np.diff(m) >= 0)


def _renditions(speech):
    d = timing.template(speech[MS:-MS], SR)
    a, b = timing.warp_audio(speech, SR, M, d, +1), timing.warp_audio(speech, SR, M, d, -1)
    return a, b, timing.realized(speech, a, b, SR, M)


def _measure(leak_buf, mezz_buf, real):
    return timing.measure(timing.features(leak_buf, SR), timing.features(mezz_buf, SR), real, M)


def test_realized_shifts_follow_the_template(speech):
    _a, _b, real = _renditions(speech)
    d = timing.template(speech[MS:-MS], SR)
    planned = np.interp(timing.CENTRES, np.arange(timing.POINTS) * timing.STEP, d)
    assert np.nanmax(np.abs(real[0] - planned)) < 0.012 and np.nanmax(np.abs(real[1] + planned)) < 0.012


@pytest.mark.parametrize("sign", [+1, -1])
def test_measured_displacement_recovers_sign(speech, sign):
    a, b, real = _renditions(speech)
    leak = np.concatenate([speech[:MS], a if sign > 0 else b, speech[-MS:]])
    stat, sigma = _measure(leak, speech, real)
    assert np.sign(stat) == sign and abs(stat / sigma) > 8


def test_timing_survives_old_phone_audio(speech, tmp_path):
    _a, b, real = _renditions(speech)
    d = real
    leak = np.concatenate([speech[:MS], b, speech[-MS:]])
    wav = tmp_path / "leak.wav"
    wavfile.write(wav, SR, (leak * 32767).astype(np.int16))
    amr = tmp_path / "leak.amr"  # speaker -> room -> Nokia: band-limited, reverberant, noisy, AMR-NB 12.2k at 8 kHz
    media.run(["-i", wav, "-af", "aecho=0.8:0.5:35|60:0.35|0.25,highpass=f=250,lowpass=f=3500,volume=0.7",
               "-ar", 8000, "-ac", 1, "-c:a", "libopencore_amrnb", "-b:a", "12.2k", amr])
    back = media.audio(amr, SR)
    back = np.pad(back, (0, max(0, len(leak) - len(back))))[: len(leak)]
    back = back + 0.003 * np.random.default_rng(1).standard_normal(len(back))
    stat, sigma = _measure(back, speech, d)
    assert stat < 0 and abs(stat / sigma) > 3

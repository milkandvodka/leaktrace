import subprocess

import numpy as np

from conftest import ffprobe
from leaktrace.synth import make_lecture


def _frame0(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8)


def _audio_rms(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    return float(np.sqrt(np.mean(x ** 2)))


def test_make_lecture_has_expected_streams(lecture_40s):
    info = ffprobe(lecture_40s)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert v["codec_name"] == "h264" and (v["width"], v["height"]) == (1280, 720) and v["r_frame_rate"] == "25/1"
    assert a["sample_rate"] == "48000"
    assert abs(float(info["format"]["duration"]) - 40) < 0.1


def test_make_lecture_is_deterministic_without_tts(tmp_path):
    a = make_lecture(tmp_path / "a.mp4", seconds=6, seed=7, tts=False)
    b = make_lecture(tmp_path / "b.mp4", seconds=6, seed=7, tts=False)
    assert np.array_equal(_frame0(a), _frame0(b))
    assert _audio_rms(a) == _audio_rms(b) > 0.01  # speech-like audio, not silence

"""Synthetic lectures for tests and the attack bench.

They look and sound like a real lecture where the marks care:
- slides with text and a coloured header (picture carriers need flat areas and colour);
- a moving pointer and a small speaker box (motion);
- speech with natural pauses (the timing carrier hides in pauses).

Speech is Windows text-to-speech when asked for and available. Otherwise it's a deterministic speech-like
signal, so tests get the same lecture on every run and on any machine.
"""
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.io import wavfile
from scipy.signal import lfilter

from .config import FPS, HEIGHT, SR, WIDTH, tool

SLIDES = [
    ("Beta blockers", ["Block beta-1 receptors in the heart", "Lower heart rate and contractility",
                       "Used in angina, hypertension and arrhythmias", "Avoid non-selective ones in asthma"]),
    ("Loop diuretics", ["Act on the thick ascending limb", "Block the Na-K-2Cl cotransporter",
                        "The strongest diuretic effect", "Watch for low potassium"]),
    ("Insulin types", ["Rapid acting insulin covers meals", "Long acting insulin gives a steady base",
                       "Doses follow glucose readings", "Hypoglycaemia is the main risk"]),
    ("The cardiac cycle", ["Atrial contraction tops up the ventricles", "Isovolumetric contraction shuts the valves",
                           "Ejection opens the aortic valve", "Relaxation lets the heart refill"]),
    ("Anticoagulants", ["Heparin works through antithrombin", "Warfarin blocks vitamin K recycling",
                        "Newer agents inhibit factor Xa", "Monitor every patient for bleeding"]),
    ("Renal physiology", ["The glomerulus filters plasma", "The proximal tubule takes back most sodium",
                          "The loop concentrates the urine", "The collecting duct answers to hormones"]),
    ("Antibiotic classes", ["Penicillins block cell wall synthesis", "Macrolides act on the ribosome",
                            "Quinolones inhibit DNA gyrase", "Overuse breeds resistance"]),
    ("Thyroid hormones", ["T4 becomes T3 in the tissues", "They raise the metabolic rate",
                          "Too much causes weight loss and tremor", "Too little causes fatigue and weight gain"]),
]
HEADER = (14, 111, 134)
ACCENTS = [(234, 120, 40), (120, 70, 190), (40, 150, 90), (210, 60, 80)]
BG = (248, 248, 246)
INK = (32, 40, 52)
VOICES = ["Microsoft Zira Desktop", "Microsoft David Desktop", "Microsoft Hazel Desktop"]
BOX = (980, 450, 1240, 650)  # speaker box: x0, y0, x1, y1


def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _slide(title: str, bullets: list[str], number: int, accent) -> np.ndarray:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, 110], fill=HEADER)
    d.text((60, 30), title, font=_font(46), fill=(255, 255, 255))
    for i, b in enumerate(bullets):
        y = 170 + i * 92
        d.ellipse([60, y + 14, 82, y + 36], fill=accent)
        d.text((104, y), b, font=_font(34), fill=INK)
    d.rectangle([60, HEIGHT - 70, 380, HEIGHT - 64], fill=accent)
    d.text((WIDTH - 120, HEIGHT - 60), str(number), font=_font(28), fill=(140, 150, 160))
    x0, y0, x1, y1 = BOX
    d.rectangle([x0, y0, x1, y1], fill=(58, 66, 84))
    return np.asarray(img).copy()


def _resonate(x: np.ndarray, fc: float, bw: float) -> np.ndarray:
    r = np.exp(-np.pi * bw / SR)
    return lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * fc / SR), r * r], x)


def _babble(rng: np.random.Generator, sentences: list[str]) -> np.ndarray:
    """Speech-like audio: voiced syllables with two formants, consonant bursts, word gaps and sentence pauses."""
    parts = [np.zeros(int(0.4 * SR))]
    for sentence in sentences:
        for _ in sentence.split():
            n = int(rng.uniform(0.14, 0.34) * SR)
            t = np.arange(n) / SR
            f0 = rng.uniform(100, 170) * (1 + 0.08 * np.sin(2 * np.pi * rng.uniform(1, 3) * t))
            phase = 2 * np.pi * np.cumsum(f0) / SR
            source = sum(np.sin(k * phase) / k for k in range(1, 28))
            voiced = _resonate(source, rng.uniform(400, 900), 110) + 0.6 * _resonate(source, rng.uniform(1100, 2600), 180)
            env = np.minimum(1, t / 0.02) * np.minimum(1, (t[-1] - t) / 0.05)
            burst = np.zeros(n)
            k = int(0.03 * SR)
            burst[:k] = rng.standard_normal(k) * np.hanning(2 * k)[k:] * 0.3
            parts += [(voiced / (np.abs(voiced).max() + 1e-9) + burst) * env, np.zeros(int(rng.uniform(0.04, 0.12) * SR))]
        parts.append(np.zeros(int(rng.uniform(0.35, 0.7) * SR)))
    x = np.concatenate(parts)
    return 0.1 * x / (np.sqrt(np.mean(x ** 2)) + 1e-9)


def _tts(text: str, voice: str, tmp: Path) -> np.ndarray | None:
    """Windows text-to-speech at 48 kHz mono, or None when it isn't available."""
    ps = shutil.which("powershell")
    if platform.system() != "Windows" or not ps:
        return None
    txt, wav = tmp / "say.txt", tmp / "say.wav"
    txt.write_text(text, encoding="utf-8")
    script = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"try {{ $s.SelectVoice('{voice}') }} catch {{}}; "
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(48000, "
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{wav}', $f); $s.Speak([IO.File]::ReadAllText('{txt}')); $s.Dispose()"
    )
    r = subprocess.run([ps, "-NoProfile", "-Command", script], capture_output=True)
    if r.returncode != 0 or not wav.exists():
        return None
    _sr, x = wavfile.read(wav)
    return x.astype(np.float64) / 32768


def make_lecture(out: Path, seconds: float, seed: int, tts: bool = True) -> Path:
    """Render a synthetic lecture of exactly `seconds` to `out` (H.264 1280x720 25 fps, AAC 48 kHz mono)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(SLIDES))
    n_samples, n_frames = round(seconds * SR), round(seconds * FPS)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        audio, schedule, used_tts = [], [], False  # schedule: (start sample, slide index, length)
        total, k = 0, 0
        while total < n_samples:
            idx = int(order[k % len(order)])
            title, bullets = SLIDES[idx]
            speech = _tts(". ".join([title, *bullets]) + ".", VOICES[seed % len(VOICES)], tmp) if tts else None
            if speech is None:
                speech = _babble(rng, [title, *bullets])
            else:
                used_tts = True
                speech = np.concatenate([np.zeros(int(0.4 * SR)), speech, np.zeros(int(0.5 * SR))])
            schedule.append((total, idx, len(speech)))
            audio.append(speech)
            total += len(speech)
            k += 1
        if tts and not used_tts:
            print("text-to-speech unavailable; used speech-like audio instead", file=sys.stderr)
        x = np.concatenate(audio)[:n_samples]
        wav = tmp / "audio.wav"
        wavfile.write(wav, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))

        slides = {i: _slide(*SLIDES[i], number=i + 1, accent=ACCENTS[i % len(ACCENTS)]) for _, i, _ in schedule}
        hop = SR // FPS
        loud = np.sqrt(np.convolve(x ** 2, np.ones(hop) / hop, mode="same"))[::hop]
        loud = loud / (loud.max() + 1e-9)

        cmd = [tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
               "-r", str(FPS), "-i", "-", "-i", str(wav), "-map", "0:v", "-map", "1:a",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-ar", str(SR), "-ac", "1", "-movflags", "+faststart", str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        x0, y0, x1, y1 = BOX
        for n in range(n_frames):
            s = n * hop
            start, idx, length = next(e for e in reversed(schedule) if e[0] <= s)
            frame = slides[idx].copy()
            progress = (s - start) / length
            line = min(3, int(progress * 4))
            px = int(104 + (progress * 4 - line) * 760)
            cv2.circle(frame, (px, 170 + line * 92 + 64), 9, (220, 40, 40), -1, cv2.LINE_AA)
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2 + int(6 * np.sin(n / 9))
            cv2.ellipse(frame, (cx, cy), (52, 66), 0, 0, 360, (224, 180, 150), -1, cv2.LINE_AA)
            mouth = 2 + int(14 * loud[min(n, len(loud) - 1)])
            cv2.ellipse(frame, (cx, cy + 34), (16, mouth // 2 + 1), 0, 0, 360, (90, 40, 40), -1, cv2.LINE_AA)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err[-2000:]}")
    return out

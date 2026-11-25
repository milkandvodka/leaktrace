"""ffmpeg in and out: probing, streaming frames and audio, and the HLS encoder for a rendition.

Everything streams: frames come one at a time from an ffmpeg pipe, so a title never sits in memory.
"""
import json
import queue
import subprocess
import tempfile
import threading
import wave
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterator

import numpy as np

from .config import HEIGHT, SR, WIDTH, tool
from .grid import snap_fps


class MediaError(RuntimeError):
    pass


def run(args: list, input: bytes | None = None) -> bytes:
    """Run ffmpeg with args; return stdout or raise MediaError with the end of stderr."""
    r = subprocess.run([tool("ffmpeg"), "-v", "error", "-y", *map(str, args)], input=input, capture_output=True)
    if r.returncode:
        raise MediaError(r.stderr.decode(errors="replace")[-2000:])
    return r.stdout


@dataclass
class Probe:
    duration: float
    has_audio: bool
    has_video: bool
    width: int
    height: int
    rate: Fraction        # exact frame rate, snapped to a broadcast rate when close
    rotation: int
    channels: int = 0


def probe(path: Path) -> Probe:
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True)
    if r.returncode:
        raise MediaError(r.stderr[-2000:] or f"can't read {path}")
    info = json.loads(r.stdout)
    streams = info.get("streams", [])
    v = next((s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    rotation, rate = 0, Fraction(0)
    if v:
        for sd in v.get("side_data_list", []):
            if "rotation" in sd:
                rotation = int(round(float(sd["rotation"])))
        if not rotation and "rotate" in v.get("tags", {}):
            rotation = int(v["tags"]["rotate"])
        for field in ("avg_frame_rate", "r_frame_rate"):
            num, den = (int(x) for x in v.get(field, "0/1").split("/"))
            if num and den:
                rate = snap_fps(Fraction(num, den))
                break
    a = next((s for s in streams if s["codec_type"] == "audio"), None)
    return Probe(duration=float(info.get("format", {}).get("duration", 0) or 0),
                 has_audio=any(s["codec_type"] == "audio" for s in streams), has_video=v is not None,
                 width=int(v["width"]) if v else 0, height=int(v["height"]) if v else 0, rate=rate, rotation=rotation,
                 channels=int(a.get("channels", 1)) if a else 0)


def content_box(path: Path, w: int = WIDTH, h: int = HEIGHT) -> tuple[int, int, int, int]:
    """Where the picture lands inside the w x h canvas after the fit-and-pad that frames() applies: (x, y, w, h)."""
    p = probe(path)
    dw, dh = (p.height, p.width) if abs(p.rotation) % 180 == 90 else (p.width, p.height)
    s = min(w / dw, h / dh)
    cw, ch = int(dw * s) // 2 * 2, int(dh * s) // 2 * 2
    return (w - cw) // 2, (h - ch) // 2, cw, ch


def _fit(w: int, h: int, fps: float) -> str:
    return (f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p")


def frames(path: Path, w: int, h: int, fps, start: float = 0.0,
           dur: float | None = None) -> Iterator[np.ndarray]:
    """YUV420 frames as (h * 3 / 2, w) uint8 arrays: Y rows, then U, then V. Rotation metadata is applied,
    the frame rate made constant, and the picture fitted inside w x h with black padding (never stretched)."""
    cmd = [tool("ffmpeg"), "-v", "error", *(["-ss", str(start)] if start else []), "-i", str(path),
           *(["-t", str(dur)] if dur is not None else []), "-map", "0:v:0", "-vf", _fit(w, h, fps),
           "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    size = w * h * 3 // 2
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        finished = False
        try:
            while len(buf := proc.stdout.read(size)) == size:
                yield np.frombuffer(buf, np.uint8).reshape(h * 3 // 2, w)
            finished = True
        finally:  # also runs when the caller stops early: then ffmpeg is just stopped, not an error
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        if finished and proc.returncode:
            err.seek(0)
            raise MediaError(err.read().decode(errors="replace")[-2000:])


def audio(path: Path, sr: int = SR, start: float = 0.0, dur: float | None = None) -> np.ndarray:
    """Mono float32 at sr. Silence (of the right length) when the file has no audio track."""
    p = probe(path)
    if not p.has_audio:
        length = dur if dur is not None else max(0.0, p.duration - start)
        return np.zeros(round(length * sr), np.float32)
    raw = run([*(["-ss", start] if start else []), "-i", path, *(["-t", dur] if dur is not None else []),
               "-map", "0:a:0", "-ac", "1", "-ar", sr, "-f", "f32le", "-"])
    x = np.frombuffer(raw, np.float32).copy()
    if dur is not None:
        n = round(dur * sr)
        x = x[:n] if len(x) >= n else np.pad(x, (0, n - len(x)))
    return x


class AudioReader:
    """float32 audio read front to back through one ffmpeg pipe (no whole-title buffer): (n,) for mono, (n, c)
    with channels. Gives silence when the file has no audio track, and zero-pads past the end."""

    def __init__(self, path: Path, sr: int = SR, channels: int = 1):
        self._proc, self.c = None, max(1, channels)
        if probe(path).has_audio:
            self._proc = subprocess.Popen([tool("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac",
                                           str(self.c), "-ar", str(sr), "-f", "f32le", "-"],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, n: int) -> np.ndarray:
        shape = (max(n, 0),) if self.c == 1 else (max(n, 0), self.c)
        if self._proc is None or n <= 0:
            return np.zeros(shape, np.float32)
        x = np.frombuffer(self._proc.stdout.read(4 * n * self.c), np.float32).reshape(-1, *shape[1:])
        return np.pad(x, ((0, n - len(x)), *[(0, 0)] * (x.ndim - 1))) if len(x) < n else x.copy()

    def close(self) -> None:
        if self._proc:
            self._proc.stdout.close()
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()


class Encoder:
    """A marked master: one MP4 (H.264 at high quality, AAC) with an IDR frame at the start of every segment.

    Frames stream into x264 with the IDR frames forced on the grid and scene-cut detection off, so both variants
    have their cuts in the same places and every later encode (the ladder, or a platform's own) can cut there too.
    Audio goes to a WAV on disk and is muxed in when the encoder closes, with the video stream-copied.
    """

    def __init__(self, out: Path, size: tuple[int, int], fps, seg_frames: int, channels: int = 1, crf: int = 16,
                 preset: str = "medium"):
        self.out = Path(out)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.with_audio, self.channels = channels > 0, channels
        w, h = size
        self._video = self.out.with_suffix(".video.mkv")
        self._wav_path = self.out.with_suffix(".audio.wav")
        self._err = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            [tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}",
             "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
             "-g", str(seg_frames), "-keyint_min", str(seg_frames), "-sc_threshold", "0",
             "-force_key_frames", f"expr:eq(mod(n,{seg_frames}),0)", str(self._video)],
            stdin=subprocess.PIPE, stderr=self._err)
        self._wav = None
        if self.with_audio:
            self._wav = wave.open(str(self._wav_path), "wb")
            self._wav.setnchannels(channels)
            self._wav.setsampwidth(2)
            self._wav.setframerate(SR)
        # Frames go through a queue to a writer thread, so marking and both encoders run side by side instead of
        # waiting on each other through small OS pipe buffers.
        self._queue: queue.Queue = queue.Queue(maxsize=96)
        self._writer_error: BaseException | None = None
        self._writer = threading.Thread(target=self._drain, daemon=True)
        self._writer.start()

    def _drain(self) -> None:
        while (buf := self._queue.get()) is not None:
            if self._writer_error is None:
                try:
                    self._proc.stdin.write(buf)
                except BaseException as e:  # encoder died; surfaced on close
                    self._writer_error = e

    def write_frame(self, yuv: np.ndarray) -> None:
        if self._writer_error:
            raise MediaError(f"encoder stopped: {self._writer_error}")
        self._queue.put(np.ascontiguousarray(yuv, np.uint8).tobytes())

    def write_audio(self, pcm: np.ndarray) -> None:
        self._wav.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())

    def _stop_writer(self) -> None:
        self._queue.put(None)
        self._writer.join()

    def close(self) -> None:
        self._stop_writer()
        self._proc.stdin.close()
        if self._proc.wait():
            self._err.seek(0)
            raise MediaError(self._err.read().decode(errors="replace")[-2000:])
        if self._wav:
            self._wav.close()
        audio_in = ["-i", self._wav_path] if self.with_audio else []
        audio_out = ["-map", "1:a", "-c:a", "aac", "-b:a", f"{160 * self.channels}k", "-ar", SR,
                     "-ac", self.channels] if self.with_audio else []
        run(["-i", self._video, *audio_in, "-map", "0:v", *audio_out, "-c:v", "copy", "-movflags", "+faststart",
             self.out])
        self._video.unlink(missing_ok=True)
        self._wav_path.unlink(missing_ok=True)

    def abort(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
        self._writer_error = self._writer_error or MediaError("aborted")
        self._stop_writer()
        self._proc.wait()
        if self._wav:
            self._wav.close()
        for p in (self._video, self._wav_path):
            p.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self.abort()
        else:
            self.close()

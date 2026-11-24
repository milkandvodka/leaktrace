"""ffmpeg in and out: probing, streaming frames and audio, and the HLS encoder for a rendition.

Everything streams: frames come one at a time from an ffmpeg pipe, so a lecture never sits in memory.
"""
import json
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from .config import FPS, HEIGHT, SEG_FRAMES, SEG_SECONDS, SR, WIDTH, tool


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
    fps: float
    rotation: int


def probe(path: Path) -> Probe:
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True)
    if r.returncode:
        raise MediaError(r.stderr[-2000:] or f"can't read {path}")
    info = json.loads(r.stdout)
    streams = info.get("streams", [])
    v = next((s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    rotation, fps = 0, 0.0
    if v:
        for sd in v.get("side_data_list", []):
            if "rotation" in sd:
                rotation = int(round(float(sd["rotation"])))
        if not rotation and "rotate" in v.get("tags", {}):
            rotation = int(v["tags"]["rotate"])
        num, den = (float(x) for x in v.get("avg_frame_rate", "0/1").split("/"))
        fps = round(num / den, 3) if den else 0.0
    return Probe(duration=float(info.get("format", {}).get("duration", 0) or 0),
                 has_audio=any(s["codec_type"] == "audio" for s in streams), has_video=v is not None,
                 width=int(v["width"]) if v else 0, height=int(v["height"]) if v else 0, fps=fps, rotation=rotation)


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


def frames(path: Path, w: int = WIDTH, h: int = HEIGHT, fps: float = FPS, start: float = 0.0,
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


class Encoder:
    """One rendition as HLS: 1.92 s TS segments named 00000.ts... and index.m3u8.

    Frames stream into x264 with an IDR frame forced every 48 frames and scene-cut detection off, so every
    rendition of a lecture has its cuts in the same places. Audio goes to a WAV on disk and is muxed in when the
    encoder closes; the video is stream-copied then, so the segment boundaries stay on those IDR frames.
    """

    def __init__(self, out_dir: Path, with_audio: bool = True, crf: int = 20, preset: str = "medium"):
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.with_audio = with_audio
        self._video = self.dir / "_video.mkv"
        self._wav_path = self.dir / "_audio.wav"
        self._err = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            [tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{WIDTH}x{HEIGHT}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
             "-g", str(SEG_FRAMES), "-keyint_min", str(SEG_FRAMES), "-sc_threshold", "0",
             "-force_key_frames", f"expr:eq(mod(n,{SEG_FRAMES}),0)", str(self._video)],
            stdin=subprocess.PIPE, stderr=self._err)
        self._wav = None
        if with_audio:
            self._wav = wave.open(str(self._wav_path), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(SR)

    def write_frame(self, yuv: np.ndarray) -> None:
        self._proc.stdin.write(np.ascontiguousarray(yuv, np.uint8).tobytes())

    def write_audio(self, pcm: np.ndarray) -> None:
        self._wav.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())

    def close(self) -> None:
        self._proc.stdin.close()
        if self._proc.wait():
            self._err.seek(0)
            raise MediaError(self._err.read().decode(errors="replace")[-2000:])
        if self._wav:
            self._wav.close()
        audio_in = ["-i", self._wav_path] if self.with_audio else []
        audio_out = ["-map", "1:a", "-c:a", "aac", "-b:a", "96k", "-ar", SR, "-ac", 1] if self.with_audio else []
        run(["-i", self._video, *audio_in, "-map", "0:v", *audio_out, "-c:v", "copy", "-f", "hls",
             "-hls_time", SEG_SECONDS, "-hls_playlist_type", "vod", "-hls_list_size", 0,
             "-hls_segment_filename", self.dir / "%05d.ts", self.dir / "index.m3u8"])
        self._video.unlink(missing_ok=True)
        self._wav_path.unlink(missing_ok=True)

    def abort(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
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

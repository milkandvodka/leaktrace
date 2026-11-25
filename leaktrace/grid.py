"""A title's segment grid: whole frames at the title's own frame rate, kept as an exact fraction.

Segment n starts at frame n * seg_frames and at time n * seg_frames / fps. Every boundary is computed from n on
the exact timeline (never by adding up rounded segment lengths), so a 29.97 fps title is still frame- and
sample-exact after hours. Audio is 48 kHz; a boundary that falls between two samples is rounded on its own.
"""
import os
from dataclasses import dataclass
from fractions import Fraction

from .config import SR

MAX_FPS = 60
_BROADCAST = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30), Fraction(50),
              Fraction(60000, 1001), Fraction(60)]


def snap_fps(rate: Fraction) -> Fraction:
    """A source's frame rate as an exact fraction: the broadcast rate it is within 0.1 % of, otherwise rounded to
    1/1000 fps. Above 60 fps it is halved until it isn't (every other frame is kept)."""
    rate = Fraction(rate)
    while rate > MAX_FPS + Fraction(1, 2):
        rate /= 2
    for c in _BROADCAST:
        if abs(rate - c) < c / 1000:
            return c
    return Fraction(round(rate * 1000), 1000)


def segment_seconds() -> float:
    return float(os.getenv("LEAKTRACE_SEGMENT_SECONDS") or 2.0)


@dataclass(frozen=True)
class Grid:
    fps: Fraction
    seg_frames: int

    @classmethod
    def for_fps(cls, fps: Fraction, seconds: float | None = None) -> "Grid":
        fps = Fraction(fps)
        target = Fraction(seconds if seconds is not None else segment_seconds()).limit_denominator(1000)
        return cls(fps, max(1, round(fps * target)))

    @property
    def seconds(self) -> float:
        """Nominal segment length (exact boundaries come from time(n))."""
        return float(self.seg_frames / self.fps)

    def time(self, n: int) -> Fraction:
        return n * self.seg_frames / self.fps

    def sample(self, n: int) -> int:
        """First audio sample of segment n."""
        return round(self.time(n) * SR)

    def samples(self, n: int) -> int:
        return self.sample(n + 1) - self.sample(n)

    def to_json(self) -> dict:
        return {"fps": str(self.fps), "seg_frames": self.seg_frames}

    @classmethod
    def from_json(cls, d: dict) -> "Grid":
        return cls(Fraction(d["fps"]), int(d["seg_frames"]))


V1 = Grid(Fraction(25), 48)  # titles packaged before grids were per title: 1.92 s at 25 fps

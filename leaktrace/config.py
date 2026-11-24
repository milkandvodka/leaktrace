"""Settings from the environment, and the constants every module shares."""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The segment grid: 48 frames at 25 fps = 92,160 samples at 48 kHz = 90 AAC frames, so video frames,
# audio samples and AAC frames all end exactly on every segment boundary.
FPS = 25
SR = 48_000
SEG_FRAMES = 48
SEG_SAMPLES = 92_160
SEG_SECONDS = SEG_FRAMES / FPS
WIDTH, HEIGHT = 1280, 720
ANALYSIS_W, ANALYSIS_H = 320, 180  # picture carriers are low-frequency, so they're measured small
FP_SR = 8_000  # landmark fingerprints and timing use phone-band audio, which survives an 8 kHz voice codec


@dataclass(frozen=True)
class Budget:
    """How strong each carrier is. Starting values from the design; the attack bench tunes them."""
    brightness: float = 1.2      # luma code values
    tint: float = 1.2            # Cb/Cr code values
    saturation: float = 0.03     # fraction of chroma radius (smaller changes round away in flat colour)
    hue_deg: float = 1.5
    contrast: float = 0.010      # fraction around mid-grey
    patchwork_db: float = 0.7
    loudness_db: float = 0.25
    tilt_db: float = 0.4
    warp_s: float = 0.030        # peak time displacement
    max_speech_slope: float = 0.05  # largest local speed change while someone is talking
    bias_cutoff: float = 0.1     # Tardos bias kept in [t, 1 - t]


BUDGET = Budget()


def tool(name: str) -> str:
    """Path to ffmpeg or ffprobe: LEAKTRACE_FFMPEG / LEAKTRACE_FFPROBE, else whatever is on PATH."""
    path = os.getenv(f"LEAKTRACE_{name.upper()}") or shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found. Install ffmpeg 6+ or set LEAKTRACE_{name.upper()}.")
    return path


@dataclass(frozen=True)
class Settings:
    secret: bytes
    data: Path
    admin_token: str


def settings() -> Settings:
    try:
        secret = bytes.fromhex(os.getenv("LEAKTRACE_SECRET", ""))
    except ValueError:
        secret = b""
    if len(secret) < 16:
        raise RuntimeError("LEAKTRACE_SECRET must be at least 32 hex characters. Make one with: python -m leaktrace keygen")
    return Settings(secret=secret, data=Path(os.getenv("LEAKTRACE_DATA") or ROOT / "var" / "data"),
                    admin_token=os.getenv("LEAKTRACE_ADMIN_TOKEN", ""))

"""Attacks: what happens to a viewer's copy on its way to a Telegram group. Each takes a source file and an
output folder and returns the attacked file.

The phone-filming ones are simulations (keystone perspective, lens blur, sensor noise, colour cast, vignette,
frame-rate mismatch, a speaker into a room into a phone microphone). The real test is filming a screen, which
the trace page accepts.
"""
import random
from pathlib import Path

from .. import media
from ..grid import segment_seconds
from .. import deliver

V264 = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]


def _out(out_dir: Path, src: Path, tag: str, ext: str = "mp4") -> Path:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    return Path(out_dir) / f"{Path(src).stem}.{tag}.{ext}"


def telegram(src: Path, out_dir: Path, passes: int = 1) -> Path:
    """Telegram's own re-compression (480p H.264, 64 kbps mono AAC), once per group it's forwarded through."""
    cur = Path(src)
    for k in range(passes):
        nxt = _out(out_dir, src, f"telegram{k + 1}")
        media.run(["-i", cur, "-vf", "scale=-2:480", *V264, "-crf", 28, "-c:a", "aac", "-b:a", "64k", "-ac", 1,
                   "-ar", 44100, nxt])
        cur = nxt
    return cur


def p240(src, out_dir):
    out = _out(out_dir, src, "240p")
    media.run(["-i", src, "-vf", "scale=-2:240", *V264, "-crf", 32, "-c:a", "aac", "-b:a", "32k", "-ac", 1, out])
    return out


def crop70(src, out_dir):
    """Someone crops to the middle 70% (cutting the logo and the visible watermark)."""
    out = _out(out_dir, src, "crop70")
    media.run(["-i", src, "-vf", "crop=trunc(iw*0.7/2)*2:trunc(ih*0.7/2)*2", *V264, "-crf", 26, "-c:a", "aac", out])
    return out


def letterbox_portrait(src, out_dir):
    """Re-posted as a vertical 9:16 video with bars."""
    out = _out(out_dir, src, "portrait")
    media.run(["-i", src, "-vf", "scale=720:-2,pad=720:1280:(ow-iw)/2:(oh-ih)/2", *V264, "-crf", 28, "-c:a", "aac", out])
    return out


def speed125(src, out_dir):
    out = _out(out_dir, src, "speed125")
    media.run(["-i", src, "-filter:v", "setpts=PTS/1.25", "-filter:a", "atempo=1.25", *V264, "-crf", 26, "-c:a", "aac", out])
    return out


def screenrec(src, out_dir):
    """A desktop screen recorder: the player window inside a 1080p desktop, 30 fps, heavy compression."""
    out = _out(out_dir, src, "screenrec")
    media.run(["-i", src, "-filter_complex",
               "color=c=0x2b2f36:s=1920x1080:r=30[bg];[0:v]scale=1280:720[w];[bg][w]overlay=320:180:shortest=1,"
               "drawbox=x=320:y=900:w=1280:h=40:color=0x111111:t=fill[v]",
               "-map", "[v]", "-map", "0:a", *V264, "-crf", 30, "-c:a", "aac", "-b:a", "128k", "-ac", 2, out])
    return out


_CAMCORD_V = ("perspective=x0=40:y0=30:x1=W-25:y1=12:x2=18:y2=H-40:x3=W-55:y3=H-22:sense=destination,"
              "gblur=sigma=1.3,eq=gamma=1.1:saturation=0.85:contrast=0.92:brightness=0.02,"
              "colorbalance=rs=0.05:bs=-0.04,vignette=PI/5,noise=alls=9:allf=t,fps=30")
_ROOM_A = ("[0:a]highpass=f=200,lowpass=f=4000,aecho=0.8:0.5:35|60:0.35|0.25,volume=0.8[s];"
           "anoisesrc=color=pink:amplitude=0.012:r=48000[n];[s][n]amix=inputs=2:duration=first[a]")


def camcord(src, out_dir):
    """A phone filming a laptop screen: keystone, blur, colour cast, vignette, noise, 30 fps; the title audio
    goes out of laptop speakers, through the room and into the phone's microphone."""
    out = _out(out_dir, src, "camcord")
    media.run(["-i", src, "-filter_complex", f"[0:v]{_CAMCORD_V}[v];{_ROOM_A}", "-map", "[v]", "-map", "[a]",
               *V264, "-crf", 26, "-c:a", "aac", "-b:a", "64k", "-ac", 1, out])
    return out


def nokia(src, out_dir):
    """An old Nokia filming the screen: the camcord damage, then QVGA MPEG-4 at 15 fps and AMR-NB voice audio
    at 8 kHz in a .3gp file."""
    out = _out(out_dir, src, "nokia", "3gp")
    media.run(["-i", src, "-filter_complex", f"[0:v]{_CAMCORD_V},scale=320:240,fps=15[v];{_ROOM_A}",
               "-map", "[v]", "-map", "[a]", "-c:v", "mpeg4", "-q:v", 8, "-c:a", "libopencore_amrnb",
               "-ar", 8000, "-ac", 1, "-b:a", "12.2k", out])
    return out


def muted(src, out_dir):
    out = _out(out_dir, src, "muted")
    media.run(["-i", src, "-an", *V264, "-crf", 26, out])
    return out


def audio_only(src, out_dir):
    out = _out(out_dir, src, "audio", "m4a")
    media.run(["-i", src, "-vn", "-c:a", "aac", "-b:a", "64k", "-ac", 1, out])
    return out


ATTACKS = {
    "telegram": telegram,
    "telegram5": lambda s, o: telegram(s, o, passes=5),
    "p240": p240,
    "crop70": crop70,
    "letterbox_portrait": letterbox_portrait,
    "speed125": speed125,
    "screenrec": screenrec,
    "camcord": camcord,
    "nokia": nokia,
    "muted": muted,
    "audio_only": audio_only,
}


def collude_interleave(title_id: str, viewers: list[str], out_dir: Path, seed: int = 0) -> Path:
    """Viewers pool their copies and splice them segment by segment (each segment from a random one)."""
    rng = random.Random(seed)
    words = {v: deliver.bits(title_id, v) for v in viewers}
    n = len(words[viewers[0]])
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out = Path(out_dir) / f"interleave-{seed}.mp4"
    return deliver.assemble(title_id, ["A" if words[rng.choice(viewers)][k] else "B" for k in range(n)], out)


def collude_average(copies: list[Path], out_dir: Path) -> Path:
    """Two viewers average their copies frame by frame and sample by sample, hoping the marks cancel."""
    a, b = copies[:2]
    out = _out(out_dir, a, "average")
    media.run(["-i", a, "-i", b, "-filter_complex", "[0:v][1:v]blend=all_mode=average[v];[0:a][1:a]amix=inputs=2[a]",
               "-map", "[v]", "-map", "[a]", *V264, "-crf", 22, "-c:a", "aac", out])
    return out


def trim(src: Path, out_dir: Path, seconds: float, seed: int = 0) -> Path:
    """A random excerpt of `seconds` (re-encoded, the way it would be cut in an editor)."""
    dur = media.probe(src).duration
    if seconds >= dur - segment_seconds():
        return Path(src)
    start = random.Random(seed).uniform(0, dur - seconds)
    out = _out(out_dir, src, f"cut{int(seconds)}", Path(src).suffix.lstrip("."))
    p = media.probe(src)
    v = ["-c:v", "libx264", "-preset", "veryfast", "-crf", 24] if p.has_video else []
    a = ["-c:a", "aac", "-b:a", "96k"] if p.has_audio else []
    if Path(src).suffix == ".3gp":
        v = ["-c:v", "mpeg4", "-q:v", 8] if p.has_video else []
        a = ["-c:a", "libopencore_amrnb", "-ar", 8000, "-b:a", "12.2k"] if p.has_audio else []
    media.run(["-ss", round(start, 2), "-i", src, "-t", seconds, *v, *a, out])
    return out

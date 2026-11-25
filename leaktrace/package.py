"""CMAF output: the marked masters A and B become a quality ladder of fragmented-MP4 segments cut on the title's
grid, stored under unguessable names, behind one set of HLS and DASH manifests that every viewer shares.

    cmaf/master.m3u8, <track>.m3u8, manifest.mpd   shared manifests, pointing at <track>/NNNNN.m4s and <track>/init.mp4
    cmaf/<track>-init.mp4                          one init segment per track, byte-identical for A and B
    cmaf/<32 hex>.m4s                              every segment of every track, in A and in B
    cmaf/tracks.json                               the index: tracks, codecs, segment times and sizes

Video: each rung is encoded from the master with an IDR frame at every segment start and ffmpeg's HLS muxer cuts
there; no B-frames, so frames present at their decode times and no player depends on edit lists to keep audio and
video in sync. Audio: AAC from the master with the encoder's priming frame dropped (content starts at time 0), then
regrouped here so each segment starts at the AAC frame nearest the exact grid boundary (ffmpeg's own muxers let
audio segments drift by up to a frame per segment). The objects go to the
CDN's origin as they are (the whole cmaf/ folder); nothing in it says which variant an object is.
"""
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import bmff, manifests, media
from .edge import naming_key, object_name

DEFAULT_LADDER = "1080:5000,720:3000,480:1500,360:800"
AAC_DELAY = 1024           # ffmpeg's AAC encoder primes one frame (1024 samples) before the content starts
AUDIO_KBPS = {1: 96, 2: 128}
VIDEO_CRF = 21             # quality target; each rung's kbps caps it (capped CRF)


class PackagingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rung:
    name: str
    width: int
    height: int
    kbps: int


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


def _parse(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in spec.split(","):
        h, _, k = part.strip().partition(":")
        out.append((int(h), int(k) if k else round(5000 * (int(h) / 1080) ** 1.5)))
    return sorted(out, reverse=True)


def ladder(canvas, spec: str | None = None) -> list[Rung]:
    """The rungs for a title of this size, from LEAKTRACE_LADDER ("height:kbps,..."; height is the short side).
    Rungs above the title are skipped, never upscaled; when that skips something, the title's own size becomes
    the top rung (at the bitrate of the smallest skipped rung)."""
    w, h = canvas
    short = min(w, h)
    rungs = _parse(spec or os.getenv("LEAKTRACE_LADDER") or DEFAULT_LADDER)
    keep = [r for r in rungs if r[0] <= short]
    above = [r for r in rungs if r[0] > short]
    if above and (not keep or keep[0][0] < short):
        keep.insert(0, (short, min(above)[1]))
    out = []
    for r, kbps in keep:
        size = (r, _even(h * r / w)) if w < h else (_even(w * r / h), r)
        out.append(Rung(f"v{r}", *size, kbps))
    return out


def encode_video(master: Path, out: Path, rungs: list[Rung], seg_frames: int, seg_s: float, preset: str) -> None:
    """Every rung of one variant from its master, in one ffmpeg run: out/<rung>/init.mp4 and NNNNN.m4s."""
    n = seg_frames
    graph = (f"[0:v]split={len(rungs)}" + "".join(f"[s{i}]" for i in range(len(rungs))) + ";"
             + ";".join(f"[s{i}]scale={r.width}:{r.height}:flags=lanczos,setsar=1[o{i}]" for i, r in enumerate(rungs)))
    args = ["-i", master, "-filter_complex", graph]
    for i, r in enumerate(rungs):
        d = out / r.name
        d.mkdir(parents=True)
        args += ["-map", f"[o{i}]", "-c:v", "libx264", "-preset", preset, "-crf", VIDEO_CRF, "-maxrate", f"{r.kbps}k",
                 "-bufsize", f"{2 * r.kbps}k", "-pix_fmt", "yuv420p", "-bf", 0, "-g", n, "-keyint_min", n,
                 "-sc_threshold", 0,
                 "-force_key_frames", f"expr:eq(mod(n,{n}),0)", "-f", "hls", "-hls_segment_type", "fmp4",
                 "-hls_time", seg_s / 2,  # under a segment: a cut at every IDR frame, which is every segment start
                 "-hls_playlist_type", "vod", "-hls_list_size", 0, "-hls_fmp4_init_filename", d / "init.mp4",
                 "-hls_segment_filename", d / "%05d.m4s", d / "index.m3u8"]
    media.run(args)


def encode_audio(master: Path, out: Path, boundaries: list[int], channels: int) -> None:
    """One variant's audio track: out/audio/init.mp4 and one NNNNN.m4s per segment, starting at the AAC frame
    nearest each boundary (content samples)."""
    d = out / "audio"
    d.mkdir(parents=True)
    tmp = d / "_all.mp4"
    media.run(["-i", master, "-map", "0:a:0", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS[channels]}k", "-ac", channels,
               "-ar", 48000, "-f", "mp4", "-movflags", "+empty_moov+default_base_moof", "-frag_duration", 10_000_000,
               tmp])
    data = tmp.read_bytes()
    tmp.unlink()
    init = data[: next(e for k, _s, e in bmff.boxes(data) if k == b"moov")]
    frames = bmff.samples(data)
    size = frames[0][1]
    if size == AAC_DELAY:  # drop the priming frame: frame k then decodes content samples [k * size, (k + 1) * size)
        frames = [(t - size, d, b) for t, d, b in frames[1:]]
    cut = [0] + [min(len(frames), round(b / size)) for b in boundaries[1:]] + [len(frames)]
    (d / "init.mp4").write_bytes(init)
    tid = bmff.track_id(init)
    for n in range(len(boundaries)):
        if cut[n] < cut[n + 1]:
            (d / f"{n:05d}.m4s").write_bytes(bmff.media_segment(n + 1, tid, frames[cut[n]: cut[n + 1]]))


def publish(work: Path, out: Path, title_id: str, rungs: list[Rung], n_segments: int, channels: int, fps: str,
            duration: float, secret: bytes) -> dict:
    """Check that A and B line up track by track, move every segment to its object name, write the index and
    the shared manifests."""
    nk = naming_key(secret)
    out.mkdir(parents=True)
    tracks = []
    for tid in [r.name for r in rungs] + (["audio"] if channels else []):
        da, db = work / "A" / tid, work / "B" / tid
        init = (da / "init.mp4").read_bytes()
        if init != (db / "init.mp4").read_bytes():
            raise PackagingError(f"{tid}: the A and B init segments differ, so a player couldn't switch between them")
        info = bmff.init_info(init)
        segs = []
        for n in range(n_segments):
            fa, fb = da / f"{n:05d}.m4s", db / f"{n:05d}.m4s"
            if not (fa.exists() and fb.exists()):
                raise PackagingError(f"{tid}: segment {n} is missing (expected {n_segments})")
            sa, sb = (bmff.segment_info(f.read_bytes(), info.default_duration) for f in (fa, fb))
            if sa != sb:
                raise PackagingError(f"{tid}: segment {n} differs in timing between A and B")
            segs.append({"t": sa.start, "d": sa.duration, "frames": sa.samples,
                         "bytes": max(fa.stat().st_size, fb.stat().st_size)})  # never which is which
            os.replace(fa, out / f"{object_name(nk, title_id, tid, n, 'A')}.m4s")
            os.replace(fb, out / f"{object_name(nk, title_id, tid, n, 'B')}.m4s")
        (out / f"{tid}-init.mp4").write_bytes(init)
        rung = next((r for r in rungs if r.name == tid), None)
        entry = {"id": tid, "kind": "video" if rung else "audio", "codec": info.codec, "timescale": info.timescale,
                 "media_time": info.media_time, "segments": segs}
        entry.update({"width": rung.width, "height": rung.height, "kbps": rung.kbps} if rung else
                     {"channels": channels, "kbps": AUDIO_KBPS[channels]})
        tracks.append(entry)
    index = {"title": title_id, "fps": fps, "duration": duration, "tracks": tracks}
    (out / "tracks.json").write_text(json.dumps(index), encoding="utf-8")
    manifests.write_shared(out, index)
    shutil.rmtree(work)
    return index

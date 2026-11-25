"""HLS and DASH manifests for a packaged title, from its tracks.json index.

The shared set (every viewer, CDN edge switching) points at virtual paths <track>/NNNNN.m4s and <track>/init.mp4,
which the edge maps to that viewer's objects. Per-viewer manifests point at the objects themselves; they are the
same documents with other URLs, so both are made here from the same index.
"""
import math
from fractions import Fraction
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import quoteattr

SegUri = Callable[[str, int], str]   # (track, n) -> URI
InitUri = Callable[[str], str]       # track -> URI


def virtual_segment(track: str, n: int) -> str:
    return f"{track}/{n:05d}.m4s"


def virtual_init(track: str) -> str:
    return f"{track}/init.mp4"


def _video(index):
    return [t for t in index["tracks"] if t["kind"] == "video"]


def _audio(index):
    return next((t for t in index["tracks"] if t["kind"] == "audio"), None)


def _rates(t) -> tuple[int, int]:
    """(peak, average) bits per second over the track's segments (each the larger of its A and B)."""
    peak, total, secs = 0, 0, 0.0
    for s in t["segments"]:
        size, dur = s["bytes"], s["d"] / t["timescale"]
        if dur > 0.25 * (t["segments"][0]["d"] / t["timescale"]):  # a short final segment overstates the peak
            peak = max(peak, 8 * size / dur)
        total, secs = total + size, secs + dur
    return math.ceil(peak or 8 * total / max(secs, 1e-9)), math.ceil(8 * total / max(secs, 1e-9))


def hls_master(index: dict, media_uri: Callable[[str], str] = lambda t: f"{t}.m3u8") -> str:
    audio = _audio(index)
    a_peak, a_avg = _rates(audio) if audio else (0, 0)
    fps = float(Fraction(index["fps"]))
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    if audio:
        lines.append(f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Main",DEFAULT=YES,AUTOSELECT=YES,'
                     f'CHANNELS="{audio["channels"]}",URI="{media_uri(audio["id"])}"')
    for v in _video(index):
        peak, avg = _rates(v)
        codecs = v["codec"] + (f",{audio['codec']}" if audio else "")
        lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={peak + a_peak},AVERAGE-BANDWIDTH={avg + a_avg},CODECS="{codecs}",'
                     f'RESOLUTION={v["width"]}x{v["height"]},FRAME-RATE={fps:.3f}' + (',AUDIO="audio"' if audio else ""))
        lines.append(media_uri(v["id"]))
    return "\n".join(lines) + "\n"


def hls_media(index: dict, track: str, seg_uri: SegUri = virtual_segment, init_uri: InitUri = virtual_init) -> str:
    t = next(x for x in index["tracks"] if x["id"] == track)
    ts = t["timescale"]
    lines = ["#EXTM3U", "#EXT-X-VERSION:7",
             f"#EXT-X-TARGETDURATION:{math.ceil(max(s['d'] for s in t['segments']) / ts - 1e-9)}",
             "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS",
             f'#EXT-X-MAP:URI="{init_uri(track)}"']
    for n, s in enumerate(t["segments"]):
        lines += [f"#EXTINF:{s['d'] / ts:.6f},", seg_uri(track, n)]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def _timeline(t) -> str:
    out, segs = [], t["segments"]
    i = 0
    while i < len(segs):
        j = i
        while j + 1 < len(segs) and segs[j + 1]["d"] == segs[i]["d"] and segs[j + 1]["t"] == segs[j]["t"] + segs[j]["d"]:
            j += 1
        out.append(f'<S t="{segs[i]["t"]}" d="{segs[i]["d"]}"' + (f' r="{j - i}"' if j > i else "") + "/>")
        i = j + 1
    return "<SegmentTimeline>" + "".join(out) + "</SegmentTimeline>"


def _segments(t, seg_uri: SegUri | None, init_uri: InitUri) -> str:
    """SegmentTemplate on the virtual numbered paths (shared), or a SegmentList of explicit URLs (per viewer)."""
    common = f'timescale="{t["timescale"]}" presentationTimeOffset="{t["media_time"]}"'
    if seg_uri is None:
        return (f'<SegmentTemplate {common} startNumber="0" initialization="$RepresentationID$/init.mp4" '
                f'media="$RepresentationID$/$Number%05d$.m4s">{_timeline(t)}</SegmentTemplate>')
    urls = "".join(f"<SegmentURL media={quoteattr(seg_uri(t['id'], n))}/>" for n in range(len(t["segments"])))
    return (f"<SegmentList {common}><Initialization sourceURL={quoteattr(init_uri(t['id']))}/>{_timeline(t)}{urls}"
            "</SegmentList>")


def dash(index: dict, seg_uri: SegUri | None = None, init_uri: InitUri = virtual_init) -> str:
    video, audio = _video(index), _audio(index)
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-live:2011" '
             f'type="static" mediaPresentationDuration="PT{index["duration"]:.3f}S" minBufferTime="PT2S">',
             '<Period id="0" start="PT0S">']
    if video:
        shared = seg_uri is None  # a template serves every rung; explicit lists are per representation
        parts.append(f'<AdaptationSet id="0" contentType="video" mimeType="video/mp4" segmentAlignment="true" '
                     f'startWithSAP="1" frameRate="{index["fps"]}">')
        if shared:
            parts.append(_segments(video[0], None, init_uri))
        for v in video:
            peak, _avg = _rates(v)
            rep = (f'<Representation id="{v["id"]}" codecs="{v["codec"]}" bandwidth="{peak}" width="{v["width"]}" '
                   f'height="{v["height"]}" sar="1:1"')
            parts.append(rep + ("/>" if shared else f">{_segments(v, seg_uri, init_uri)}</Representation>"))
        parts.append("</AdaptationSet>")
    if audio:
        peak, _avg = _rates(audio)
        parts.append('<AdaptationSet id="1" contentType="audio" mimeType="audio/mp4" lang="und" segmentAlignment="true" '
                     'startWithSAP="1">')
        parts.append(f'<Representation id="{audio["id"]}" codecs="{audio["codec"]}" bandwidth="{peak}" '
                     'audioSamplingRate="48000"><AudioChannelConfiguration '
                     'schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011" '
                     f'value="{audio["channels"]}"/>{_segments(audio, seg_uri, init_uri)}</Representation>')
        parts.append("</AdaptationSet>")
    parts += ["</Period>", "</MPD>"]
    return "\n".join(parts) + "\n"


def write_shared(folder: Path, index: dict) -> None:
    (folder / "master.m3u8").write_text(hls_master(index), encoding="utf-8")
    for t in index["tracks"]:
        (folder / f"{t['id']}.m3u8").write_text(hls_media(index, t["id"]), encoding="utf-8")
    (folder / "manifest.mpd").write_text(dash(index), encoding="utf-8")

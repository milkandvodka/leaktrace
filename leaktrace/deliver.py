"""Per-viewer delivery on the shared CMAF objects.

- session(): the viewer's edge token and manifest URLs (/wm/<token>/<title>/master.m3u8 and manifest.mpd). This
  server answers those paths itself (edge.route on local files), so it works with no CDN; in production a CDN edge
  function (edge/) does the same mapping in front of a bucket holding the cmaf/ folders.
- viewer_manifest(): the same manifests listing that viewer's objects directly (for CDNs without edge code).
- export(): the viewer's own copy as one MP4 (their variant of every segment), cached.
"""
import base64
import json
import os
import threading
import time
from pathlib import Path

from . import edge, manifests, media
from .codes import codeword
from .config import settings
from .ingest import TitleMeta, title_dir

TOKEN_TTL = 6 * 3600
_export_lock = threading.Lock()


def _keys():
    s = settings().secret
    return edge.edge_key(s), edge.naming_key(s)


def bits(title_id: str, viewer: str) -> list[bool]:
    """The viewer's variant for every segment of the title (True = A)."""
    meta = TitleMeta.load(title_dir(title_id))
    return [bool(b) for b in codeword(settings().secret, meta.id, viewer, meta.n_segments)]


def index(title_id: str) -> dict:
    return json.loads((title_dir(title_id) / "cmaf" / "tracks.json").read_text(encoding="utf-8"))


def token(title_id: str, viewer: str, ttl: int = TOKEN_TTL) -> str:
    return edge.make_token(_keys()[0], title_id, bits(title_id, viewer), int(time.time()) + ttl)


def session(title_id: str, viewer: str, ttl: int = TOKEN_TTL) -> dict:
    t = token(title_id, viewer, ttl)
    return {"token": t, "hls": f"/wm/{t}/{title_id}/master.m3u8", "dash": f"/wm/{t}/{title_id}/manifest.mpd",
            "expires": int(time.time()) + ttl}


def resolve(path: str) -> Path:
    """The local file for an edge path `<token>/<title>/<file>` (edge.Denied for a bad token or unknown file)."""
    ek, nk = _keys()
    title, obj = edge.route(path, ek, nk, time.time()).split("/", 1)
    return title_dir(title) / "cmaf" / obj


def assemble(title_id: str, variants: list[str], out: Path) -> Path:
    """One MP4 from the top rung and the audio, taking variants[n] ("A" or "B") for segment n."""
    folder, idx, nk = title_dir(title_id) / "cmaf", index(title_id), _keys()[1]
    video = next(t for t in idx["tracks"] if t["kind"] == "video")
    audio = next((t for t in idx["tracks"] if t["kind"] == "audio"), None)
    parts = []
    for t in [video] + ([audio] if audio else []):
        p = out.with_name(f"{out.stem}.{t['id']}.mp4")
        with open(p, "wb") as f:
            f.write((folder / f"{t['id']}-init.mp4").read_bytes())
            for n in range(len(t["segments"])):
                f.write((folder / f"{edge.object_name(nk, title_id, t['id'], n, variants[n])}.m4s").read_bytes())
        parts.append(p)
    try:
        media.run([*sum((["-i", p] for p in parts), []), *sum((["-map", f"{i}:0"] for i in range(len(parts))), []),
                   "-c", "copy", "-movflags", "+faststart", out])
    finally:
        for p in parts:
            p.unlink(missing_ok=True)
    return out


def export(title_id: str, viewer: str) -> Path:
    """The viewer's own copy as one MP4 (cached)."""
    out = title_dir(title_id) / "exports" / f"{base64.urlsafe_b64encode(viewer.encode()).rstrip(b'=').decode()}.mp4"
    with _export_lock:
        if not out.exists():
            out.parent.mkdir(exist_ok=True)
            tmp = out.with_name(out.stem + ".part.mp4")
            try:
                assemble(title_id, ["A" if b else "B" for b in bits(title_id, viewer)], tmp)
                tmp.replace(out)
            finally:
                tmp.unlink(missing_ok=True)
    return out


HLS, DASH = "application/vnd.apple.mpegurl", "application/dash+xml"


def media_url() -> str:
    """Where per-viewer manifests point for objects: the CDN in front of the bucket holding each title's cmaf/
    folder at /<title>/ (LEAKTRACE_MEDIA_URL), or this server's own /media."""
    return (os.getenv("LEAKTRACE_MEDIA_URL") or "/media").rstrip("/")


def viewer_manifest(path: str) -> tuple[str, str]:
    """(text, media type) of a manifest for `<token>/<title>/<file>` that lists the token's own objects directly,
    for CDNs that can't run edge code. Raises edge.Denied: 403 for a bad or expired token, 404 for anything else."""
    parts = path.split("/", 2)
    if len(parts) < 3 or not edge.TITLE.match(parts[1]):
        raise edge.Denied(404, "not found")
    token, title, file = parts
    ek, nk = _keys()
    bits_ = edge.check(token, title, ek, time.time())
    if not edge.MANIFEST.match(file) or not (title_dir(title) / "cmaf" / "tracks.json").exists():
        raise edge.Denied(404, "not found")
    idx, base = index(title), f"{media_url()}/{title}"

    def seg(track: str, n: int) -> str:
        variant = "A" if n < 8 * len(bits_) and bits_[n >> 3] & (0x80 >> (n & 7)) else "B"
        return f"{base}/{edge.object_name(nk, title, track, n, variant)}.m4s"

    def init(track: str) -> str:
        return f"{base}/{track}-init.mp4"

    if file == "master.m3u8":
        return manifests.hls_master(idx), HLS
    if file == "manifest.mpd":
        return manifests.dash(idx, seg, init), DASH
    if file[:-5] not in {t["id"] for t in idx["tracks"]}:
        raise edge.Denied(404, "not found")
    return manifests.hls_media(idx, file[:-5], seg, init), HLS

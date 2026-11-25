"""Per-viewer delivery: a signed HLS playlist and a downloadable MP4, both following the viewer's codeword.

Segment URLs are opaque signed tokens carrying (title, segment, viewer, expiry), but not the variant. The
server works the variant out from the secret code when it serves the segment, so a viewer can neither see their
A/B pattern in URLs nor fetch the other variant.
"""
import base64
import hashlib
import hmac
import threading
import time
from pathlib import Path

from . import media
from .codes import codeword
from .config import settings
from .ingest import TitleMeta, title_dir

_export_lock = threading.Lock()


class TokenError(ValueError):
    pass


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: bytes) -> bytes:
    return hmac.new(settings().secret, b"seg\x1f" + payload, hashlib.sha256).digest()[:16]


def _token(title: str, seg: int, viewer: str, exp: int) -> str:
    payload = "\x1f".join([title, str(seg), viewer, str(exp)]).encode()
    return _b64(payload) + "." + _b64(_sign(payload))


def _variant(meta: TitleMeta, viewer: str, seg: int) -> str:
    if seg >= meta.marked_segments:
        return "A"  # the unmarked tail is the same in both
    return "A" if codeword(settings().secret, meta.id, viewer, meta.n_segments)[seg] else "B"


def playlist(title_id: str, viewer: str, base_url: str, ttl: int = 3600) -> str:
    """The viewer's HLS media playlist; segment URIs are base_url + /s/<token>, valid for ttl seconds."""
    src = (title_dir(title_id) / "A" / "index.m3u8").read_text().splitlines()
    exp = int(time.time()) + ttl
    out, seg = [], 0
    for line in src:
        if line.endswith(".ts"):
            out.append(f"{base_url.rstrip('/')}/s/{_token(title_id, seg, viewer, exp)}")
            seg += 1
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def resolve(token: str) -> Path:
    """The segment file a token stands for, after checking its signature and expiry."""
    try:
        head, sig = token.split(".")
        payload = _unb64(head)
        if not hmac.compare_digest(_unb64(sig), _sign(payload)):
            raise TokenError("bad signature")
        title, seg, viewer, exp = payload.decode().split("\x1f")
        seg, exp = int(seg), int(exp)
    except TokenError:
        raise
    except Exception as e:
        raise TokenError("malformed token") from e
    if exp < time.time():
        raise TokenError("expired")
    meta = TitleMeta.load(title_dir(title))
    if not 0 <= seg < meta.n_segments:
        raise TokenError("no such segment")
    return title_dir(title) / _variant(meta, viewer, seg) / f"{seg:05d}.ts"


def export(title_id: str, viewer: str) -> Path:
    """The viewer's own copy as one MP4 (cached): their segments, stream-copied in order."""
    folder = title_dir(title_id)
    out = folder / "exports" / f"{_b64(viewer.encode())}.mp4"
    with _export_lock:
        if out.exists():
            return out
        meta = TitleMeta.load(folder)
        out.parent.mkdir(exist_ok=True)
        # A local playlist of this viewer's segments: read the way a player reads it, the segments' own continuous
        # timestamps are kept (the concat demuxer would add up per-file durations and drift).
        listing = out.with_suffix(".m3u8")
        lines, seg = [], 0
        for line in (folder / "A" / "index.m3u8").read_text().splitlines():
            if line.endswith(".ts"):
                line = (folder / _variant(meta, viewer, seg) / f"{seg:05d}.ts").as_posix()
                seg += 1
            lines.append(line)
        listing.write_text("\n".join(lines) + "\n")
        tmp = out.with_suffix(".part.mp4")
        try:
            media.run(["-protocol_whitelist", "file", "-i", listing, "-c", "copy",
                       *(["-bsf:a", "aac_adtstoasc"] if meta.has_audio else []), "-movflags", "+faststart", tmp])
            tmp.replace(out)
        finally:
            listing.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
        return out

"""Per-viewer A/B switching on shared objects: the logic a CDN edge runs (edge/core.js is the same in JavaScript).

Every viewer gets the same manifests and fetches the same URLs, under a path prefix holding their token:

    /wm/<token>/<title>/master.m3u8        shared HLS manifests (and manifest.mpd for DASH)
    /wm/<token>/<title>/<track>/init.mp4    shared init segment  -> <title>/<track>-init.mp4
    /wm/<token>/<title>/<track>/NNNNN.m4s   segment n            -> <title>/<HMAC name of track, n, A or B>.m4s

The token is <expiry>.<bits>.<signature>: the viewer's A/B decision for every segment of this title (bit n set =
A), signed with the edge key over the title, so it works for that title only, until it expires, and can't be
edited. The edge looks up bit n and fetches that variant's object. Objects are named with a second key (the
naming key), so knowing one variant's URL tells nobody where the other is. Segment objects are identical for
everyone who has the same bit, so CDN caching works as usual; only the token in the path is per viewer.

Both keys are derived from LEAKTRACE_SECRET; the edge holds only them, never the secret itself (which also makes
the codewords).

A platform that encodes and packages the marked masters with its own tools uses layout "folders" instead: variant A
under <title>/A/ and B under <title>/B/ with the same file names. Manifests and init segments come from A/; a
segment's number is the last number in its file name, counted from `first`.
"""
import base64
import hashlib
import hmac
import re
from typing import Sequence

TITLE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MANIFEST = re.compile(r"^(master\.m3u8|manifest\.mpd|[a-z0-9]+\.m3u8)$")
INIT = re.compile(r"^([a-z0-9]+)/init\.mp4$")
SEGMENT = re.compile(r"^([a-z0-9]+)/(\d{5,6})\.m4s$")
FOLDER_SEGMENT = re.compile(r"(\d+)\.(m4s|mp4|ts|cmfv|cmfa|aac|m4v|m4a)$")


class Denied(Exception):
    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status = status


def _derive(secret: bytes, label: bytes) -> bytes:
    return hmac.new(secret, b"leaktrace " + label + b" v1", hashlib.sha256).digest()


def edge_key(secret: bytes) -> bytes:
    """Signs and checks viewer tokens."""
    return _derive(secret, b"edge")


def naming_key(secret: bytes) -> bytes:
    """Names the stored segment objects."""
    return _derive(secret, b"names")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s + "=" * (-len(s) % 4), altchars=b"-_", validate=True)


def _mac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()[:16]


def object_name(nk: bytes, title: str, track: str, n: int, variant: str) -> str:
    return _mac(nk, f"{title}|{track}|{n}|{variant}").hex()


def pack(bits: Sequence[bool]) -> str:
    out = bytearray((len(bits) + 7) // 8)
    for n, b in enumerate(bits):
        if b:
            out[n >> 3] |= 0x80 >> (n & 7)
    return _b64(bytes(out))


def make_token(ek: bytes, title: str, bits: Sequence[bool], exp: int) -> str:
    packed = pack(bits)
    return f"{int(exp)}.{packed}.{_b64(_mac(ek, f'{title}|{int(exp)}|{packed}'))}"


def check(token: str, title: str, ek: bytes, now: float) -> bytes:
    """The token's packed bits, after checking signature, title and expiry; raises Denied(403)."""
    try:
        exp, packed, sig = token.split(".")
        if not re.fullmatch(r"[0-9]{1,12}", exp):
            raise ValueError
        good = hmac.compare_digest(sig, _b64(_mac(ek, f"{title}|{exp}|{packed}")))
        bits = _unb64(packed)
    except Exception:
        raise Denied(403, "malformed token") from None
    if not good:
        raise Denied(403, "bad signature")
    if int(exp) < now:
        raise Denied(403, "expired")
    return bits


def variant(bits: bytes, n: int) -> str:
    return "A" if bits[n >> 3] & (0x80 >> (n & 7)) else "B"


def _folders(title: str, file: str, bits: bytes, first: int) -> str:
    parts = file.split("/")
    if any(p in ("", ".", "..") for p in parts) or "\\" in file:
        raise Denied(404, "not found")
    name = parts[-1]
    if name.endswith((".m3u8", ".mpd")) or "init" in name:
        return f"{title}/A/{file}"
    m = FOLDER_SEGMENT.search(name)
    n = int(m[1]) - first if m else -1
    if not 0 <= n < 8 * len(bits):
        raise Denied(404, "not found")
    return f"{title}/{variant(bits, n)}/{file}"


def route(path: str, ek: bytes, nk: bytes, now: float, layout: str = "cmaf", first: int = 0) -> str:
    """The origin object for a request path `<token>/<title>/<file>`: raises Denied(403) for a bad, altered or
    expired token, Denied(404) for anything that isn't a manifest, init or segment."""
    parts = path.split("/", 2)
    if len(parts) < 3 or not TITLE.match(parts[1]):
        raise Denied(404, "not found")
    token, title, file = parts
    bits = check(token, title, ek, now)
    if layout == "folders":
        return _folders(title, file, bits, first)
    if MANIFEST.match(file):
        return f"{title}/{file}"
    if m := INIT.match(file):
        return f"{title}/{m[1]}-init.mp4"
    if not (m := SEGMENT.match(file)) or int(m[2]) >= 8 * len(bits):
        raise Denied(404, "not found")
    n = int(m[2])
    return f"{title}/{object_name(nk, title, m[1], n, variant(bits, n))}.m4s"

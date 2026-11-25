"""Just enough ISO-BMFF (MP4) reading to package CMAF: a track's timescale and codec from its init segment, and
each media segment's decode start, duration and sample count."""
import struct
from dataclasses import dataclass

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"mvex", b"moof", b"traf", b"edts", b"dinf"}


def boxes(data: bytes, start: int = 0, end: int | None = None):
    """(type, payload start, box end) for each box in data[start:end], descending into containers."""
    end = len(data) if end is None else end
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, i)
        head = 8
        if size == 1:
            size, head = struct.unpack_from(">Q", data, i + 8)[0], 16
        elif size == 0:
            size = end - i
        if size < head or i + size > end:
            raise ValueError(f"bad {kind!r} box at {i}")
        yield kind, i + head, i + size
        if kind in _CONTAINERS:
            yield from boxes(data, i + head, i + size)
        i += size


def _first(data: bytes, kind: bytes):
    return next(((s, e) for k, s, e in boxes(data) if k == kind), None)


@dataclass(frozen=True)
class Init:
    timescale: int
    codec: str            # RFC 6381 codecs string: avc1.PPCCLL or mp4a.40.2
    default_duration: int  # trex default sample duration (0 if unset)
    media_time: int       # edit-list start (composition delay of B-frames), in timescale units


def init_info(data: bytes) -> Init:
    s, _ = _first(data, b"mdhd")
    version = data[s]
    timescale = struct.unpack_from(">I", data, s + (20 if version == 1 else 12))[0]
    a = data.find(b"avcC")
    codec = f"avc1.{data[a + 5]:02x}{data[a + 6]:02x}{data[a + 7]:02x}" if a > 0 else "mp4a.40.2"
    trex = _first(data, b"trex")
    default = struct.unpack_from(">I", data, trex[0] + 12)[0] if trex else 0
    media_time = 0
    if elst := _first(data, b"elst"):  # the first edit that isn't empty (media_time -1 is a dwell)
        v, n = data[elst[0]], struct.unpack_from(">I", data, elst[0] + 4)[0]
        step = 20 if v == 1 else 12
        for k in range(n):
            t = struct.unpack_from(">q" if v == 1 else ">i", data, elst[0] + 8 + k * step + (8 if v == 1 else 4))[0]
            if t >= 0:
                media_time = t
                break
    return Init(timescale, codec, default, media_time)


@dataclass(frozen=True)
class Segment:
    start: int     # decode time of the first sample (tfdt), timescale units
    duration: int  # sum of sample durations
    samples: int


def segment_info(data: bytes, default_duration: int = 0) -> Segment:
    start, duration, samples, tfhd_default = None, 0, 0, default_duration
    for kind, s, _e in boxes(data):
        if kind == b"tfhd":
            flags = int.from_bytes(data[s + 1: s + 4], "big")
            o = s + 8 + (8 if flags & 0x1 else 0) + (4 if flags & 0x2 else 0)
            tfhd_default = struct.unpack_from(">I", data, o)[0] if flags & 0x8 else default_duration
        elif kind == b"tfdt" and start is None:
            start = struct.unpack_from(">Q" if data[s] == 1 else ">I", data, s + 4)[0]
        elif kind == b"trun":
            flags = int.from_bytes(data[s + 1: s + 4], "big")
            count = struct.unpack_from(">I", data, s + 4)[0]
            o = s + 8 + (4 if flags & 0x1 else 0) + (4 if flags & 0x4 else 0)
            per = 4 * bin(flags & 0xF00).count("1")
            if flags & 0x100:
                duration += sum(struct.unpack_from(">I", data, o + k * per)[0] for k in range(count))
            else:
                duration += count * tfhd_default
            samples += count
    if start is None:
        raise ValueError("not a media segment (no tfdt)")
    return Segment(start, duration, samples)


def samples(data: bytes) -> list[tuple[int, int, bytes]]:
    """Every sample of a fragmented MP4 as (decode time, duration, bytes), in order."""
    out, moof, base, default_dur, default_size, t = [], None, 0, 0, 0, 0
    for kind, s, e in boxes(data):
        if kind == b"moof":
            moof = s - 8
        elif kind == b"tfhd":
            flags = int.from_bytes(data[s + 1: s + 4], "big")
            o = s + 8
            base = struct.unpack_from(">Q", data, o)[0] if flags & 0x1 else moof
            o += (8 if flags & 0x1 else 0) + (4 if flags & 0x2 else 0)
            default_dur = struct.unpack_from(">I", data, o)[0] if flags & 0x8 else 0
            o += 4 if flags & 0x8 else 0
            default_size = struct.unpack_from(">I", data, o)[0] if flags & 0x10 else 0
        elif kind == b"tfdt":
            t = struct.unpack_from(">Q" if data[s] == 1 else ">I", data, s + 4)[0]
        elif kind == b"trun":
            flags = int.from_bytes(data[s + 1: s + 4], "big")
            count = struct.unpack_from(">I", data, s + 4)[0]
            o = s + 8
            pos = base + (struct.unpack_from(">i", data, o)[0] if flags & 0x1 else 0)
            o += (4 if flags & 0x1 else 0) + (4 if flags & 0x4 else 0)
            for _ in range(count):
                dur = size = None
                if flags & 0x100:
                    dur = struct.unpack_from(">I", data, o)[0]; o += 4
                if flags & 0x200:
                    size = struct.unpack_from(">I", data, o)[0]; o += 4
                o += (4 if flags & 0x400 else 0) + (4 if flags & 0x800 else 0)
                dur, size = dur if dur is not None else default_dur, size if size is not None else default_size
                out.append((t, dur, data[pos: pos + size]))
                t, pos = t + dur, pos + size
    return out


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def media_segment(seq: int, track_id: int, items: list[tuple[int, int, bytes]]) -> bytes:
    """One CMAF media segment (styp, moof, mdat) holding these samples (decode time, duration, bytes)."""
    entries = b"".join(struct.pack(">II", d, len(b)) for _t, d, b in items)

    def moof(offset: int) -> bytes:
        trun = _full(b"trun", 0, 0x000301, struct.pack(">Ii", len(items), offset) + entries)
        traf = _box(b"traf", _full(b"tfhd", 0, 0x020000, struct.pack(">I", track_id))
                    + _full(b"tfdt", 1, 0, struct.pack(">Q", items[0][0])) + trun)
        return _box(b"moof", _full(b"mfhd", 0, 0, struct.pack(">I", seq)) + traf)

    head = moof(len(moof(0)) + 8)
    return _box(b"styp", b"msdh\x00\x00\x00\x00msdhmsixcmfs") + head + _box(b"mdat", b"".join(b for *_x, b in items))


def track_id(init: bytes) -> int:
    s, _ = _first(init, b"tkhd")
    return struct.unpack_from(">I", init, s + (20 if init[s] == 1 else 12))[0]

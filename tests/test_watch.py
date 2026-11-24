import shutil

from leaktrace.store import Store
from leaktrace.watch import Message, Watcher


class FakeReport:
    def __init__(self, n):
        self.id, self.verdict, self.lecture, self.suspects = f"t{n}", "identified", {"title": "L"}, [{"student": "s1"}]


def _msg(i, kind, src=None):
    def download(folder):
        out = folder / f"m{i}.mp4"
        if src:
            shutil.copy(src, out)
        else:
            out.write_bytes(b"x")
        return out
    return Message(chat="@leaks", id=i, date="2026-11-20T10:00:00+00:00", kind=kind, size=1000,
                   link=f"https://t.me/leaks/{i}", download=download)


def test_new_video_message_is_traced_once(tmp_path):
    calls = []

    def tracer(path, store, first_seen=None):
        calls.append((path.name, first_seen))
        return FakeReport(len(calls))

    w = Watcher(Store(tmp_path / "db.sqlite"), tmp_path / "dl", tracer=tracer)
    assert w.handle(_msg(1, "video")).id == "t1"
    assert w.handle(_msg(1, "video")) is None  # the same post again (restart, edit) isn't traced twice
    assert calls == [("m1.mp4", "2026-11-20T10:00:00+00:00")]


def test_non_video_ignored(tmp_path):
    called = []
    w = Watcher(Store(tmp_path / "db.sqlite"), tmp_path / "dl", tracer=lambda *a, **k: called.append(1))
    assert w.handle(_msg(2, "text")) is None and w.handle(_msg(3, "photo")) is None
    assert not called


def test_oversized_file_skipped(tmp_path):
    called = []
    w = Watcher(Store(tmp_path / "db.sqlite"), tmp_path / "dl", tracer=lambda *a, **k: called.append(1), max_mb=0)
    assert w.handle(_msg(4, "video")) is None and not called

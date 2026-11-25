from leaktrace.store import Store


def test_first_seen_with_a_timezone_is_compared_in_utc(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.log_view("a", "lec", "play", at="2026-11-20T06:00:00+00:00")
    s.log_view("b", "lec", "play", at="2026-11-20T08:00:00+00:00")
    assert s.watched_by("lec", before="2026-11-20T12:00:00+05:30") == ["a"]  # 06:30 UTC
    assert s.watched_by("lec", before="2026-11-20T06:30:00Z") == ["a"]
    assert s.watched_by("lec", before="2026-11-20T09:00:00") == ["a", "b"]  # no zone given: UTC

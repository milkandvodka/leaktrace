from types import SimpleNamespace

from leaktrace.locate import Location
from leaktrace.trace import _notice

LOC = Location(title_id="ep3", offset=60.0, speed=1.0, votes=40, via="audio", duration=30.0)


def test_notice_names_the_rights_holder_from_the_environment(monkeypatch):
    monkeypatch.setenv("LEAKTRACE_ORG", "Harbour Pictures")
    text = _notice(SimpleNamespace(name="Episode 3"), LOC, "leak.mp4")
    assert "Harbour Pictures" in text and '"Episode 3"' in text and "60 s to 90 s" in text


def test_notice_without_an_org_is_still_complete(monkeypatch):
    monkeypatch.delenv("LEAKTRACE_ORG", raising=False)
    assert "our copyrighted title" in _notice(SimpleNamespace(name="Episode 3"), LOC, "leak.mp4")

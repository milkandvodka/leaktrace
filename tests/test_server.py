import time
import uuid

import pytest
from fastapi.testclient import TestClient

from leaktrace import media

ADMIN = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture(scope="module")
def client(packaged):
    from leaktrace import server
    with pytest.MonkeyPatch.context() as mp:  # other tests' monkeypatched data dirs must not leak in
        mp.setenv("LEAKTRACE_DATA", str(packaged[0]))
        server.store.cache_clear()
        server.jobs.cache_clear()
        with TestClient(server.app) as c:
            yield c


def _wait(client, job_id, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}", headers=ADMIN).json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_admin_requires_token(client):
    assert client.get("/api/titles").status_code == 401
    assert client.get("/api/titles", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/titles", headers=ADMIN).status_code == 200


def test_session_playlist_segments_and_view_log(client):
    assert client.post("/api/viewers", json={"viewers": [{"id": "s00042", "name": "Asha Rao"}]}, headers=ADMIN).status_code == 200
    r = client.post("/api/sessions", json={"title": "lec40", "viewer": "s00042"}, headers=ADMIN)
    assert r.status_code == 200
    play = r.json()["playlist"]
    assert play.startswith("/play/") and r.json()["download"].startswith("/download/")  # relative: works behind a proxy
    text = client.get(play).text
    seg_urls = [l for l in text.splitlines() if "/s/" in l]
    assert text.startswith("#EXTM3U") and len(seg_urls) == 21
    seg = client.get(seg_urls[0][seg_urls[0].index("/s/"):])
    assert seg.status_code == 200 and seg.content[:1] == b"\x47"  # MPEG-TS sync byte
    from leaktrace import server
    assert "s00042" in server.store().watched_by("lec40")


def test_segment_token_cannot_be_swapped(client):
    play = client.post("/api/sessions", json={"title": "lec40", "viewer": "s00042"}, headers=ADMIN).json()["playlist"]
    url = [l for l in client.get(play).text.splitlines() if "/s/" in l][2]
    path = url[url.index("/s/"):]
    assert client.get(path[:-3] + ("AAA" if not path.endswith("AAA") else "BBB")).status_code == 403
    assert client.get("/s/garbage").status_code == 403


def test_unknown_title_or_viewer_rejected(client):
    assert client.post("/api/sessions", json={"title": "nope", "viewer": "s00042"}, headers=ADMIN).status_code == 404
    assert client.post("/api/sessions", json={"title": "lec40", "viewer": "ghost"}, headers=ADMIN).status_code == 404


def test_trace_job_returns_report(client, tmp_path):
    other = tmp_path / "other.mp4"
    media.run(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6", "-f", "lavfi",
               "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", other])
    with open(other, "rb") as f:
        r = client.post("/api/traces", files={"file": ("leak.mp4", f, "video/mp4")}, headers=ADMIN)
    assert r.status_code == 200
    job = _wait(client, r.json()["job"])
    assert job["status"] == "done"
    report = client.get(f"/api/traces/{job['result']['trace']}", headers=ADMIN).json()
    assert report["report"]["verdict"] == "not_ours"


def test_title_upload_packages_it(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LEAKTRACE_PRESET", "veryfast")
    master = tmp_path / "m.mp4"
    media.run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=4.5", "-f", "lavfi",
               "-i", "sine=frequency=300:duration=4.5", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", master])
    lid = f"upl-{uuid.uuid4().hex[:8]}"  # the test data folder is a cache kept between runs
    with open(master, "rb") as f:
        r = client.post("/api/titles", data={"id": lid, "name": "Uploaded"},
                        files={"file": ("m.mp4", f, "video/mp4")}, headers=ADMIN)
    assert r.status_code == 200
    job = _wait(client, r.json()["job"])
    assert job["status"] == "done", job
    lec = next(l for l in client.get("/api/titles", headers=ADMIN).json() if l["id"] == lid)
    assert lec["status"] == "ready"
    bad = client.post("/api/titles", data={"id": "Bad Id!", "name": "x"}, files={"file": ("m.mp4", b"x", "video/mp4")},
                      headers=ADMIN)
    assert bad.status_code == 422
    again = client.post("/api/titles", data={"id": "lec40", "name": "x"}, files={"file": ("m.mp4", b"x", "video/mp4")},
                        headers=ADMIN)
    assert again.status_code == 409  # never silently replace a title viewers already have copies of

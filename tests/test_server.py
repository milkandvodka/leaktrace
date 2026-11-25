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


def test_session_manifests_segments_and_view_log(client, packaged):
    assert client.post("/api/viewers", json={"viewers": [{"id": "s00042", "name": "Asha Rao"}]}, headers=ADMIN).status_code == 200
    r = client.post("/api/sessions", json={"title": "lec40", "viewer": "s00042"}, headers=ADMIN)
    assert r.status_code == 200
    hls, dash = r.json()["hls"], r.json()["dash"]
    assert hls.startswith("/wm/") and r.json()["download"].startswith("/download/")  # relative: works behind a proxy
    master = client.get(hls)
    assert master.status_code == 200 and master.text.startswith("#EXTM3U") and master.headers["access-control-allow-origin"] == "*"
    base = hls.rsplit("/", 1)[0]
    media_pl = client.get(f"{base}/v720.m3u8").text
    seg_urls = [l for l in media_pl.splitlines() if l.endswith(".m4s")]
    assert len(seg_urls) == packaged[1].n_segments == 20
    seg = client.get(f"{base}/{seg_urls[0]}")
    assert seg.status_code == 200 and b"moof" in seg.content[:256] and "immutable" in seg.headers["cache-control"]
    assert client.get(f"{base}/v720/init.mp4").content[4:8] == b"ftyp"
    assert client.get(dash).text.lstrip().startswith("<?xml")
    from leaktrace import server
    assert "s00042" in server.store().watched_by("lec40")


def test_altered_or_foreign_tokens_get_403(client):
    hls = client.post("/api/sessions", json={"title": "lec40", "viewer": "s00042"}, headers=ADMIN).json()["hls"]
    token = hls.split("/")[2]
    exp, bits, sig = token.split(".")
    flipped = bits[:-1] + ("A" if bits[-1] != "A" else "B")
    assert client.get(f"/wm/{exp}.{flipped}.{sig}/lec40/v720/00002.m4s").status_code == 403
    assert client.get("/wm/garbage/lec40/master.m3u8").status_code == 403
    assert client.get(f"/wm/{token}/lec40/meta.json").status_code == 404  # tracing files are never served


def test_per_viewer_manifests_and_media_objects(client, packaged):
    r = client.post("/api/sessions", json={"title": "lec40", "viewer": "s00042"}, headers=ADMIN).json()
    direct = r["direct"]["hls"]
    assert direct.startswith("/pl/") and client.get(direct).status_code == 200
    media_pl = client.get(direct.rsplit("/", 1)[0] + "/v720.m3u8")
    assert media_pl.headers["cache-control"] == "private, no-store"
    urls = [l for l in media_pl.text.splitlines() if l.endswith(".m4s")]
    assert len(urls) == packaged[1].n_segments and all(u.startswith("/media/lec40/") for u in urls)
    assert r["token"] not in media_pl.text  # segment URLs carry no token: plain cacheable objects
    seg = client.get(urls[3])
    assert seg.status_code == 200 and "immutable" in seg.headers["cache-control"]
    assert client.get(f"/pl/{r['token']}/lec40/manifest.mpd").text.count("<SegmentURL") == 4 * packaged[1].n_segments
    for bad in ("meta.json", "mezzanine.mp4", "master.m3u8", "tracks.json", "fp.npz"):
        assert client.get(f"/media/lec40/{bad}").status_code == 404, bad


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


def test_demo_mode_opens_the_api_but_not_new_titles(client, monkeypatch):
    monkeypatch.setenv("LEAKTRACE_DEMO", "1")
    assert client.get("/api/health").json()["demo"] is True
    assert client.get("/api/titles").status_code == 200  # no token needed
    r = client.post("/api/titles", data={"id": "x1", "name": "x"}, files={"file": ("m.mp4", b"x", "video/mp4")})
    assert r.status_code == 403  # packaging is the expensive part: visitors can't start it
    monkeypatch.delenv("LEAKTRACE_DEMO")
    assert client.get("/api/health").json()["demo"] is False
    assert client.get("/api/titles").status_code == 401


def test_demo_seeds_its_own_title_and_viewers_once(client, monkeypatch, title_40s):
    from leaktrace import server
    monkeypatch.setenv("LEAKTRACE_DEMO", "1")
    monkeypatch.setenv("LEAKTRACE_DEMO_SOURCE", str(title_40s))
    monkeypatch.setenv("LEAKTRACE_PRESET", "veryfast")
    job = server.seed_demo()
    if job:  # the test data folder is a cache: a previous run may have packaged it already
        assert _wait(client, job, timeout=600)["status"] == "done"
    assert next(t for t in client.get("/api/titles").json() if t["id"] == server.DEMO_TITLE)["status"] == "ready"
    assert len([v for v in client.get("/api/viewers").json() if v["id"].startswith("demo")]) >= 5
    assert server.seed_demo() is None  # already there: nothing to do

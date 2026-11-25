"""HTTP service.

Admin API (Authorization: Bearer LEAKTRACE_ADMIN_TOKEN):
  POST /api/titles (multipart: id, name, file)    package a title (background job)
  GET  /api/titles
  POST /api/viewers {"viewers": [{"id", "name"}]}   GET /api/viewers
  POST /api/sessions {"title", "viewer"}           a viewer's token: HLS, DASH and download URLs (logged as a view)
  POST /api/traces   (multipart: file, first_seen?)   trace a leak (background job)
  GET  /api/traces   GET /api/traces/{id}   GET /api/jobs/{id}   GET /api/bench/latest

Viewer-facing (what a platform's player and download button point at):
  GET /wm/{token}/{title}/master.m3u8 | manifest.mpd   shared manifests; segments under them are mapped to the
                           viewer's variant from the token (what a CDN edge function does in production, edge/)
  GET /pl/{token}/{title}/master.m3u8 | manifest.mpd   the same manifests listing the viewer's objects directly,
                           under LEAKTRACE_MEDIA_URL (for CDNs without edge code)
  GET /media/{title}/{object}   the objects themselves (unguessable names; a bucket or CDN serves these in production)
  GET /download/{token}    the viewer's own MP4 (logged as a download)

Run: uvicorn leaktrace.server:app --port 8003
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, StringConstraints

from . import deliver, edge
from .config import ROOT, settings
from .ingest import ingest, title_dir
from .jobs import Jobs
from .store import Store

ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VIEWER = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
MAX_UPLOAD = int(os.getenv("LEAKTRACE_MAX_UPLOAD_MB", "4096")) * 1024 * 1024
DIST = ROOT / "frontend" / "dist"


@lru_cache(maxsize=1)
def store() -> Store:
    return Store(settings().data / "leaktrace.db")


@lru_cache(maxsize=1)
def jobs() -> Jobs:
    return Jobs(store())


@asynccontextmanager
async def lifespan(_app):
    jobs()  # marks jobs left unfinished by a previous process as failed
    yield
    jobs().shutdown()


app = FastAPI(title="LeakTrace", lifespan=lifespan)


def admin(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = settings().admin_token
    if not expected:
        raise HTTPException(503, "Set LEAKTRACE_ADMIN_TOKEN to use the admin API.")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "Wrong or missing admin token.")


Admin = Depends(admin)


def _sid(s: str) -> str:
    if not VIEWER.match(s):
        raise ValueError("viewer ids: 1-64 letters, digits, _ . @ -")
    return s


class ViewerIn(BaseModel):
    id: Annotated[str, AfterValidator(_sid)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ViewersIn(BaseModel):
    viewers: list[ViewerIn]


def _lid(s: str) -> str:
    if not ID.match(s):
        raise ValueError("title ids: lowercase letters, digits and dashes, up to 64")
    return s


class SessionIn(BaseModel):
    title: Annotated[str, AfterValidator(_lid)]  # validated: it becomes part of a file path
    viewer: Annotated[str, AfterValidator(_sid)]


# signed tokens for download links
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _session_token(title: str, viewer: str, ttl: int = 6 * 3600) -> str:
    payload = json.dumps([title, viewer, int(time.time()) + ttl], separators=(",", ":")).encode()
    sig = hmac.new(settings().secret, b"play\x1f" + payload, hashlib.sha256).digest()[:16]
    return _b64(payload) + "." + _b64(sig)


def _open_session(token: str) -> tuple[str, str]:
    try:
        head, sig = token.split(".")
        payload = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4))
        good = hmac.new(settings().secret, b"play\x1f" + payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), good):
            raise ValueError
        title, viewer, exp = json.loads(payload)
    except Exception:
        raise HTTPException(403, "Invalid link.")
    if exp < time.time():
        raise HTTPException(403, "This link has expired.")
    return title, viewer


def _save_upload(file: UploadFile, folder: str) -> Path:
    dest = settings().data / "uploads" / folder
    dest.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload").suffix[:10] or ".bin"
    path = dest / f"{uuid.uuid4().hex}{suffix}"
    size = 0
    with path.open("wb") as out:
        while chunk := file.file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, "File too large.")
            out.write(chunk)
    return path


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/titles", dependencies=[Admin])
def titles() -> list[dict]:
    return store().titles()


@app.post("/api/titles", dependencies=[Admin])
def add_title(id: Annotated[str, Form()], name: Annotated[str, Form()], file: Annotated[UploadFile, File()]) -> dict:
    if not ID.match(id):
        raise HTTPException(422, "Title ids: lowercase letters, digits and dashes, up to 64.")
    if not name.strip():
        raise HTTPException(422, "A name is required.")
    existing = store().title(id)
    if (existing and existing["status"] != "failed") or (title_dir(id) / "meta.json").exists():
        # Viewers already hold copies of it; replacing it would make their leaks untraceable.
        raise HTTPException(409, "A title with this id already exists. Pick another id.")
    path = _save_upload(file, "masters")
    store().add_title(id, name.strip(), "processing")
    preset = os.getenv("LEAKTRACE_PRESET", "medium")

    def run(progress):
        try:
            meta = ingest(path, id, name.strip(), progress=progress, preset=preset)
        except Exception:
            store().set_title(id, "failed")
            raise
        store().set_title(id, "ready", meta.summary())
        return {"title": id}

    return {"job": jobs().submit("ingest", run, {"title": id})}


@app.get("/api/viewers", dependencies=[Admin])
def viewers() -> list[dict]:
    return store().viewers()


@app.post("/api/viewers", dependencies=[Admin])
def add_viewers(body: ViewersIn) -> dict:
    for s in body.viewers:
        store().add_viewer(s.id, s.name)
    return {"added": len(body.viewers)}


@app.post("/api/sessions", dependencies=[Admin])
def session(body: SessionIn) -> dict:
    if not (title_dir(body.title) / "cmaf" / "tracks.json").exists():  # packaged files are the truth (API or CLI)
        raise HTTPException(404, "No such title (or it isn't packaged yet).")
    if body.viewer not in set(store().viewer_ids()):
        raise HTTPException(404, "No such viewer.")
    store().log_view(body.viewer, body.title, "session")
    # Relative links: right behind any proxy or CDN, whatever host and scheme the viewer reached us on.
    s = deliver.session(body.title, body.viewer)
    return {**s, "direct": {"hls": f"/pl/{s['token']}/{body.title}/master.m3u8",
                            "dash": f"/pl/{s['token']}/{body.title}/manifest.mpd"},
            "download": f"/download/{_session_token(body.title, body.viewer)}"}


_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".mpd": "application/dash+xml", ".mp4": "video/mp4",
          ".m4s": "video/iso.segment"}


@app.get("/wm/{path:path}")
def watermarked(path: str) -> FileResponse:
    """Shared manifests and objects for a viewer's token; the same mapping a CDN edge function makes."""
    try:
        file = deliver.resolve(path)
    except edge.Denied as e:
        raise HTTPException(e.status, "This link has expired or isn't valid." if e.status == 403 else "Not found.")
    if not file.exists():
        raise HTTPException(404, "Not found.")
    manifest = file.suffix in (".m3u8", ".mpd")
    return FileResponse(file, media_type=_TYPES.get(file.suffix, "application/octet-stream"),
                        headers={"Cache-Control": "public, max-age=60" if manifest else "public, max-age=31536000, immutable",
                                 "Access-Control-Allow-Origin": "*"})


@app.get("/pl/{path:path}")
def per_viewer(path: str) -> Response:
    """The viewer's own manifests: the shared ones with each segment URL pointing at that viewer's object."""
    try:
        text, mime = deliver.viewer_manifest(path)
    except edge.Denied as e:
        raise HTTPException(e.status, "This link has expired or isn't valid." if e.status == 403 else "Not found.")
    return Response(text, media_type=mime, headers={"Cache-Control": "private, no-store", "Access-Control-Allow-Origin": "*"})


OBJECT = re.compile(r"^([0-9a-f]{32}\.m4s|[a-z0-9]+-init\.mp4)$")


@app.get("/media/{title}/{name}")
def media_object(title: str, name: str) -> FileResponse:
    """A packaged object: segments and init segments only, never manifests or tracing files."""
    file = title_dir(title) / "cmaf" / name if ID.match(title) and OBJECT.match(name) else None
    if file is None or not file.exists():
        raise HTTPException(404, "Not found.")
    return FileResponse(file, media_type=_TYPES[file.suffix], headers={"Cache-Control": "public, max-age=31536000, immutable",
                                                                         "Access-Control-Allow-Origin": "*"})


@app.get("/download/{token}")
def download(token: str) -> FileResponse:
    title, viewer = _open_session(token)
    store().log_view(viewer, title, "download")
    path = deliver.export(title, viewer)
    return FileResponse(path, media_type="video/mp4", filename=f"{title}.mp4")


@app.post("/api/traces", dependencies=[Admin])
def add_trace(file: Annotated[UploadFile, File()], first_seen: Annotated[str | None, Form()] = None) -> dict:
    from .trace import trace
    path = _save_upload(file, "leaks")
    name = Path(file.filename or "leak").name

    def run(progress):
        renamed = path.with_name(f"{path.stem}-{re.sub(r'[^A-Za-z0-9._-]', '_', name)[:80]}")
        path.rename(renamed)
        report = trace(renamed, store(), first_seen=first_seen, progress=progress)
        return {"trace": report.id, "verdict": report.verdict}

    return {"job": jobs().submit("trace", run, {"file": name})}


@app.get("/api/traces", dependencies=[Admin])
def traces() -> list[dict]:
    return store().traces()


@app.get("/api/traces/{tid}", dependencies=[Admin])
def get_trace(tid: str) -> dict:
    t = store().trace(tid)
    if not t:
        raise HTTPException(404, "No such trace.")
    return t


@app.get("/api/jobs/{jid}", dependencies=[Admin])
def get_job(jid: str) -> dict:
    j = store().job(jid)
    if not j:
        raise HTTPException(404, "No such job.")
    return j


@app.get("/api/bench/latest", dependencies=[Admin])
def bench_latest() -> dict:
    p = settings().data / "bench" / "latest.json"
    if not p.exists():
        raise HTTPException(404, "No bench results yet. Run: python -m leaktrace bench <title>")
    return json.loads(p.read_text())


if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")

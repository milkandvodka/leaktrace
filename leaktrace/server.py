"""HTTP service.

Admin API (Authorization: Bearer LEAKTRACE_ADMIN_TOKEN):
  POST /api/lectures (multipart: id, title, file)    package a lecture (background job)
  GET  /api/lectures
  POST /api/students {"students": [{"id", "name"}]}   GET /api/students
  POST /api/sessions {"lecture", "student"}           a student's signed playlist and download URLs
  POST /api/traces   (multipart: file, first_seen?)   trace a leak (background job)
  GET  /api/traces   GET /api/traces/{id}   GET /api/jobs/{id}   GET /api/bench/latest

Student-facing (what a platform's player and download button would point at):
  GET /play/{token}.m3u8   the student's playlist (logged as a view)
  GET /s/{token}           one segment (the variant is decided here, never visible in the URL)
  GET /download/{token}    the student's own MP4 (logged as a download)

Run: uvicorn leaktrace.server:app --port 8003
"""
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, StringConstraints

from . import deliver
from .config import ROOT, settings
from .ingest import ingest, lecture_dir
from .jobs import Jobs
from .store import Store

ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STUDENT = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
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
    if not STUDENT.match(s):
        raise ValueError("student ids: 1-64 letters, digits, _ . @ -")
    return s


class StudentIn(BaseModel):
    id: Annotated[str, AfterValidator(_sid)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class StudentsIn(BaseModel):
    students: list[StudentIn]


def _lid(s: str) -> str:
    if not ID.match(s):
        raise ValueError("lecture ids: lowercase letters, digits and dashes, up to 64")
    return s


class SessionIn(BaseModel):
    lecture: Annotated[str, AfterValidator(_lid)]  # validated: it becomes part of a file path
    student: Annotated[str, AfterValidator(_sid)]


# signed session tokens for the playlist and download links
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _session_token(lecture: str, student: str, ttl: int = 6 * 3600) -> str:
    payload = json.dumps([lecture, student, int(time.time()) + ttl], separators=(",", ":")).encode()
    sig = hmac.new(settings().secret, b"play\x1f" + payload, hashlib.sha256).digest()[:16]
    return _b64(payload) + "." + _b64(sig)


def _open_session(token: str) -> tuple[str, str]:
    try:
        head, sig = token.split(".")
        payload = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4))
        good = hmac.new(settings().secret, b"play\x1f" + payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), good):
            raise ValueError
        lecture, student, exp = json.loads(payload)
    except Exception:
        raise HTTPException(403, "Invalid link.")
    if exp < time.time():
        raise HTTPException(403, "This link has expired.")
    return lecture, student


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


@app.get("/api/lectures", dependencies=[Admin])
def lectures() -> list[dict]:
    return store().lectures()


@app.post("/api/lectures", dependencies=[Admin])
def add_lecture(id: Annotated[str, Form()], title: Annotated[str, Form()], file: Annotated[UploadFile, File()]) -> dict:
    if not ID.match(id):
        raise HTTPException(422, "Lecture ids: lowercase letters, digits and dashes, up to 64.")
    if not title.strip():
        raise HTTPException(422, "A title is required.")
    path = _save_upload(file, "masters")
    store().add_lecture(id, title.strip(), "processing")
    preset = os.getenv("LEAKTRACE_PRESET", "medium")

    def run(progress):
        try:
            meta = ingest(path, id, title.strip(), progress=progress, preset=preset)
        except Exception:
            store().set_lecture(id, "failed")
            raise
        store().set_lecture(id, "ready", {"duration": meta.duration, "segments": meta.n_segments,
                                          "marked": meta.marked_segments, "has_audio": meta.has_audio})
        return {"lecture": id}

    return {"job": jobs().submit("ingest", run, {"lecture": id})}


@app.get("/api/students", dependencies=[Admin])
def students() -> list[dict]:
    return store().students()


@app.post("/api/students", dependencies=[Admin])
def add_students(body: StudentsIn) -> dict:
    for s in body.students:
        store().add_student(s.id, s.name)
    return {"added": len(body.students)}


@app.post("/api/sessions", dependencies=[Admin])
def session(body: SessionIn, request: Request) -> dict:
    if not (lecture_dir(body.lecture) / "meta.json").exists():  # packaged files are the truth (API or CLI)
        raise HTTPException(404, "No such lecture (or it isn't packaged yet).")
    if body.student not in set(store().student_ids()):
        raise HTTPException(404, "No such student.")
    token = _session_token(body.lecture, body.student)
    base = str(request.base_url).rstrip("/")
    return {"playlist": f"{base}/play/{token}.m3u8", "download": f"{base}/download/{token}"}


@app.get("/play/{token}.m3u8")
def play(token: str, request: Request) -> PlainTextResponse:
    lecture, student = _open_session(token)
    store().log_view(student, lecture, "play")
    text = deliver.playlist(lecture, student, str(request.base_url).rstrip("/"))
    return PlainTextResponse(text, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})


@app.get("/s/{token}")
def segment(token: str) -> FileResponse:
    try:
        path = deliver.resolve(token)
    except deliver.TokenError:
        raise HTTPException(403, "Invalid segment link.")
    except FileNotFoundError:
        raise HTTPException(404, "No such lecture.")
    if not path.exists():
        raise HTTPException(404, "Segment missing.")
    return FileResponse(path, media_type="video/mp2t", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/download/{token}")
def download(token: str) -> FileResponse:
    lecture, student = _open_session(token)
    store().log_view(student, lecture, "download")
    path = deliver.export(lecture, student)
    return FileResponse(path, media_type="video/mp4", filename=f"{lecture}.mp4")


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
        raise HTTPException(404, "No bench results yet. Run: python -m leaktrace bench <lecture>")
    return json.loads(p.read_text())


if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")

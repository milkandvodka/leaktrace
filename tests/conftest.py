import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "var" / "test-cache"  # generated media reused across test runs (gitignored)

# Tests never touch real secrets or data.
os.environ.setdefault("LEAKTRACE_SECRET", "00" * 32)
os.environ.setdefault("LEAKTRACE_ADMIN_TOKEN", "test-admin-token")


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.fixture(scope="session")
def title_40s() -> Path:
    from leaktrace.synth import make_title
    out = CACHE / "title_40s.mp4"
    if not out.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        make_title(out, seconds=40, seed=1, tts=False)
    return out


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("LEAKTRACE_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def _code_hash() -> str:
    """Changes whenever the marking code changes, so a cached packaged title is never stale."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted((ROOT / "leaktrace").rglob("*.py")):
        if p.name not in ("server.py", "jobs.py", "watch.py", "cli.py"):
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


@pytest.fixture(scope="session")
def packaged(title_40s):
    """title_40s packaged as 'lec40' in a cached data folder: (data dir, TitleMeta)."""
    from leaktrace.ingest import TitleMeta, ingest
    data = CACHE / f"data-{_code_hash()}"
    folder = data / "titles" / "lec40"
    os.environ["LEAKTRACE_DATA"] = str(data)
    if not (folder / "meta.json").exists():
        ingest(title_40s, "lec40", "Test title", preset="veryfast")
    return data, TitleMeta.load(folder)


@pytest.fixture(scope="session")
def rendition_a(packaged) -> Path:
    """Rendition A of lec40 as one MP4 (what a viewer with an all-A codeword would download)."""
    from leaktrace import media
    data, _ = packaged
    out = data / "allA.mp4"
    if not out.exists():
        media.run(["-i", data / "titles" / "lec40" / "A" / "index.m3u8", "-c", "copy", out])
    return out


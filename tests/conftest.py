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
def lecture_40s() -> Path:
    from leaktrace.synth import make_lecture
    out = CACHE / "lecture_40s.mp4"
    if not out.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        make_lecture(out, seconds=40, seed=1, tts=False)
    return out


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("LEAKTRACE_DATA", str(tmp_path / "data"))
    return tmp_path / "data"

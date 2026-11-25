import subprocess
import sys

from conftest import ROOT

WORKER_OPENCV = """
import leaktrace
import numpy as np
from concurrent.futures import ThreadPoolExecutor

def work():
    import cv2
    img = (np.random.default_rng(0).random((360, 640)) * 255).astype(np.uint8)
    cv2.ORB_create(500).detectAndCompute(img, None)
    return cv2.resize(img, (320, 180), interpolation=cv2.INTER_AREA).shape

with ThreadPoolExecutor(1) as ex:
    print(ex.submit(work).result())
"""


def test_process_exits_after_opencv_first_runs_on_a_worker_thread():
    # Jobs (traces) run on worker threads; OpenCV first used there made the process hang at exit on Windows,
    # so the server could never stop cleanly.
    r = subprocess.run([sys.executable, "-c", WORKER_OPENCV], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "(180, 320)" in r.stdout, r.stderr

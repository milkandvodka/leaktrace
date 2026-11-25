"""LeakTrace: per-viewer invisible forensic marks for streaming video, and tracing of leaked copies."""
import cv2 as _cv2
import numpy as _np

__version__ = "0.2.0"

# OpenCV first used on a worker thread (traces and packaging run as background jobs) leaves the process unable to
# exit on Windows. Using it once here, on the thread that imports the package (the main thread for the server,
# the CLI and tests), avoids that.
_cv2.resize(_np.zeros((8, 8), _np.uint8), (4, 4))

import json

from test_ingest import keyframes


def test_export_masters_writes_both_masters_and_the_grid(packaged, tmp_path):
    from leaktrace.cli import main
    _, meta = packaged
    out = tmp_path / "masters"
    assert main(["export-masters", "lec40", "--out", str(out)]) == 0
    g = json.loads((out / "grid.json").read_text())
    assert (g["fps"], g["seg_frames"], g["segment_seconds"], g["segments"], g["frames"]) == (
        "25", 50, 2.0, meta.n_segments, meta.n_frames)
    for v in "AB":  # a platform's encoder can cut exactly where LeakTrace's marks change
        assert keyframes(out / f"{v}.mp4") == (list(range(0, meta.n_frames, 50)), meta.n_frames)
    assert (out / "A.mp4").read_bytes() != (out / "B.mp4").read_bytes()

from fractions import Fraction

from leaktrace import media
from leaktrace.grid import V1, Grid, snap_fps

NTSC = Fraction(30000, 1001)


def test_each_frame_rate_gets_whole_frame_segments_of_about_two_seconds():
    assert Grid.for_fps(NTSC, 2.0) == Grid(NTSC, 60)
    assert Grid.for_fps(Fraction(24), 2.0).seg_frames == 48
    assert Grid.for_fps(Fraction(25), 2.0).seg_frames == 50
    assert Grid.for_fps(Fraction(60), 2.0).seg_frames == 120
    assert Grid.for_fps(NTSC, 2.0).samples(0) == 96096  # 2.002 s at 48 kHz


def test_a_2997_grid_has_no_drift_after_an_hour():
    g = Grid(NTSC, 60)
    n = 1798  # just over an hour of 2.002 s segments
    assert g.time(n) == Fraction(n * 60 * 1001, 30000)  # exact, not n * a rounded float
    assert g.sample(n) == n * 96096
    assert sum(g.samples(k) for k in range(n)) == g.sample(n)


def test_odd_rates_round_each_boundary_on_the_exact_timeline():
    g = Grid(Fraction(3001, 100), 60)  # 30.01 fps: a segment is 95968.01 samples, never a whole number
    assert {g.samples(k) for k in range(5000)} == {95968, 95969}
    assert g.sample(4000) == round(Fraction(4000 * 60 * 100 * 48000, 3001))  # no accumulated rounding


def test_source_rates_snap_to_broadcast_rates():
    assert snap_fps(Fraction(2997, 100)) == NTSC
    assert snap_fps(Fraction(1803000, 60161)) == NTSC  # a phone's variable-rate average
    assert snap_fps(Fraction(120)) == Fraction(60)
    assert snap_fps(Fraction(25)) == Fraction(25)
    assert snap_fps(Fraction(12.5)) == Fraction(25, 2)


def test_grid_round_trips_through_json_and_old_titles_keep_theirs():
    g = Grid(NTSC, 60)
    assert Grid.from_json(g.to_json()) == g and g.to_json() == {"fps": "30000/1001", "seg_frames": 60}
    assert V1 == Grid(Fraction(25), 48) and V1.seconds == 1.92


def test_probe_reads_the_exact_frame_rate(tmp_path):
    out = tmp_path / "ntsc.mp4"
    media.run(["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30000/1001:duration=1", "-c:v", "libx264",
               "-preset", "ultrafast", out])
    assert media.probe(out).rate == NTSC

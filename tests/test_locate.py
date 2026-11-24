from leaktrace import media
from leaktrace.config import SEG_SECONDS
from leaktrace.locate import locate


def _lectures(packaged):
    data, _ = packaged
    return [data / "lectures" / "lec40"]


def test_locates_clip_by_audio(packaged, rendition_a, tmp_path):
    clip = tmp_path / "clip.mp4"
    media.run(["-ss", 7.3, "-i", rendition_a, "-t", 30, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", clip])
    loc = locate(clip, _lectures(packaged))
    assert loc is not None and loc.lecture_id == "lec40" and loc.via == "audio"
    # rendition A's content is shifted by up to 30 ms by design (timing carrier) and AAC adds ~21 ms of delay;
    # the per-segment alignment downstream searches +-60 ms
    assert abs(loc.offset - 7.3) < 0.06 and abs(loc.speed - 1.0) < 0.01
    first = int((7.3 + SEG_SECONDS - 1e-9) // SEG_SECONDS)
    assert loc.segments[0] == first and loc.segments[-1] <= int((7.3 + 30) // SEG_SECONDS) - 1


def test_locates_speeded_clip(packaged, rendition_a, tmp_path):
    clip = tmp_path / "fast.mp4"
    media.run(["-ss", 5, "-i", rendition_a, "-t", 25, "-filter:v", "setpts=PTS/1.25", "-filter:a", "atempo=1.25",
               "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", clip])
    loc = locate(clip, _lectures(packaged))
    assert loc is not None and abs(loc.speed - 1.25) <= 0.02 and abs(loc.offset - 5) < 0.1


def test_locates_muted_clip_by_video(packaged, rendition_a, tmp_path):
    clip = tmp_path / "muted.mp4"
    media.run(["-ss", 12, "-i", rendition_a, "-t", 20, "-an", "-c:v", "libx264", "-preset", "veryfast", clip])
    loc = locate(clip, _lectures(packaged))
    assert loc is not None and loc.via == "video" and abs(loc.offset - 12) < 0.25


def test_rotated_portrait_phone_clip_located(packaged, rendition_a, tmp_path):
    flat, clip = tmp_path / "flat.mp4", tmp_path / "phone.mp4"
    media.run(["-ss", 10, "-i", rendition_a, "-t", 15, "-vf", "transpose=1,scale=720:1280",
               "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", 44100, flat])
    media.run(["-display_rotation:v:0", "90", "-i", flat, "-c", "copy", clip])
    loc = locate(clip, _lectures(packaged))
    assert loc is not None and abs(loc.offset - 10) < 0.05


def test_foreign_video_returns_none(packaged, tmp_path):
    other = tmp_path / "other.mp4"
    media.run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=15", "-f", "lavfi",
               "-i", "sine=frequency=440:duration=15", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", other])
    assert locate(other, _lectures(packaged)) is None

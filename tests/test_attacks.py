from leaktrace import media
from leaktrace.bench.attacks import ATTACKS, collude_average, collude_interleave


def _short(rendition_a, tmp_path):
    clip = tmp_path / "src.mp4"
    media.run(["-t", 6, "-i", rendition_a, "-c", "copy", clip])
    return clip


def test_nokia_is_qvga_amr(rendition_a, tmp_path):
    out = ATTACKS["nokia"](_short(rendition_a, tmp_path), tmp_path)
    p = media.probe(out)
    assert (p.width, p.height) == (320, 240) and p.has_audio and abs(p.duration - 6) < 0.5
    import subprocess
    codecs = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                            capture_output=True, text=True).stdout.split()
    assert "amr_nb" in codecs and "mpeg4" in codecs


def test_telegram5_is_480p(rendition_a, tmp_path):
    p = media.probe(ATTACKS["telegram5"](_short(rendition_a, tmp_path), tmp_path))
    assert p.height == 480 and p.has_audio


def test_every_attack_produces_a_playable_file(rendition_a, tmp_path):
    src = _short(rendition_a, tmp_path)
    for name, fn in ATTACKS.items():
        p = media.probe(fn(src, tmp_path))
        assert p.duration > 3, name
        assert p.has_video or name == "audio_only", name
        assert p.has_audio or name == "muted", name


def test_collusion_attacks(packaged, tmp_path):
    from leaktrace import deliver
    a, b = deliver.export("lec40", "s00001"), deliver.export("lec40", "s00002")
    assert media.probe(collude_interleave("lec40", ["s00001", "s00002"], tmp_path, seed=1)).duration > 30
    assert media.probe(collude_average([a, b], tmp_path)).duration > 30

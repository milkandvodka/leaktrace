def test_a_trace_that_fails_is_recorded_and_the_bench_goes_on(packaged, monkeypatch):
    from leaktrace.bench import run as bench_run
    from leaktrace.media import MediaError
    calls = []

    def broken(src, store, candidates=None):
        calls.append(src)
        raise MediaError("ffmpeg exited with 1")

    monkeypatch.setattr(bench_run, "trace", broken)
    monkeypatch.setattr(bench_run, "_psnr", lambda title_id: 0.0)
    res = bench_run.bench("lec40", viewers=1, clips=(None,), attacks=["muted", "audio_only"], pool_size=3,
                          collusion=False, log=lambda *_: None)
    assert [r["verdict"] for r in res["rows"]] == ["error", "error"] and len(calls) == 2
    assert "ffmpeg exited" in res["rows"][0]["error"] and not res["rows"][0]["false_accusation"]

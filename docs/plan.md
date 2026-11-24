# LeakTrace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every student a lecture copy that is invisibly unique in nine ways, and trace any leaked copy back to the account, with a bounded false-accusation rate.

**Architecture:**
- **Packaging:** two renditions (A and B) per lecture, cut into 1.92 s segments. Each segment carries one bit, written by picture, audio and timing carriers at the same time. Each student gets an HMAC-derived Tardos codeword that picks A or B per segment, delivered as a signed HLS playlist or MP4.
- **Tracing:** locate the leak (audio landmarks, video keyframes), align it to the unmarked mezzanine, measure every carrier against decoy noise, fuse the results into soft bits, and score candidate accounts against a simulated innocent null.

**Tech Stack:** Python 3.13, numpy, scipy, opencv-python-headless, Pillow, FastAPI and uvicorn, SQLite (stdlib), ffmpeg 6+, Telethon (watcher), React 19, Vite, Tailwind v4 and hls.js (UI), pytest.

**Spec:** `docs/design.md`

## Global Constraints

- Classical signal processing only: no ML models. Permissive licences only: no GPL or AGPL dependencies.
- Constants in `leaktrace/config.py`: FPS = 25, SR = 48000, SEG_FRAMES = 48, SEG_SAMPLES = 92160 (1.92 s), WIDTH = 1280, HEIGHT = 720, ANALYSIS = 320×180, FP_SR = 8000.
- Initial carrier budgets:
  - picture: brightness 1.5 code values, tint 1.2 code values, saturation 1.5 %, hue 0.6°, contrast 0.6 %;
  - audio: patchwork 0.7 dB, loudness 0.25 dB, tone balance 0.4 dB;
  - timing: warp 30 ms, maximum speech speed change 5 %;
  - code: Tardos cutoff t = 0.1.
- Every modulation is antipodal (A = +, B = −) and zero at segment boundaries. Temporal codes are symmetric and zero-mean: `s(x) = sin 3x − (sin x)/3`, with x = πt/T, normalised to a maximum |s| of 1.
- Verdicts: **identified** when N·p < 1e-6; **narrowed** when N·p < 1e-2; otherwise **inconclusive**. Never print a name below "identified" without the word "narrowed".
- Secrets come from the environment only: `LEAKTRACE_SECRET` (required), `LEAKTRACE_ADMIN_TOKEN` and `LEAKTRACE_DATA`. `.env*` is gitignored except `.env.example`.
- No company names anywhere in the repo.
- Never load a whole lecture's frames into memory. Stream segment by segment.

## Review Focus

1. **Phone clips:** a portrait clip with rotation metadata or a variable frame rate should be normalised by ffmpeg (autorotate, constant frame rate) and still be located. This is tested in Task 8.
2. **Foreign videos:** a leak that isn't from the library should return "not one of our lectures", with no accusation. Tested in Task 9.
3. **Silent masters:** a master with no audio track should still ingest, with audio and timing carriers off, and still trace by picture. Tested in Task 7.
4. **Partial segments:** a lecture whose length isn't a multiple of 1.92 s gets an unmarked final partial segment, and its playlist is still valid. Tested in Task 7.
5. **Innocent students:** an innocent student's export, when traced, must never put another student at "identified". Tested in Task 9.

---

### Task 1: Scaffold, config and synthetic lecture generator

**Files:**
- Create:
  - `leaktrace/__init__.py`
  - `leaktrace/__main__.py`
  - `leaktrace/config.py`
  - `leaktrace/cli.py`
  - `leaktrace/synth.py`
  - `requirements.txt`, `requirements-dev.txt`
  - `.gitignore`, `.env.example`
  - `pytest.ini`
  - `tests/conftest.py`
- Test: `tests/test_synth.py`

**Interfaces:**
- Produces:
  - `config.settings() -> Settings`, which has `secret: bytes`, `data: Path`, `admin_token: str`, `ffmpeg: str`, `ffprobe: str`, plus the constants above.
  - `synth.make_lecture(out: Path, seconds: float, seed: int, tts: bool = True) -> Path`: rendered slides, speech (Windows SAPI when `tts` is set and available, otherwise syllable-rhythm noise), and a moving pointer and "speaker" box.
  - The conftest fixture `lecture_40s` is a cached 40 s synthetic lecture, built with `tts=False` so it's deterministic.

- [ ] Step 1: Write `test_make_lecture_has_expected_streams`: ffprobe reports h264 1280×720 at 25 fps with 48 kHz audio, and duration within 0.1 s of 40. Also write `test_make_lecture_is_deterministic_without_tts`: two runs with the same seed give identical frame 0 and identical audio RMS.
- [ ] Step 2: Run `pytest tests/test_synth.py -v`. Expected: FAIL (no module).
- [ ] Step 3: Implement. Slides are drawn with PIL, frames piped to ffmpeg; audio is a WAV muxed in. TTS uses PowerShell `System.Speech` `SetOutputToWaveFile`.
- [ ] Step 4: Run again. Expected: PASS.
- [ ] Step 5: Commit "scaffold, config, synthetic lectures".

### Task 2: Codes and accusation

**Files:**
- Create: `leaktrace/codes.py`
- Test: `tests/test_codes.py`

**Interfaces:**
- Produces:
  - `bias(key: bytes, lecture: str, n: int) -> np.ndarray[float]`, with values in [0.1, 0.9].
  - `codeword(key, lecture, student: str, n) -> np.ndarray[bool]`.
  - `score(y: np.ndarray, code: np.ndarray, p: np.ndarray) -> float`: the symmetric Tardos score with soft y in [−1, 1]; zero entries are unobserved.
  - `null_stats(key, lecture, y, p, n_null=20000) -> (mean, std)`.
  - `accuse(key, lecture, y, candidates: list[str]) -> list[Suspect]`, sorted by z. `Suspect` holds `student, z, p, p_family, verdict`.

- [ ] Step 1: Write the tests:
  - `test_codeword_is_deterministic_and_keyed`: same inputs give the same codeword; a different key changes about half of it.
  - `test_bias_follows_truncated_arcsine`: with n = 20,000, the mean is 0.5 ± 0.01 and the share of values below 0.2 is between 0.18 and 0.24.
  - `test_single_leaker_identified_from_150_clean_segments`: z > 7, and the leaker's verdict is "identified" with 5,000 candidates.
  - `test_innocents_never_identified`: 200 random observation vectors give no "identified" among 2,000 candidates.
  - `test_interleaved_pair_points_to_a_colluder`: 600 segments mixed from 2 students put one of them in the top 2, with family-wise p below 1e-6.
- [ ] Step 2: Run `pytest tests/test_codes.py -v`. Expected: FAIL.
- [ ] Step 3: Implement. Hash with HMAC-SHA256 and read the first 8 bytes as a uniform number. For bias, map u to `sin²(a + u(b−a))`, where a = asin(√0.1) and b = asin(√0.9). Compute the null with vectorised pseudo-student codewords, then `p = norm.sf(z)` and `p_family = min(1, N·p)`.
- [ ] Step 4: Run again. Expected: PASS.
- [ ] Step 5: Commit "keyed tardos codes and accusation".

### Task 3: Media I/O

**Files:**
- Create: `leaktrace/media.py`
- Test: `tests/test_media.py`

**Interfaces:**
- Produces:
  - `probe(path) -> Probe`, holding duration, has_audio, has_video, width, height, fps and rotation.
  - `frames(path, w, h, fps, start=0.0) -> Iterator[np.ndarray]`: YUV420 frames as a (h·3/2, w) uint8 array. It handles autorotate, constant frame rate and fit-with-padding.
  - `audio(path, sr, start=0.0, dur=None) -> np.ndarray[float32]`: mono; zeros when there is no audio track.
  - `content_box(path) -> (x, y, w, h)`: where the content sits inside 1280×720 after padding.
  - `Encoder(out_dir, name, with_audio)`, a context manager with `.write_frame(yuv)` and `.write_audio(pcm)`. It writes HLS: x264 with a forced IDR frame every 48 frames, no scenecut, AAC at 96 kbps, segments named `{i:05d}.ts` plus `index.m3u8`.
  - `run(args) -> None`, which raises `MediaError` carrying stderr.

- [ ] Step 1: Write the tests:
  - `test_frames_and_audio_roundtrip`: from `lecture_40s`, you get 1000 frames and 1,920,000 samples.
  - `test_encoder_makes_aligned_segments`: 4 segments of 48 frames each, with identical EXTINF values of 1.920.
  - `test_portrait_rotated_input_is_padded_not_stretched`: a generated 720×1280 clip with rotate=90 metadata comes back with a content box narrower than it is tall.
- [ ] Step 2: Run the tests and see them fail.
- [ ] Step 3: Implement with ffmpeg pipes; audio and video go into the encoder through a named temporary WAV, muxed at close.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 4: Timing carrier (warp)

**Files:**
- Create:
  - `leaktrace/carriers/__init__.py`
  - `leaktrace/carriers/patterns.py`
  - `leaktrace/carriers/timing.py`
- Test: `tests/test_timing.py`

**Interfaces:**
- Produces:
  - `patterns.temporal(n: int) -> np.ndarray`, the code s sampled at n points.
  - `patterns.rng(key, *parts) -> np.random.Generator`.
  - `timing.template(seg_audio: np.ndarray, sr) -> np.ndarray`: displacement in seconds, sampled at 100 Hz (192 values). It's zero at both ends, peaks at 30 ms, and concentrates its slope in quiet frames, with speech slope capped at 5 %.
  - `timing.warp_audio(x_with_margin, sr, margin, disp: np.ndarray, sign) -> np.ndarray`: variable-rate WSOLA. Output length equals the segment length.
  - `timing.frame_map(disp, sign, fps=25) -> np.ndarray[int]`: the source frame index for each of the 48 output frames.
  - `timing.measure(leak_feat, mezz_feat, disp) -> (stat, sigma)`: from 8 sub-window cross-correlations.
  - `timing.features(x, sr) -> np.ndarray`: 16-band log envelopes at a 2 ms hop.

- [ ] Step 1: Write the tests:
  - `test_template_shape_and_budget`: zero ends, |peak| = 30 ms ± 1 ms, maximum slope ≤ 0.05 in speech frames.
  - `test_warp_preserves_length_and_identity_at_edges`: the first and last 5 ms are unchanged to within 1e-3 RMS.
  - `test_measured_displacement_recovers_sign`: on clean audio |z| > 8 with the correct sign; after AMR-NB 8 kHz plus reverb, |z| > 3.
  - `test_frame_map_monotonic`.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement.
  - WSOLA uses a 30 ms Hann window with a 15 ms synthesis hop and a ±5 ms search.
  - Invert the output-to-input time map numerically.
  - In `measure`, remove each segment's constant and linear trend from the displacements, then correlate with the detrended template.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 5: Audio carriers (patchwork, loudness, tone balance)

**Files:**
- Create: `leaktrace/carriers/audio.py`
- Test: `tests/test_audio_carriers.py`

**Interfaces:**
- Produces:
  - `embed(x_with_margin, sr, margin, key, lecture, seg, sign) -> np.ndarray` applies all three carriers, faded to zero at the segment edges.
  - `measure(leak_spec, mezz_spec, key, lecture, seg) -> dict[str, (stat, sigma)]`, keyed by `patchwork`, `loudness` and `tilt`. Sigma comes from 16 decoys.
  - `spec(x, sr) -> TileSpec` gives log-magnitudes on the physical tile grid (80 ms × 200 Hz, 300–3300 Hz), independent of sample rate.

- [ ] Step 1: Write the tests:
  - `test_each_audio_carrier_recovers_sign_clean`: |z| > 6.
  - `test_patchwork_survives_aac_64k`: z > 3.
  - `test_loudness_survives_amr_nb`: z > 2.
  - `test_wrong_key_is_noise`: |z| < 3.5 over 50 trials.
  - `test_snr_budget`: the marked versus original SNR is above 25 dB.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement with an STFT (n_fft 2048, hop 512) and bilinear-smoothed tile gains. Loudness is a time-domain gain driven by s(t); tone balance is a shelf at 1 kHz in the same STFT pass.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 6: Picture carriers (brightness, tint, saturation, hue, contrast)

**Files:**
- Create: `leaktrace/carriers/picture.py`
- Test: `tests/test_picture_carriers.py`

**Interfaces:**
- Produces:
  - `embed(yuv: np.ndarray, t_frac: float, box, key, lecture, seg, sign) -> np.ndarray` works in place on a copy. The spatial patterns are functions of normalised content coordinates, built from 12 keyed low-frequency cosine products.
  - `measure(leak_yuv: list[np.ndarray], mezz_yuv: list[np.ndarray], t_fracs, valid: np.ndarray, key, lecture, seg) -> dict[str, (stat, sigma)]`, keyed by `brightness`, `tint`, `saturation`, `hue` and `contrast`. It works at analysis resolution, fits a per-segment photometric model (gain and offset, each with a linear drift in time) per channel, and uses 16 decoys.
  - `register(leak_gray, mezz_gray) -> (H: np.ndarray, valid_mask)`: ORB features plus RANSAC; returns `None` when there are fewer than 12 inliers.

- [ ] Step 1: Write the tests:
  - `test_carriers_recover_sign_clean`: |z| > 6 for brightness, tint and contrast on a grey slide; saturation and hue on a colourful frame.
  - `test_survives_camcord_sim`: perspective, blur σ 1.5, noise 8, gamma 1.1 and a colour cast. Brightness and tint z > 3 after registering.
  - `test_grey_slide_saturation_is_silent`: |z| < 3.5.
  - `test_budget`: maximum |ΔY| ≤ 2 and PSNR above 45 dB.
  - `test_letterbox_untouched`: pixels outside the box are unchanged.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 7: Ingest (packaging)

**Files:**
- Create: `leaktrace/ingest.py`, `leaktrace/store.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: Tasks 3–6.
- Produces:
  - `ingest(master: Path, lecture_id: str, title: str, progress=None) -> LectureMeta`. It writes `lectures/<id>/{mezzanine.mp4, A/, B/, meta.json}`.
  - `LectureMeta` has `id, title, n_segments, marked_segments, duration, box, has_audio, templates`.
  - `store.Store(path)` with `add_student`, `students`, `add_lecture`, `lecture`, `log_view(student, lecture, kind)`, `viewers(lecture, before)`, `jobs` and `traces`.

- [ ] Step 1: Write the tests:
  - `test_ingest_makes_twin_renditions`: equal segment counts; matching EXTINF and first PTS per segment; A ≠ B in bytes for marked segments.
  - `test_partial_tail_unmarked`: a 41 s master has 22 segments, and the last one is byte-identical in A and B, or its decoded content matches.
  - `test_silent_master_ingests_picture_only`.
  - `test_imperceptible`: rendition A decoded against the mezzanine gives PSNR > 42 dB.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement the streaming per-segment loop, with a 0.1 s audio margin.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 8: Index and locate

**Files:**
- Create: `leaktrace/index.py`, `leaktrace/locate.py`
- Test: `tests/test_locate.py`

**Interfaces:**
- Produces:
  - `index.build(lecture_dir)` writes `fp.npz` (sorted 22-bit landmark hashes with times) and `kf.npz` (ORB descriptors for each segment's middle mezzanine frame).
  - `locate(leak: Path, lectures: list[Path]) -> Location | None`. `Location` has `lecture_id, offset, speed, votes, via ('audio'|'video'), segments: list[int]`.

- [ ] Step 1: Write the tests:
  - `test_locates_clip_by_audio`: a 30 s cut at 7.3 s gives an offset within 20 ms and the right segments.
  - `test_locates_speeded_clip`: 1.25× speed comes back as 1.25 ± 0.02.
  - `test_locates_muted_clip_by_video`.
  - `test_rotated_portrait_phone_clip_located`.
  - `test_foreign_video_returns_none`.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement.
  - Landmarks: 8 kHz, n_fft 512, hop 128, a fan-out of 3, and hash = f1(9 bits) | df(7 bits) | dt(6 bits).
  - Speeds 0.80 to 1.25 in steps of 0.02.
  - Video: FLANN LSH matching with a RANSAC line fit.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 9: Trace

**Files:**
- Create: `leaktrace/trace.py`, `leaktrace/fuse.py`
- Test: `tests/test_trace.py`

**Interfaces:**
- Consumes: `locate`, the carrier `measure` functions and `codes.accuse`.
- Produces:
  - `fuse(z: np.ndarray[seg, carrier]) -> (y: np.ndarray, weights: dict)`, with leave-one-out agreement weights.
  - `trace(leak: Path, store, first_seen=None, progress=None) -> TraceReport`, serialisable as JSON. It holds the location, per-carrier strength, per-segment y, suspects, the verdict and a takedown draft.

- [ ] Step 1: Write the tests (these need Task 10's `export`, so build Task 10's export function first, or call its helper):
  - `test_export_is_traced_to_its_student`.
  - `test_trimmed_60s_narrows_to_student`.
  - `test_innocent_student_not_accused`.
  - `test_foreign_video_not_ours`.
  - `test_muted_export_traced_by_picture`.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 10: Delivery

**Files:**
- Create: `leaktrace/deliver.py`
- Test: `tests/test_deliver.py`

**Interfaces:**
- Produces:
  - `playlist(lecture_id, student, base_url, ttl=3600) -> str`: m3u8 whose segment URIs are `/s/{token}`. Each token is HMAC(lecture, i, variant, student, exp), so nobody can forge the other variant.
  - `resolve(token) -> Path`, which raises on a bad or expired token.
  - `export(lecture_id, student) -> Path`: a cached MP4.

- [ ] Step 1: Write the tests:
  - `test_playlist_follows_codeword`.
  - `test_tampered_token_rejected`.
  - `test_expired_token_rejected`.
  - `test_export_matches_playlist_segments`.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 11: Attack bench

**Files:**
- Create: `leaktrace/bench/{__init__,attacks,run}.py`
- Test: `tests/test_attacks.py` (the attack outputs are probed for the expected properties). The bench run itself is a CLI and isn't part of CI.

**Interfaces:**
- Produces:
  - `attacks.ATTACKS: dict[str, Callable[[Path, Path], Path]]`, keyed by telegram, telegram5, p240, crop70, letterbox_portrait, speed125, screenrec, camcord, nokia, muted, audio_only, plus `collude_interleave` and `collude_average` (which take several paths).
  - `run.bench(lecture_id, students=3, clips=(None, 300, 120, 60)) -> BenchResult`, written to `data/bench/<ts>.json` and `.md`.

- [ ] Step 1: Write the tests: `test_nokia_is_qvga_amr` and `test_telegram5_is_480p`.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Run the bench on a 4-minute TTS lecture and tune the budgets. Log each change in the ledger as a Ruling.
- [ ] Step 6: Commit.

### Task 12: Service (API and job queue)

**Files:**
- Create: `leaktrace/server.py`, `leaktrace/jobs.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces these endpoints:
  - `GET /api/health`.
  - Admin, behind a bearer token:
    - `POST /api/lectures` (multipart upload), returning a job;
    - `GET /api/lectures`;
    - `POST /api/students`, `GET /api/students`;
    - `POST /api/sessions {lecture, student}`, returning a playlist URL;
    - `POST /api/traces` (multipart, with first_seen), returning a job;
    - `GET /api/traces/{id}`;
    - `GET /api/jobs/{id}`;
    - `GET /api/bench/latest`.
  - Student-facing: `GET /play/{sid}.m3u8`, `GET /s/{token}`, `GET /download/{token}.mp4`.

- [ ] Step 1: Write the tests:
  - `test_admin_requires_token`.
  - `test_ingest_then_session_then_segments_flow`.
  - `test_trace_job_returns_report`.
  - `test_segment_token_cannot_be_swapped`.
- [ ] Step 2: Run the tests; they fail.
- [ ] Step 3: Implement.
- [ ] Step 4: Run the tests; they pass.
- [ ] Step 5: Commit.

### Task 13: UI

**Files:**
- Create: `frontend/` (Vite React TS Tailwind). Its views are Library, Watch, Trace and Bench.

- [ ] Steps:
  1. `npm run build` passes.
  2. Lint is clean.
  3. Screenshots at desktop and phone widths.
  4. Commit.

### Task 14: Telegram watcher

**Files:**
- Create: `leaktrace/watch.py`
- Test: `tests/test_watch.py`

**Interfaces:**
- Produces: `Watcher(source, tracer, store)` with `.handle(message) -> TraceReport | None`. `TelegramSource` wraps Telethon and is never exercised in CI; `FakeSource` exists for tests.

- [ ] Step 1: Write `test_new_video_message_is_traced_once` and `test_non_video_ignored`.
- [ ] Steps 2–5: TDD as usual, then commit.

### Task 15: Ship

**Files:**
- Create:
  - `Dockerfile`
  - `.github/workflows/ci.yml`
  - `README.md`
  - `LICENSE` (MIT, if the user agrees; ask)

- [ ] Steps:
  1. The Docker build works.
  2. CI is green.
  3. The README documents the threat model, results table and usage.
  4. Push to a private GitHub repo.

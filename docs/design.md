# LeakTrace: design

## Why

Ed-tech platforms stream recorded lectures. Students download them or record the screen, and the copies end up in Telegram groups. Encryption (DRM) stops easy downloads, but nothing stops a phone pointed at a screen. LeakTrace takes the other route: every student receives a copy that is unique in many independent, invisible ways. Any leaked copy, however damaged, can then be traced back to the account it came from, with a stated confidence.

## Goals

- **Unique copies at streaming scale.** Each lecture is encoded exactly twice (renditions A and B). Each student's stream or download is a per-student sequence of A and B segments, so there is no per-student encoding.
- **Survive real leaks:**
  - chains of re-encoding (Telegram, WhatsApp, forwarding through groups);
  - downscaling to 240p, cropping, letterboxing, speed changes and trimming;
  - screen recording;
  - filming the screen with a phone, including old phones (QVGA video, 8 kHz AMR voice audio);
  - muted (picture-only) or audio-only copies;
  - a few students mixing their copies (collusion).
- **Trace.** From any leaked file, find the lecture and time range, read the evidence, and score the candidate accounts (narrowed by view logs). Report the account with a false-accusation probability that is controlled by maths, not guessed.
- **Discover.** Optionally watch Telegram channels and groups, and trace new uploads automatically.
- **Imperceptible.** Every carrier has a measured budget that students can't see or hear.
- **Production grade:**
  - streaming processing, never whole videos in memory;
  - a job queue;
  - an HTTP API with a UI;
  - per-student signed playlists and downloads;
  - tests and an attack bench;
  - Docker and CI.

## Non-goals

- Encryption or DRM: the platform keeps its existing vendor. LeakTrace produces the A/B segments and per-student manifests, or runs alongside as the tracing side.
- Visible watermarks: platforms usually have one already, and it can be added on top.
- Identifying a person beyond the account. Shared logins are a separate policy question.

## Anti-goals (failure even if it "works")

- Accusing an innocent account. The false-accusation probability is bounded and reported, and below the threshold the system says "narrowed" or "inconclusive", never a name.
- Any artifact a student notices.
- Needing the complete lecture to trace it.

## Constraints

- Classical signal processing only: no ML models. Permissive licences only, with no GPL dependencies.
- Python 3.13 and ffmpeg 6 or later. Developed on Windows, deployed on Linux (Docker).
- Secrets live only in the environment (`LEAKTRACE_SECRET`); `.env` is gitignored.
- No company names anywhere in the repo.

## Approaches considered

| Approach | What it is | Verdict |
|---|---|---|
| Picture-only A/B mark | What most vendors sell | Rejected: the first thing to break when a phone films the screen |
| Audio-first | Timing plus audio carriers | Strongest against phones, but loses muted clips |
| **Full multi-carrier (chosen)** | Picture, audio and timing carriers on the same A/B bit | Every family stands alone. The tracer learns which families survived each leak and weighs them |

The user chose the full multi-carrier system, built production grade, as a standalone project to be linked from the agents site later.

## How it works

### Segment grid

- Segments are **1.92 s** long: 48 frames at 25 fps, which is 92,160 samples at 48 kHz, or 90 AAC frames. Video and audio frames therefore both line up exactly on segment boundaries.
- Every carrier's modulation fades to zero at segment boundaries. A and B segments therefore splice seamlessly, and both renditions have identical segment durations and timestamps.

### Codes (who gets A, who gets B)

- **Secret key:** K.
- **Per-segment bias:** for lecture L and segment i, `p_i ∈ [0.1, 0.9]` is drawn from a truncated arcsine distribution, using HMAC(K, "bias", L, i).
- **Each student's bit:** student u's bit is `c_ui = 1` if and only if HMAC(K, "bit", L, u, i), read as a uniform number in [0,1), is below `p_i`.
- **Bit to rendition:** bit 1 is rendition A (carrier sign +1); bit 0 is rendition B (sign −1).
- **Why this works:**
  - It's fully deterministic, so nothing is stored per student.
  - Biased codes (a Tardos code with a 0.1 cutoff) still point to at least one member of a small coalition that mixes copies.
  - The expected evidence per segment for a single leaker is about 0.86 standard deviations.

### Carriers

Every carrier is antipodal, meaning rendition A adds +x and B adds −x. Each one is keyed per lecture and segment, and each is zero at segment boundaries. Picture and audio carriers use a zero-mean temporal code s(t) inside the segment, so static distortions (vignetting, room tone, a fixed equaliser) cancel out.

| # | Family | Carrier | What changes | Initial budget (the bench tunes these) |
|---|---|---|---|---|
| 1 | Picture | Brightness | Smooth keyed low-frequency luma pattern | ≤ 1.5 code values |
| 2 | Picture | Tint | Additive keyed Cb and Cr patterns. Hue and saturation move everywhere, including on grey and white slides | ≤ 1.2 code values |
| 3 | Picture | Saturation | Chroma radius × (1 + pattern), in colourful regions | ≤ 1.5 % |
| 4 | Picture | Hue | Chroma angle + pattern, in colourful regions | ≤ 0.6° |
| 5 | Picture | Contrast | Tone around mid-grey | ≤ 0.6 % |
| 6 | Audio | Spectral patchwork | Keyed gains on 80 ms × 200 Hz tiles, 300–3300 Hz | ≤ 0.7 dB per tile |
| 7 | Audio | Loudness | Broadband gain envelope | ≤ 0.25 dB |
| 8 | Audio | Tone balance | Low band versus high band | ≤ 0.4 dB |
| 9 | Timing | Time warp | Displacement ±Δ (Δ = 30 ms), placed in pauses where possible; audio by WSOLA, video by frame remap | max local speed change ≤ 5 % in speech |

Picture carriers are applied only inside the content area, never to letterbox bars. A carrier with nothing to work on, such as saturation on a grey slide, simply reports zero evidence. The fusion step gives it no weight for that leak.

The final segment of a lecture is usually partial. It carries no marks, so A and B are identical there.

### Ingest (packaging)

Processing is streaming throughout.

1. ffmpeg decodes the master and normalises it: 1280×720 (padded), constant 25 fps, 48 kHz mono.
2. For each segment:
   1. Read 48 frames and 92,160 samples, plus a context margin for WSOLA.
   2. Compute the warp template from the master's audio.
   3. Render frames and audio for A and for B.
   4. Pipe both into two ffmpeg encoders: x264 with a forced IDR frame every 48 frames and scenecut off, AAC at 96 kbps. Each encoder writes HLS (TS segments) for its rendition.
3. Ingest also writes:
   - an unmarked high-quality mezzanine (the reference for informed detection);
   - an audio landmark index and video keyframe descriptors, used to locate leaked clips;
   - `meta.json`: version, parameters and per-segment templates.

### Delivery

- **Per-student HLS playlist:** built from the codeword, with short-lived signed URLs (HMAC tokens).
- **Per-student MP4 download:** concatenated with ffmpeg stream copy, then cached.
- **View log:** every playlist or download request writes one, recording student, lecture and time. It narrows the suspects at trace time.

### Trace

1. **Decode** the leaked file; ffmpeg reads any container or codec.
2. **Locate:**
   - Audio landmarks (Shazam-style, searched across several playback speeds) give the lecture, offset and speed.
   - Video keyframes (ORB features plus RANSAC homography) confirm this, or replace it for muted clips.
3. **Align each segment:**
   - Cross-correlating the leak against the mezzanine in sub-windows gives a fine time-displacement curve.
   - A homography registers the leaked frames onto the mezzanine.
4. **Measure:** every carrier gives a statistic, which is turned into a z-score using decoy patterns (keys that were never embedded) as the noise reference.
5. **Fuse:** per-carrier weights are estimated from agreement across segments, giving a soft bit `y_i ∈ [−1, 1]` per segment.
6. **Accuse:**
   1. The candidates are students who opened the lecture before the leak was first seen, or all students if there are no logs.
   2. Each candidate gets a symmetric Tardos soft score.
   3. A null distribution comes from at least 20,000 random pseudo-students, giving p-values and a family-wise bound of N·p.
   4. The verdict follows from that bound:
      - **Identified** when the bound is below 1e-6.
      - **Narrowed** (top-k accounts) when it is below 1e-2.
      - **Inconclusive** otherwise.
7. **Report:** JSON plus a UI view. It shows the lecture and time range, the strength of each carrier, per-segment evidence, the candidates with their scores, any coalition, and a takedown notice draft.

### Discovery

A Telethon client runs with the operator's own API credentials and login. It watches the listed channels and groups, downloads new videos (up to a size cap), traces them, and stores reports. It can also call a webhook.

### Attack bench

- **Attack chains** (ffmpeg and numpy):
  - Telegram re-encode ×N;
  - 240p;
  - crop and letterbox;
  - 1.25× speed;
  - trimming to 5, 2 and 1 minutes;
  - screen recording;
  - simulated camcording: perspective, blur, noise, colour cast, vignette, frame-rate change, band-limited reverberant audio;
  - **Nokia**: QVGA MPEG-4 Part 2 with AMR-NB 8 kHz audio;
  - muted, and audio-only;
  - collusion: two or three students, interleaved or averaged.
- **Output:**
  - a table of identification, narrowing and false-accusation rates per attack and clip length;
  - imperceptibility metrics: PSNR, maximum colour change, audio SNR, maximum speed change.

### Service

- FastAPI app:
  - an admin API covering ingest, students, traces and bench, protected by a bearer token;
  - the delivery endpoints;
  - a job queue (thread pool plus a SQLite job table) for ingest and trace.
- React UI with four views:
  - **Library:** lectures and their ingest status;
  - **Watch as a student:** hls.js playback plus a download button;
  - **Trace:** upload a leaked file and get a verdict with its evidence;
  - **Bench:** the attack results.
- Docker image (Python plus ffmpeg), and CI running the tests.

### Storage

- SQLite holds students, lectures, views, jobs and traces.
- Files go under `LEAKTRACE_DATA/lectures/<id>/`: the mezzanine, `A/` and `B/` segment folders, `meta.json` and the index files.

## Verification

- **Unit tests** for each module.
- **Integration test:** a synthetic 2-minute lecture is ingested, three students' copies are exported, attacked and traced, and each is identified with no false accusation.
- **Bench** on synthetic lectures generated offline: rendered slides, Windows speech synthesis and a moving element.
- **Real phone test:** the user films the screen and uploads the clip through the UI.

## Left to the builder

These are tuned with the bench and logged in the plan's ledger:
- carrier strengths;
- WSOLA window and search sizes;
- landmark parameters;
- registration settings;
- decoy counts;
- verdict thresholds.

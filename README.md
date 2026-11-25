# LeakTrace

Forensic watermarking for any streaming service. Every viewer gets an invisibly unique copy of each title; when a copy leaks (re-compressed on Telegram, cropped, sped up, screen-recorded, or filmed off a laptop with an old phone), LeakTrace finds which title it is, reads the hidden marks, and names the account it came from, with a false-accusation probability that comes from maths rather than a guess.

It plugs into what you already run: HLS and DASH (CMAF), any CDN, any player, your own DRM. No machine learning: classical signal processing only.

## How it works

**Two encodes, millions of unique copies.** Each title is cut into segments of whole frames, about two seconds each, at its own frame rate and resolution. It's marked twice, as variant A and variant B, and each variant is encoded into a normal quality ladder of fragmented-MP4 segments. Every viewer gets A or B for each segment according to a secret code, so every copy is unique while every object stays shared and cacheable. The switch happens at the CDN edge from a signed token in the request path, so manifests are the same for everyone and nobody can see or swap which variant they get (the industry's A/B model; see ETSI TS 104 002).

**Nine independent marks in three families.** The A/B difference is written into every channel a leak might keep:

| Family | Marks | What keeps it alive |
|---|---|---|
| Timing | ±30 ms shifts, placed mostly in pauses | Survives everything that keeps the sound, including a phone mic in a room and 8 kHz AMR audio |
| Sound | spectral patchwork, slow loudness swell, bass/treble balance | Low-frequency, slow, and spread over the whole speech band |
| Picture | brightness, tint, saturation, hue, local contrast | Large, smooth patterns tied to the content, read after undoing the camera's geometry and colour |

Each mark is far below what a viewer can notice (a marked master measures 41.2 dB PSNR against the original). The same segment carries the same bit in every rung and in the audio, so a player switching quality changes nothing. At trace time LeakTrace measures which families survived this particular leak and weighs them accordingly: a muted clip is read from its picture, a phone recording mostly from its timing and sound.

**Finding the title.** Audio landmark fingerprints, searched over playback speeds from 0.75x to 2x, place a clip in the library to the frame. Clips with no sound are placed by matching their picture sequence. When a clip matches several places about equally (a recap, a series intro, the same film in two editions), the marks decide which one it really is.

**Naming the account.** The A/B choices follow a Tardos code, which keeps pointing at a real member even when several viewers splice or average their copies. Every candidate who opened the title before it leaked is scored. The verdict uses an exact Chernoff bound on the chance that an innocent account scores that high, multiplied by the number of candidates:

| Verdict | Meaning |
|---|---|
| **Identified** | Chance the named account is wrong is below one in a million |
| **Narrowed** | Points at one account, below 1 in 100, but not strong enough to name |
| **Inconclusive** | Too short or too damaged; nobody is accused |
| **Not ours** | Not one of the titles in the library |

All nine marks carry the same bit, one per segment, so evidence grows with length: about two minutes of a leak is usually enough to identify among thousands of accounts, and one minute to narrow.

## Results

Real copies of a 4-minute sample title, put through what leaks go through and traced among 1,000 candidate accounts (69 runs, two different viewers per row; **0 innocent accounts named**). *Named* means identified (chance of a wrong name below one in a million); *narrowed* points at the right account but below the bar for naming it.

| Leak | Full length | 2 minutes | 1 minute |
|---|---|---|---|
| Telegram re-compression | 2/2 named | 2/2 named | 2 narrowed |
| Forwarded through 5 Telegram groups | 2/2 named | 2/2 named | 2 narrowed |
| Shrunk to 240p | 2/2 named | 2/2 named | 2 narrowed |
| Cropped to 70% | 2/2 named | 2/2 named | 2 narrowed |
| Letterboxed into portrait | 2/2 named | 1 named, 1 inconclusive | 2 narrowed |
| Sped up 1.25x | 2/2 named | 2/2 named | 1 named, 1 inconclusive |
| Screen recording | 2/2 named | 2/2 named | 2 narrowed |
| Phone filming the screen | 2/2 named | 2/2 named | 1 narrowed, 1 inconclusive |
| Old phone (QVGA, 8 kHz AMR audio) | 1 named, 1 inconclusive | 1 narrowed, 1 inconclusive | 2 inconclusive |
| Sound removed | 2/2 named | 2/2 named | 2 narrowed |
| Sound only | 2/2 named | 2/2 named | 2 narrowed |
| Two viewers splicing their copies | one of them narrowed | | |
| … then re-compressed | one of them narrowed | | |
| Two viewers averaging their copies | one of them named | | |

Run it yourself: `python -m leaktrace bench <title>`.

## Plugging it in

Package a title once (`python -m leaktrace ingest master.mp4 --id ep3 --name "Episode 3"`, or `POST /api/titles`). It keeps the source's frame rate (exactly, 29.97 included) and size (capped at `LEAKTRACE_MAX_HEIGHT`, never upscaled), and writes:

```
titles/ep3/
  cmaf/                 → upload to your origin bucket as /ep3/ (this is all a CDN ever serves)
    master.m3u8  v1080.m3u8 … audio.m3u8  manifest.mpd     shared manifests
    v1080-init.mp4 … audio-init.mp4                         init segments (identical for A and B)
    3f9c…e1.m4s …                                           every segment of A and of B, under unguessable names
  masters/A.mp4 B.mp4   marked masters (for your own pipeline, below)
  mezzanine.mp4 fp.npz kf.npz meta.json                     what tracing needs: keep private
```

Your backend asks for a viewer's session when they press play (`POST /api/sessions {"title", "viewer"}`, logged as a view) and hands the player one of these URLs. Pick the mode that fits your CDN:

### 1. CDN edge switching (recommended)

The player loads `https://<cdn>/wm/<token>/ep3/master.m3u8` (or `manifest.mpd`). Manifests are the same for everyone; an edge function maps each segment request to the object for that viewer's variant, fetched by its own URL, so the CDN caches each object once for everybody.

- **Cloudflare Workers:** deploy `edge/cloudflare-worker.js` (with `edge/core.js`) on the route `<cdn>/wm/*`. Bind your R2 bucket as `MEDIA`, or set `ORIGIN`. Store the keys as secrets: `python -m leaktrace edge-keys --out edge.env` writes `LEAKTRACE_EDGE_KEY` and `LEAKTRACE_NAMING_KEY`.
- **Amazon CloudFront:** `python -m leaktrace edge-bundle cloudfront --out leaktrace-cf.js` writes a CloudFront Function (runtime `cloudfront-js-2.0`, viewer request) with the keys filled in. Attach it to a `/wm/*` behavior whose origin is the S3 bucket; add CORS with a response headers policy.
- **Anything else:** `edge/core.js` is plain JavaScript with no imports (one function, `leaktraceRoute`), and `leaktrace/edge.py` is the same logic in Python. The tests run both on the same requests.

The edge holds two keys derived from `LEAKTRACE_SECRET`, never the secret itself. An expired, edited or foreign token gets 403; nothing ever falls back to a default variant.

### 2. Per-viewer manifests (CDNs without edge code)

The player loads `/pl/<token>/ep3/master.m3u8` (or `manifest.mpd`) from this service: the same manifests, with every segment URL pointing straight at that viewer's object under `LEAKTRACE_MEDIA_URL` (your CDN in front of the bucket). Segment URLs carry no token, so they cache like any static file. Only the small manifests are per viewer.

### 3. Your own pipeline

If you encode and package with your own tools, take the marked masters instead: `python -m leaktrace export-masters ep3 --out ep3-masters/` writes `A.mp4`, `B.mp4` and `grid.json` (frame rate, frames per segment, segment count). Encode and package each master exactly as you do today, with segments of `seg_frames` frames and an IDR frame at the start of each (the masters already have one there), A into `/ep3/A/` and B into `/ep3/B/` with the same file names. Then put the edge in front with `LEAKTRACE_EDGE_LAYOUT=folders`: manifests and init segments come from `A/`, and segment *n* (the number in its file name, counted from `LEAKTRACE_EDGE_FIRST_SEGMENT`) from the viewer's folder. Cut the audio on the same boundaries, or the sound marks blur.

### Downloads

`/download/<token>` serves the viewer's own MP4 (their variant of every segment, the top rung plus audio), logged as a download.

### Tracing

Drop a leak into the UI, `POST /api/traces`, or `python -m leaktrace trace leaked.mp4 --first-seen 2026-11-20T10:00:00Z`. The report gives the verdict, the accounts scored, which marks survived and a takedown notice naming `LEAKTRACE_ORG`. `python -m leaktrace watch @channel` traces every new video posted in Telegram chats your account has joined.

## What it can't do

- **Very short clips.** A 20-second clip carries about ten bits. It will rarely get past "narrowed", by design: the bound refuses to name anyone on thin evidence.
- **Large coalitions.** Two or three viewers mixing their copies still point at one of them. Dozens mixing carefully will dilute every mark; the code length needed grows with the square of the coalition size.
- **Re-creation.** Someone who re-records the title in their own voice, or retypes the slides, has made a new work with no marks in it.
- **Beyond the account.** It identifies the account that received the copy, not the person holding the phone.
- **Shared links.** A session token plays as its owner until it expires (6 hours by default), so a copy made from a link someone shared traces back to the person who shared it. Hand tokens only to signed-in viewers, and keep them short-lived.
- **Live streams.** Titles are packaged before they're published.

## Running it

Needs Python 3.13, ffmpeg 6 or later, and Node 22 for the UI.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cp .env.example .env
python -m leaktrace keygen          # paste into LEAKTRACE_SECRET in .env, and set LEAKTRACE_ADMIN_TOKEN
```

Keep `LEAKTRACE_SECRET` safe and never change it: every code, token and object name comes from it. Every setting is in `.env.example`.

```bash
python -m leaktrace synth demo.mp4 --seconds 240                  # a synthetic title to try it on
python -m leaktrace ingest demo.mp4 --id demo --name "Demo title"
python -m leaktrace viewer v1001 "Asha Rao"
python -m leaktrace export demo v1001                              # Asha's own MP4
python -m leaktrace trace leaked.mp4
python -m leaktrace bench demo                                     # the attack bench below
```

Service and UI:

```bash
cd frontend && npm ci && npm run build && cd ..
python -m uvicorn leaktrace.server:app --port 8003                 # then open http://127.0.0.1:8003
```

Public demo: set `LEAKTRACE_DEMO=1` on a service with no real titles or viewers. It opens on a guided page (watch a viewer's copy, pick a sample leak, see whose copy it was, try your own) with a short tour, and the console behind one link. No admin token, no new titles; the first start packages `demo/lecture.mp4` and makes the sample leaks, which takes a few minutes.

Docker:

```bash
docker build -t leaktrace .
docker run -p 8003:8003 -v leaktrace-data:/data -e LEAKTRACE_SECRET=... -e LEAKTRACE_ADMIN_TOKEN=... leaktrace
```

### API

Admin endpoints take `Authorization: Bearer $LEAKTRACE_ADMIN_TOKEN`.

| Endpoint | |
|---|---|
| `POST /api/titles` | multipart `id`, `name`, `file`: package a title (background job) |
| `POST /api/viewers` | `{"viewers": [{"id", "name"}]}` |
| `POST /api/sessions` | `{"title", "viewer"}` → `token`, `hls`, `dash` (edge mode), `direct.hls`, `direct.dash` (per-viewer manifests), `download` |
| `POST /api/traces` | multipart `file`, optional `first_seen`: trace a leak (background job) |
| `GET /api/jobs/{id}` | job status, progress and result |
| `GET /api/traces/{id}` | the full trace report |

Viewer-facing: `GET /wm/{token}/{title}/…` (shared manifests and objects; this service does the edge's job when there's no CDN in front), `GET /pl/{token}/{title}/…` (per-viewer manifests), `GET /media/{title}/{object}` (objects), `GET /download/{token}`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -m "not slow"     # a few minutes
python -m pytest -m slow           # packages and traces 150 s titles at 25 and 29.97 fps
```

## Licence

Not chosen yet. LeakTrace calls ffmpeg as a separate program, and does not link it.

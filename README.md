# LeakTrace

Every student gets an invisibly unique copy of each lecture. When a copy leaks (re-compressed on Telegram, cropped, sped up, screen-recorded, or filmed off a laptop with an old phone), LeakTrace finds which lecture it is, reads the hidden marks, and names the account it came from, with a false-accusation probability that comes from maths rather than a guess.

No machine learning: classical signal processing only.

## How it works

**One encode, millions of unique copies.** Each lecture is cut into 1.92-second pieces and encoded exactly twice, as rendition A and rendition B. The two differ by invisible marks. Each student's playlist or download picks A or B for every piece according to a secret code, so every copy is unique without encoding anything per student. Segment URLs are opaque signed tokens, so nobody can see or swap which version they're getting.

**Nine independent marks in three families.** The A/B difference is written into every channel a leak might keep:

| Family | Marks | What keeps it alive |
|---|---|---|
| Timing | ±30 ms shifts, placed mostly in pauses | Survives everything that keeps the sound, including a phone mic in a room and 8 kHz AMR audio |
| Sound | spectral patchwork, slow loudness swell, bass/treble balance | Low-frequency, slow, and spread over the whole speech band |
| Picture | brightness, tint, saturation, hue, local contrast | Large, smooth patterns tied to the content, read after undoing the camera's geometry and colour |

Each mark is far below what a viewer can notice (a marked copy measures PSNR_DB dB PSNR against the original). At trace time LeakTrace measures which families survived this particular leak and weighs them accordingly: a muted clip is read from its picture, a phone recording mostly from its timing and sound.

**Finding the lecture.** Audio landmark fingerprints, searched over playback speeds from 0.75x to 2x, place a clip in the library to the frame. Clips with no sound are placed by matching their picture sequence.

**Naming the account.** The A/B choices follow a Tardos code, which keeps pointing at a real member even when several students splice or average their copies. Every candidate who opened the lecture before it leaked is scored. The verdict uses an exact Chernoff bound on the chance that an innocent account scores that high, multiplied by the number of candidates:

| Verdict | Meaning |
|---|---|
| **Identified** | Chance the named account is wrong is below one in a million |
| **Narrowed** | Points at one account, below 1 in 100, but not strong enough to name |
| **Inconclusive** | Too short or too damaged; nobody is accused |
| **Not ours** | Not one of the lectures in the library |

All nine marks carry the same bit, one per 1.92 s piece, so evidence grows with length: about two minutes of a leak is usually enough to identify among thousands of accounts, and one minute to narrow.

## Results

RESULTS_TABLE

## What it can't do

- **Very short clips.** A 20-second clip carries about ten bits. It will rarely get past "narrowed", by design: the bound refuses to name anyone on thin evidence.
- **Large coalitions.** Two or three students mixing their copies still point at one of them. Dozens mixing carefully will dilute every mark; the code length needed grows with the square of the coalition size.
- **Re-creation.** Someone who re-records the lecture in their own voice, or retypes the slides, has made a new work with no marks in it.
- **Beyond the account.** It identifies the account that received the copy, not the person holding the phone.

## Running it

Needs Python 3.13 and ffmpeg 6 or later.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cp .env.example .env
python -m leaktrace keygen          # paste into LEAKTRACE_SECRET in .env, and set LEAKTRACE_ADMIN_TOKEN
```

Keep `LEAKTRACE_SECRET` safe and never change it: every code and every segment token comes from it.

Command line:

```bash
python -m leaktrace synth demo.mp4 --seconds 240                  # a synthetic lecture to try it on
python -m leaktrace ingest demo.mp4 --id demo --title "Demo lecture"
python -m leaktrace student s1001 "Asha Rao"
python -m leaktrace export demo s1001                              # Asha's own MP4
python -m leaktrace trace leaked.mp4 --first-seen 2026-11-20T10:00:00Z
python -m leaktrace bench demo                                     # the attack bench below
python -m leaktrace watch @some_channel                            # trace every new video posted there
```

Service and UI:

```bash
cd frontend && npm ci && npm run build && cd ..
python -m uvicorn leaktrace.server:app --port 8003                 # then open http://127.0.0.1:8003
```

Docker:

```bash
docker build -t leaktrace .
docker run -p 8003:8003 -v leaktrace-data:/data -e LEAKTRACE_SECRET=... -e LEAKTRACE_ADMIN_TOKEN=... leaktrace
```

### API

Admin endpoints take `Authorization: Bearer $LEAKTRACE_ADMIN_TOKEN`.

| Endpoint | |
|---|---|
| `POST /api/lectures` | multipart `id`, `title`, `file`: package a lecture (background job) |
| `POST /api/students` | `{"students": [{"id", "name"}]}` |
| `POST /api/sessions` | `{"lecture", "student"}` → that student's signed playlist and download links |
| `POST /api/traces` | multipart `file`, optional `first_seen`: trace a leak (background job) |
| `GET /api/jobs/{id}` | job status, progress and result |
| `GET /api/traces/{id}` | the full trace report |

Student-facing: `GET /play/{token}.m3u8` (the student's playlist, logged as a view), `GET /s/{token}` (one segment), `GET /download/{token}` (their MP4).

A platform that already has a player and DRM keeps them: it serves the two renditions as it does today and asks LeakTrace for each student's playlist, or generates the same playlists itself from the shared secret.

### Telegram watcher

Set `LEAKTRACE_TG_API_ID` and `LEAKTRACE_TG_API_HASH` (from my.telegram.org) and run `python -m leaktrace watch <channels...>`. It uses your own Telegram account, sees only chats that account has joined, traces each new video or audio post once, and can post identified or narrowed results to `LEAKTRACE_WEBHOOK`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -m "not slow"     # a few minutes
python -m pytest -m slow           # packages and traces a 150 s lecture
```

## Licence

Not chosen yet. LeakTrace calls ffmpeg as a separate program, and does not link it.

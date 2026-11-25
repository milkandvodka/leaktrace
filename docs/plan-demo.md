# Guided Public Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In demo mode, the service opens on a single guided page (watch → sample leaks → trace) with a replayable tour; the existing tabs move behind a Console link.

**Architecture:** A new `leaktrace/demo.py` builds the demo audience (1,000 accounts) and the sample leaks (bench attacks on 2-minute excerpts of named viewers' copies), traces each once, and records them in `<data>/demo/samples.json`. `server.py` runs it in a background job after the demo title is ready and exposes `GET /api/demo` and `GET /api/demo/samples/{key}.mp4`. The React app gets `Demo.tsx` and `Tour.tsx`; `App.tsx` switches between demo and console.

**Tech Stack:** Python 3.13, FastAPI, ffmpeg; React 19, Tailwind 4, hls.js, lucide-react (all already installed; no new dependencies).

**Spec:** `docs/demo-design.md`

## Global Constraints

- No new marking or tracing logic; no fake progress or canned verdicts.
- Nothing removed: every existing tab stays reachable from **Console**.
- Outside demo mode the console stays the default page; `/api/demo` answers 404.
- Sample keys and attacks: `telegram` (demo-v004), `camcord` (demo-v002), `crop70` (demo-v005), `screenrec` (demo-v001); excerpts of 120 s.
- Audience: the six named viewers plus `demo-a0001`…`demo-a0994`, all logged as having played the title at `2026-01-01T00:00:00`.
- No company names; generic wording. Layout works at phone width with 16px gutters.
- `localStorage` access wrapped in try/catch.

## Review Focus

1. First start, samples not ready yet → `/api/demo` says `preparing: true`, the page says so and polls; no broken cards.
2. Restart in the middle of making samples → the next start makes only the missing ones.
3. Tour at phone width → box stays inside the screen, Skip always reachable.
4. Storage blocked (third-party iframe) → tour still works, no crash.
5. Unknown sample key or path tricks (`../`) → 404, never a file outside `<data>/demo/`.

---

### Task 1: Demo audience, sample leaks and the demo API

**Files:**
- Create: `leaktrace/demo.py`
- Modify: `leaktrace/server.py` (seed_demo, _package, two routes)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `demo.SAMPLES: list[dict]` (key, attack, viewer, label, blurb); `demo.audience() -> list[str]`; `demo.add_audience(store, title_id) -> None`; `demo.done() -> list[dict]` (samples.json rows: key, label, blurb, trace); `demo.missing() -> list[str]`; `demo.make_samples(store, title_id, keys=None, seconds=120, progress=None) -> list[dict]`.
- `GET /api/demo` → `{"title": {"id","name","status"} | null, "viewers": [{"id","name"}] (the six), "samples": [{"key","label","blurb","video","trace"}], "preparing": bool, "audience": int}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_server.py`:
  - `test_demo_api_is_off_outside_demo_mode`: `GET /api/demo` → 404.
  - `test_demo_api_says_samples_are_being_prepared`: demo mode, empty `<data>/demo/`, → 200, `preparing` true (title not ready or samples missing), `viewers` are the six named ones, `samples == []`.
  - `test_demo_sample_route_only_serves_known_samples`: `/api/demo/samples/nope.mp4` and `/api/demo/samples/..%2Fleaktrace.db` → 404.
  - `test_missing_lists_only_samples_not_made_yet` (unit, tmp data dir): with samples.json holding `telegram`, `missing()` is the other three keys.
  - slow `test_a_sample_leak_traces_back_to_its_viewer`: `make_samples(store, "lec40", keys=["telegram"], seconds=40)` → its trace's top suspect is demo-v004, no other account identified, `missing()` no longer lists telegram, the mp4 exists.
- [ ] **Step 2: Run them; expect failures** (404 route missing / module missing).
- [ ] **Step 3: Implement `leaktrace/demo.py` and the server changes.** `make_samples` uses `deliver.export`, `bench.attacks.trim(copy, work, min(seconds, duration))`, then `ATTACKS[attack]`, moves the result to `<data>/demo/<key>.mp4`, runs `trace(..., candidates=audience())`, and rewrites samples.json after each sample. `seed_demo` submits one job: package if needed, then `add_audience` + `make_samples(missing())`.
- [ ] **Step 4: Run `pytest tests/test_server.py -v`; expect PASS.**
- [ ] **Step 5: Commit** `feat: demo audience, sample leaks and /api/demo`.

### Task 2: Guided demo page, tour and console switch

**Files:**
- Create: `frontend/src/Demo.tsx`, `frontend/src/Tour.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/Trace.tsx` (export the upload form as `LeakUpload`)

**Interfaces:**
- Consumes: `GET /api/demo`, `POST /api/sessions`, `GET /api/traces/{id}`, `POST /api/traces`.
- Produces: `Tour({ steps, open, onClose })` with steps `{ target: string (data-tour value) | null, title, text }`.

- [ ] **Step 1: Build** the page: hero sentence; step 1 viewer chips + player (HLS, download link); step 2 four sample cards (video + label + Trace button, "Preparing…" state polling every 10 s); step 3 the existing `Report`; "Try your own" with `LeakUpload`; "How it plugs in" (3 lines) + bench link.
- [ ] **Step 2: Build** the tour: dimmed backdrop with a spotlight on the target, box with title, text, step dots, Skip / Next / Done; first visit only (`lt-tour-seen`), **?** button replays; Escape closes.
- [ ] **Step 3: App.tsx:** demo mode → Demo page by default, header **?** and **Console**; console view keeps the tabs with a **Demo** link back.
- [ ] **Step 4: Verify:** `npm run build` and `npm run lint` clean; in Chrome against a local demo server: tour shows on first load, Skip and replay work, a sample trace shows "Leaked from …", width 375 has no horizontal scroll.
- [ ] **Step 5: Commit** `feat: guided demo page with a quick tour`.

### Task 3: Ship

- [ ] Full test suite green; README demo paragraph; push; `railway up --service leaktrace`; wait for samples on the live demo; check each sample's verdict and the mrw site's tab.

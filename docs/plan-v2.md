# LeakTrace v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make LeakTrace a generic plug-in for any streaming service: titles and viewers, any source format, CMAF output for HLS and DASH, and three ways to switch A/B per viewer that keep CDN caching intact.

**Architecture:** Packaging marks each title once at its own frame rate and resolution, encodes a quality ladder in variants A and B as CMAF segments with unguessable names, and writes one shared HLS and DASH manifest set. Per-viewer switching happens at the CDN edge (signed token carrying the viewer's A/B decisions), in per-viewer manifests, or in the platform's own pipeline from two exported marked masters. Tracing is unchanged apart from reading the title's own grid.

**Tech Stack:** Python 3.13, numpy, OpenCV, ffmpeg (x264, AAC, fragmented MP4), FastAPI, SQLite; JavaScript edge functions (Cloudflare Workers, CloudFront Functions); React UI with hls.js.

**Spec:** `docs/design.md`, section "v2: a generic plug-in for any streaming service".

## Global Constraints

- No company names anywhere in the repo. Terms: *title*, *viewer*.
- Classical signal processing only; secrets only in the environment.
- Segments: whole frames, about `LEAKTRACE_SEGMENT_SECONDS` (default 2.0); audio 48 kHz.
- Every segment object is shared by all viewers (cacheable); only tokens or manifests are per viewer.
- Never replace a packaged title in place.

## Review Focus

1. **A 29.97 fps source:** the grid uses exact rational frame rates; segment boundaries never drift over an hour.
2. **Mixed A/B playback:** a manifest mixing A and B segments decodes with no errors at every switch (identical init segments per rung).
3. **Quality switching:** the same viewer gets the same bit for segment n at every rung and in the audio track.
4. **Expired or altered token:** the edge refuses it (403), never falls back to a default variant.
5. **Source smaller than a rung:** rungs above the source height are skipped, never upscaled.

---

### Task V1: Titles and viewers
Rename lecture → title and student → viewer in code, database, API, CLI, UI and tests. The takedown notice uses `LEAKTRACE_ORG`. Tests: the existing suite, renamed, passes.

### Task V2: Each title keeps its own frame rate and resolution
`Grid(fps: Fraction, seg_frames: int)` in the title's metadata; `seg_samples = seg_frames / fps * 48000`. Marking at the source canvas (capped at `LEAKTRACE_MAX_HEIGHT`), analysis mezzanine fitted inside 1280×720 at the title's frame rate. Carriers, ingest, index, locate and trace take the grid instead of module constants. Tests: 30 fps 1080p and 24 fps sources package with the right segment length and trace to the right viewer; a 29.97 fps grid has no drift after an hour.

### Task V3: CMAF ladder in A and B
Per rung and variant: fragmented MP4 with a fragment per segment, split into an init segment and media segments named `HMAC(naming key, title|track|n|variant)`. Audio track in A and B. Shared HLS multivariant and media playlists and a DASH MPD. Tests: init segments identical across A and B per rung; segment n has exactly `seg_frames` frames in every rung; a mixed A/B sequence decodes cleanly; rungs above the source are skipped.

### Task V4: Sessions, tokens and CDN edge switching
`POST /api/sessions {title, viewer}` logs the session and returns a token (signed with an edge key derived from the secret; payload: title, expiry, A/B bits) and the manifest paths. Server route `/wm/<token>/<title>/...` serves manifests and maps segment requests to objects. `edge/core.js` with `edge/cloudflare-worker.js` and `edge/cloudfront-function.js`. Tests: Python and the JS core (run under Node) map the same token and path to the same object; expired or altered tokens get 403.

### Task V5: Per-viewer manifests
`/pl/<token>/master.m3u8`, media playlists and `manifest.mpd` listing that viewer's objects directly under `LEAKTRACE_MEDIA_URL`. Tests: the listed objects match the viewer's bits; no token in any segment URL.

### Task V6: Bring your own pipeline
`export-masters <title>`: marked masters A and B (keyframes on the grid) plus `grid.json`. Edge path template with `{variant}` for platforms' own layouts. Per-viewer MP4 download rebuilt on CMAF. Tests: the masters' keyframes fall on the grid; a viewer's MP4 traces to that viewer.

### Task V7: Service, UI, docs, ship
API and UI in the new terms (Watch plays through the edge route with hls.js); README with an integration guide per mode; `.env.example`; Dockerfile and CI; bench rerun; screenshots at desktop and phone widths.

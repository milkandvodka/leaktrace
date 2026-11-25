# Guided public demo: design

Approved 2026-11-25.

## Why

Someone who has never heard of LeakTrace opens the demo (on its own or inside another site's tab) and should understand in seconds what it does and see that it works. Today the demo opens on five tabs and an upload box with nothing to trace: too much, and no story.

## Goals

- One page, one story: **watch** a viewer's copy → see **real sample leaks** of copies → **trace** one and see the account named.
- One sentence on what it does; three lines on how it plugs into a streaming stack; a link to the attack-bench results.
- A short guided tour on first open (boxes pointing at each part, Skip, step dots), replayable from a **?** button in the header corner.
- "Try your own": download a viewer's copy, damage it however you like, upload it, watch it trace live.

## Non-goals and anti-goals

- No new marking or tracing logic. No fake progress or canned verdicts: every result shown is a real trace by this server.
- Nothing removed: the existing tabs (titles, viewers, watch, trace, bench) stay, behind one **Console** link.
- Outside demo mode (a real deployment) the console stays the default page.

## Approaches considered

1. **Sample leaks made and traced by the server on first start (chosen).** Real leaks of real copies; clicking shows the result instantly. Costs a few minutes of CPU once.
2. Sample leaks bundled in the repo: impossible, because the marks depend on each server's own secret.
3. Trace samples live on click: honest but 1–2 minutes of waiting per click on a small host; kept only for "try your own".

## Decisions

- Samples (2-minute excerpts of four demo viewers' copies): forwarded on Telegram, filmed with a phone, cropped, screen-recorded. Made with the bench's own attacks, traced once, stored under `<data>/demo/`.
- The demo audience is 1,000 accounts: the six named viewers plus 994 sample accounts, all of whom opened the title, so a result reads "named out of 1,000", the same pool size as the bench.
- `GET /api/demo` gives the page everything it needs (title, named viewers, samples with their trace ids, whether samples are still being prepared). `GET /api/demo/samples/<key>.mp4` serves a sample.
- The tour remembers it was seen in `localStorage` (wrapped in try/catch; it just shows again if storage is blocked).

## Left to the builder

Layout details, copy wording, tour positioning on small screens.

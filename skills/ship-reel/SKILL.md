---
name: ship-reel
description: Make a polished, captioned product demo reel (MP4 for X, plus 9:16) from a public URL and a one-line brief, using the Ship Reel tools. Use when the user asks for a demo video, launch reel, product walkthrough or screen recording of a website.
---

# Ship Reel: URL + brief -> finished demo reel

You direct; the tools do the camera work, editing and checks. A good reel is
short (30-60 s), shows 5-8 beats, and every beat has a caption that says why it
matters, not what is being clicked.

## 1. Plan (plan_reel)

1. If you do not know the page, call `plan_reel(url, brief)` with no steps. It
   returns a draft plan plus the page's headings and clickable labels.
2. Write the real shot list and call `plan_reel` again with `job_id` and `steps`.
   Each step: `action` (navigate, click, type, scroll, hover, wait, press),
   `target` (visible text or accessible name; `role=link:Docs`, `css=...` as a
   last resort), `caption`, optional `dwell` seconds.
3. Read `dry_run`. Every step must be `ok: true`. If a target did not resolve,
   pick another label from `page.clickable` and re-plan.

Shot list rules that make reels look professional:
- Open on the hero with a `wait` step (dwell 2) whose caption is the product's promise.
- Use `scroll` with a heading as `target` to move between sections; never more than two scrolls in a row.
- `hover` opens hover menus; `click` follows links and buttons. Clicks zoom in automatically.
- Use `wait` with `until: "<text>"` for anything slow (a run, a build, a generation). It is shown as a time-lapse with a speed badge.
- Captions: under 70 characters, sentence case, no emojis, no trailing full stop, benefit first ("Every hold lands in the audit log", not "Click Audit log").
- Set `title` and `tagline` for the title card; `cta_url` for the end card if it differs.
- If a click starts a one-off or rate-limited action, pass `dry_run: false` so it only runs once, while recording.

Ship Reel is read-only by design: it refuses to click sign-up, login, payment,
submit, delete or wallet controls. Only pass `allow_risky: true` when the user
explicitly asked for that exact action.

## 2. Record (record_reel)

`record_reel(job_id)`. Check `steps_ok == steps_total`. Skipped steps are cut
from the reel; if an important one failed, fix the target and re-plan.

## 3. Edit (edit_reel)

`edit_reel(job_id)` renders 1920x1080 plus a 1080x1920 vertical cut. Options:
`background` / `accent` (hex, default brand colour from the page), `fps` (30/60),
`sound` (`clicks` or `none`), `music` (path to a track the user owns),
`speed`. The edit fits itself under 90 s.

## 4. Verify and fix

Always call `verify_reel(job_id)`. If it fails or warns, call `fix_reel(job_id)`;
it maps each failure to a repair (re-time, re-encode, re-record) and re-verifies.
Report the final `summary` honestly.

## 5. Hand over

`draft_post(job_id, mention="@handle")` returns post text and alt text. Show the
user the video path and the draft. Never post or upload anything yourself
without the user's explicit go-ahead.

`list_reels` and `reel_status(job_id)` show past jobs.

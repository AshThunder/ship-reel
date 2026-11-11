# Meta demo: Hermes makes a reel of Nous Research (about 75 s)

The idea: Hermes Agent, running the Ship Reel plugin, makes a launch reel of
nousresearch.com. The demo is the agent at work, then the reel it made.

| Time | Shot | On screen | Voice-over or caption |
|---|---|---|---|
| 0:00-0:05 | Title | "Ship Reel, a Hermes Agent plugin" on a flat #0000EF card | "Demo videos take hours. Hermes can make one from a URL." |
| 0:05-0:15 | Hermes TUI | Type: "Make a launch reel of https://nousresearch.com. Show the search palette, the Hermes menu, and how to install." | "One sentence and a link." |
| 0:15-0:25 | Hermes TUI | `plan_reel` tool call with the 9-step shot list and the dry run marking each step ok | "It plans a shot list and dry-runs every step against the live site first." |
| 0:25-0:35 | Hermes TUI, sped up 4x (labelled) | `record_reel`, then `edit_reel` progress | "Then it records in a real browser and edits: zoom, cursor, captions, a vertical cut." |
| 0:35-0:40 | Hermes TUI | `verify_reel` PASS table, then `draft_post` text | "It checks its own work and drafts the post. It never posts." |
| 0:40-1:05 | The reel | samples/nous-research_landscape.mp4, 0:04-0:29 at full screen | (reel audio) |
| 1:05-1:12 | Split | Vertical cut beside the landscape cut | "Both formats, ready for X." |
| 1:12-1:15 | End card | "hermes plugins install AshThunder/ship-reel" | - |

The total is about 75 s, under X's limit of 2:20 and under the hackathon's cap.

## What is needed to record it

1. **A working model for the live Hermes session.** GEMINI_API_KEY authenticates
   (`gemini-2.5-flash` answered 404 "use a newer model", so the key is accepted), but
   on 11 Nov at 13:50-14:00 WAT `gemini-3.8-flash` returned 503 "high demand", and
   `gemini-3.7-flash` and `gemini-flash-latest` timed out. Options: retry Gemini later,
   or Chris supplies another provider key.
2. **Screen capture of the Hermes TUI.** Run in a terminal at 1920x1080 with a large font
   (asciinema plus agg, or a screen recorder). Ship Reel itself can't record a terminal.
3. **The finished reel.** samples/nous-research_landscape.mp4 and _vertical.mp4, or a
   fresh run from the live session.
4. **Assembly.** A short ffmpeg concat or any editor: title card, TUI capture (speed up
   the record and edit wait to 4x and label it), reel, end card.
5. **Optional voice-over.** Chris reads the lines above, or captions only.

Nothing in the demo logs in, submits forms or posts. The browsing on nousresearch.com is
read-only.

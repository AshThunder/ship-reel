# Meta demo: Hermes makes its own launch reel

A real Hermes Agent session builds a launch reel of nousresearch.com with Ship Reel.
Nothing is staged: the prompt is typed into the Hermes TUI, the model (openai/gpt-oss-120b
on Groq) decides every tool call, and the reel at the end is the unedited output of
`edit_reel`.

## What the recorded session shows (11 Nov 2026)

1. The prompt, typed into `hermes chat`.
2. `plan_reel`: the model writes an 8-step shot list. Ship Reel dry-runs it on the live
   site and flags two captions that described the cursor instead of the product. The
   model rewrites them and plans again.
3. `record_reel`: headless Chromium records the walkthrough, with every click checked
   for an effect (30 s).
4. `edit_reel`: landscape and vertical cuts with zoom, captions, Kokoro voice-over and
   the music bed (45 s).
5. `verify_reel`: PASS, 24 checks, no warnings.
6. `draft_post`: post text. Ship Reel never posts.

The whole session took 2 min 45 s of wall time, including Groq rate-limit retries.

## The cut (`scripts/compose_demo.py`, about 88 s)

| Time | Shot |
|---|---|
| 0:00 | Title card: "Hermes makes its own launch reel" |
| 0:03 | The prompt being typed (1.6x) |
| 0:19 | `plan_reel` and the caption rewrite (sped up, labelled) |
| 0:24 | `record_reel`, with the live browser capture picture-in-picture (sped up, labelled) |
| 0:37 | `edit_reel` (sped up, labelled) |
| 0:42 | `verify_reel` passes, `draft_post`, and the draft held on screen |
| 0:52 | Card: "The reel Hermes made" |
| 0:53 | The narrated reel, 31 s, with its own audio |
| 1:24 | End card: `hermes plugins install AshThunder/ship-reel` |

Every speed-up is labelled on screen; nothing is cut out of the agent's run.

## Reproduce

1. Configure a provider in `~/.hermes/config.yaml` that reads its key from the
   environment (`key_env`). Never put a key in the config file.
2. Record the session: `asciinema rec -c "hermes chat" session.cast`. A 112x34 terminal
   works well.
3. `python scripts/compose_demo.py session.cast <job_dir> demo.mp4`. `job_dir` is the
   Ship Reel job the session created, under the plugin data directory.

Notes from making it:
- Groq's free tier caps a request at 8,000 tokens per minute, so Ship Reel's tool results
  are deliberately compact. Full detail is always available from `reel_status`.
- In this Hermes build, `-t ship_reel` on the command line was rejected because the
  toolset name is validated before plugins load. Limit the CLI to Ship Reel with
  `platform_toolsets: {cli: [ship_reel]}` instead.

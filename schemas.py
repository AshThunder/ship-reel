"""Tool schemas: what the model reads."""

_JOB = {"job_id": {"type": "string", "description": "Reel job id returned by plan_reel"}}

_STEP = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["navigate", "click", "type", "scroll", "hover", "wait", "press"]},
        "target": {"type": "string", "description": "Visible text or accessible name of the element (preferred), or role=link:Docs, text=..., label=..., placeholder=..., css=..."},
        "url": {"type": "string", "description": "For navigate"},
        "value": {"type": "string", "description": "Text to type (for type)"},
        "key": {"type": "string", "description": "Key for press, e.g. Enter"},
        "amount": {"type": "number", "description": "Scroll distance in viewports (default 0.75)"},
        "to": {"type": "string", "enum": ["top", "bottom"]},
        "until": {"type": "string", "description": "For wait: text to wait for (e.g. 'Build finished'); the wait is shown as a sped-up time-lapse"},
        "timeout": {"type": "number", "description": "For wait with until: max seconds (default 60, max 150)"},
        "caption": {"type": "string", "description": "On-screen caption for this beat, under 70 characters, plain text, no emojis"},
        "dwell": {"type": "number", "description": "Seconds to hold after the action (0.3-6, default 1.6)"},
        "zoom": {"type": "boolean", "description": "Zoom the camera toward the interaction (default true for click/type/hover)"},
        "zoom_level": {"type": "number", "description": "1.0-2.2, default 1.6"},
    },
    "required": ["action"],
}

PLAN_REEL = {
    "name": "plan_reel",
    "description": (
        "Plan a product demo reel of a public web page. Give the URL and a short brief; optionally give "
        "your own shot list of 5-8 steps (navigate/click/type/scroll/hover/wait/press, each with a caption). "
        "The tool validates and normalises the steps, then dry-runs them in headless Chromium to confirm "
        "every target resolves. Without steps it inspects the page and drafts a plan you should refine. "
        "Returns a job_id, the plan, dry-run results and the page's headings and clickable labels. "
        "Read-only: clicks on payment, sign-up, login, submit or delete controls are refused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public http(s) URL to start from"},
            "brief": {"type": "string", "description": "What the reel should show, in a sentence or two"},
            "steps": {"type": "array", "items": _STEP, "description": "Shot list (5-8 steps reads best). Omit to get a draft."},
            "title": {"type": "string", "description": "Product name for the title card"},
            "tagline": {"type": "string", "description": "One line under the title"},
            "cta_url": {"type": "string", "description": "URL shown on the end card (defaults to url)"},
            "job_id": {"type": "string", "description": "Re-plan an existing job instead of creating a new one"},
            "allow_risky": {"type": "boolean", "description": "Only if the user explicitly asked to click account or payment controls"},
            "dry_run": {"type": "boolean", "description": "Dry-run the steps after validating (default true). Set false when a click starts a one-off or rate-limited action, so it only runs once while recording."},
        },
        "required": ["url", "brief"],
    },
}

RECORD_REEL = {
    "name": "record_reel",
    "description": (
        "Record the planned walkthrough in headless Chromium at 2x pixel density (~50 fps capture). "
        "Moves a cursor along eased paths, scrolls smoothly, verifies every step changed the page, retries "
        "misses with alternate locators and skips (and reports) steps that still fail. Takes about as long as the reel."
    ),
    "parameters": {"type": "object", "properties": _JOB, "required": ["job_id"]},
}

EDIT_REEL = {
    "name": "edit_reel",
    "description": (
        "Cut the recording into a finished reel: title card, auto-zoom toward clicks with a spring camera, "
        "rendered cursor and click ripples, numbered captions, page-to-page cross-fades, end card, subtle click "
        "sound design, loudness-safe audio. Exports H.264 MP4 (yuv420p, faststart, <=90s) for X at 1080p, plus a "
        "9:16 vertical cut by default. Colours follow the site's brand colour unless set."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **_JOB,
            "format": {"type": "string", "enum": ["landscape", "landscape720", "vertical"], "description": "Main output (default landscape 1920x1080)"},
            "vertical": {"type": "boolean", "description": "Also export 1080x1920 (default true)"},
            "fps": {"type": "integer", "enum": [30, 60]},
            "zoom": {"type": "boolean", "description": "Auto-zoom toward interactions (default true)"},
            "background": {"type": "string", "description": "Hex colour or 'auto'"},
            "accent": {"type": "string", "description": "Hex colour or 'auto'"},
            "sound": {"type": "string", "enum": ["clicks", "none"]},
            "music": {"type": "string", "description": "Optional path to a music file the user has rights to"},
            "speed": {"type": "number", "description": "Global speed multiplier 0.5-3 (default 1)"},
        },
        "required": ["job_id"],
    },
}

VERIFY_REEL = {
    "name": "verify_reel",
    "description": (
        "Check the rendered reel objectively: duration <=90s, H.264/yuv420p, resolution, frame rate, faststart, "
        "file size under X's limit, black frames, frozen video, audio true peak, captions present and readable, "
        "and whether any step was skipped. Returns pass/fail with details."
    ),
    "parameters": {"type": "object", "properties": _JOB, "required": ["job_id"]},
}

FIX_REEL = {
    "name": "fix_reel",
    "description": (
        "Repair what verify_reel flagged: tighten timing to fit, speed up frozen holds, re-encode format problems, "
        "shrink oversized files, re-record when steps were skipped or frames were black. Re-verifies after each "
        "attempt (max 3)."
    ),
    "parameters": {"type": "object", "properties": {**_JOB, "max_attempts": {"type": "integer", "minimum": 1, "maximum": 3}}, "required": ["job_id"]},
}

LIST_REELS = {
    "name": "list_reels",
    "description": "List recent reel jobs with status and output path.",
    "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
}

REEL_STATUS = {
    "name": "reel_status",
    "description": "Full status of one reel job: plan, recording results, outputs, verification and history.",
    "parameters": {"type": "object", "properties": _JOB, "required": ["job_id"]},
}

DRAFT_POST = {
    "name": "draft_post",
    "description": "Draft an X post (and video alt text) to accompany the reel. Plain text, no emojis. Never posts.",
    "parameters": {
        "type": "object",
        "properties": {
            **_JOB,
            "tone": {"type": "string", "enum": ["launch", "short"]},
            "mention": {"type": "string", "description": "Optional @handle to tag, e.g. @NousResearch"},
        },
        "required": ["job_id"],
    },
}

ALL = [PLAN_REEL, RECORD_REEL, EDIT_REEL, VERIFY_REEL, FIX_REEL, LIST_REELS, REEL_STATUS, DRAFT_POST]

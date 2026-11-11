"""Ship Reel - a Hermes Agent plugin that turns a URL and a brief into a polished product reel."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from . import hooks, schemas, tools
from .engine import netguard

logger = logging.getLogger(__name__)

TOOLSET = "ship_reel"


def _ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def register(ctx):
    # User-only opt-in for recording local/private hosts (plugins.entries.ship-reel.settings).
    if hasattr(ctx, "get_config"):
        netguard.set_setting_reader(lambda: ctx.get_config("allow_private_hosts", False))
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    for schema in schemas.ALL:
        name = schema["name"]
        kwargs = {}
        if name in ("edit_reel", "verify_reel", "fix_reel"):
            kwargs["check_fn"] = _ffmpeg_available
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=tools.HANDLERS[name], **kwargs)
    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            md = child / "SKILL.md"
            if child.is_dir() and md.exists():
                ctx.register_skill(child.name, md)

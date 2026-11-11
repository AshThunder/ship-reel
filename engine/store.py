"""Persistent job store.

Each reel is a job directory under the plugin data dir:
    <data>/jobs/<job_id>/job.json      job metadata, plan, status, history
    <data>/jobs/<job_id>/capture/      recorded frames + events.json
    <data>/jobs/<job_id>/render/       rendered outputs
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_ID_RE = re.compile(r"^[a-z0-9-]{4,64}$")


def data_dir() -> Path:
    """Resolve the plugin data directory.

    Order: SHIP_REEL_HOME (tests / standalone) -> Hermes plugin_data_dir -> ~/.hermes/plugin-data/ship-reel.
    """
    override = os.environ.get("SHIP_REEL_HOME")
    if override:
        p = Path(override).expanduser()
    else:
        p = None
        try:  # Hermes public helper; absent when running standalone
            from plugins.plugin_storage import plugin_data_dir  # type: ignore

            p = Path(plugin_data_dir("ship-reel"))
        except Exception:
            p = Path.home() / ".hermes" / "plugin-data" / "ship-reel"
    p.mkdir(parents=True, exist_ok=True)
    return p


def jobs_dir() -> Path:
    d = data_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:24] or "reel"


def new_job_id(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return f"{_slug(host)}-{time.strftime('%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def job_path(job_id: str) -> Path:
    if not _ID_RE.match(job_id or ""):
        raise ValueError(f"invalid job id: {job_id!r}")
    return jobs_dir() / job_id


def load(job_id: str) -> dict[str, Any]:
    p = job_path(job_id) / "job.json"
    if not p.exists():
        raise FileNotFoundError(f"no reel job named {job_id!r}; call list_reels to see jobs")
    return json.loads(p.read_text())


def save(job: dict[str, Any]) -> dict[str, Any]:
    d = job_path(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    job["updated_at"] = time.time()
    tmp = d / "job.json.tmp"
    with _LOCK:
        tmp.write_text(json.dumps(job, indent=2))
        os.replace(tmp, d / "job.json")
    return job


def create(url: str, brief: str) -> dict[str, Any]:
    jid = new_job_id(url)
    job = {
        "id": jid,
        "url": url,
        "brief": brief,
        "status": "planned",
        "created_at": time.time(),
        "history": [],
    }
    return save(job)


def log(job: dict[str, Any], event: str, **fields: Any) -> None:
    job.setdefault("history", []).append({"t": round(time.time(), 2), "event": event, **fields})


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    out = []
    for d in sorted(jobs_dir().iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "job.json"
        if not f.exists():
            continue
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        out.append(
            {
                "id": j["id"],
                "url": j.get("url"),
                "status": j.get("status"),
                "title": (j.get("plan") or {}).get("title"),
                "output": (j.get("render") or {}).get("main"),
                "updated_at": j.get("updated_at"),
            }
        )
        if len(out) >= limit:
            break
    return out

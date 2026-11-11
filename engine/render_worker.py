"""Render worker: renders a frame range of a render context to an H.264 chunk.

Run as a separate process by editor.render_parallel:
    python render_worker.py <ctx.json> <start> <end> <out.mp4>
It loads the engine package by path, so it works no matter what name Hermes
gave the plugin package.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _load_engine():
    pkg_dir = Path(__file__).resolve().parent
    name = "shipreel_engine"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def main(argv: list[str]) -> int:
    ctx_path, start, end, out = argv[1], int(argv[2]), int(argv[3]), argv[4]
    _load_engine()
    from shipreel_engine import ffmpeg as ff  # type: ignore
    from shipreel_engine.render import Compositor  # type: ignore

    ctx = json.loads(Path(ctx_path).read_text())
    comp = Compositor(ctx)
    rp = ctx["plan"]
    proc = subprocess.Popen(ff.encode_chunk_cmd(rp["W"], rp["H"], rp["fps"], out, crf=ctx.get("crf", 17)),
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for fr in rp["frames"][start:end]:
            proc.stdin.write(comp.render(fr).tobytes())  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        sys.stderr.write(err[-2000:])
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))

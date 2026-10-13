#!/usr/bin/env python3
"""Stage the static site and run the browser smoke test (Playwright).

SMOKE_BASE  — hit an already-served site (no staging, no local server).
SMOKE_SITE  — serve this directory as-is (a staged or published tree).
Neither set — stage frontend/ plus data/generated/<month>/ into the published
layout (data/months.json and data/<month>/) and serve that.

The published tree has no frontend/ directory: index.html is at the root and
data/<month>/ is already in place, so this script serves the project directory.
"""
from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def run_node(base: str) -> None:
    environment = os.environ.copy()
    environment["SMOKE_BASE"] = base if base.endswith("/") else base + "/"
    subprocess.run(
        ["node", str(PROJECT / "tests" / "browser_smoke.js")],
        check=True,
        timeout=240,
        env=environment,
    )


def serve(directory: Path) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run_node(f"http://127.0.0.1:{server.server_port}/")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def stage_source(stage: Path) -> None:
    frontend = PROJECT / "frontend"
    if not frontend.is_dir():
        raise SystemExit(f"no frontend/ at {frontend} and no SMOKE_SITE/SMOKE_BASE")
    shutil.copytree(frontend, stage, dirs_exist_ok=True)
    data = stage / "data"
    data.mkdir()
    gen = PROJECT / "data" / "generated"
    months = gen / "months.json"
    if not months.exists():
        raise SystemExit(f"missing {months}; build a month before smoking")
    shutil.copy2(months, data / "months.json")
    copied = 0
    for child in sorted(gen.iterdir()):
        if child.is_dir() and child.name.isdigit() and len(child.name) == 6:
            shutil.copytree(child, data / child.name)
            copied += 1
    if copied == 0:
        raise SystemExit(f"no data/generated/YYYYMM directory under {gen}")


def main() -> int:
    base = os.environ.get("SMOKE_BASE")
    site = os.environ.get("SMOKE_SITE")
    if base and site:
        raise SystemExit("set only one of SMOKE_BASE and SMOKE_SITE")
    if base:
        run_node(base)
    elif site:
        serve(Path(site))
    elif (PROJECT / "index.html").exists() and (PROJECT / "data" / "months.json").exists() and not (PROJECT / "frontend").is_dir():
        serve(PROJECT)
    else:
        with tempfile.TemporaryDirectory(prefix="eqc-smoke-") as temporary:
            stage = Path(temporary)
            stage_source(stage)
            serve(stage)
    print("[smoke] browser smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

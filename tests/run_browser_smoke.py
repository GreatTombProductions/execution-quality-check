#!/usr/bin/env python3
"""Stage the static site and run the browser smoke test (Playwright).

Stages frontend/ plus all generated data (meta/summary/rollup/coverage/validation/
search_index + symbol shards), serves on a local port, and runs browser_smoke.js.
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="eqc-smoke-") as temporary:
        stage = Path(temporary)
        shutil.copytree(PROJECT / "frontend", stage, dirs_exist_ok=True)
        data = stage / "data"
        data.mkdir()
        gen = PROJECT / "data" / "generated"
        for name in ("meta.json", "summary.json", "rollup.json", "search_index.json",
                     "coverage.json", "validation.json", "receipts.json"):
            src = gen / name
            if not src.exists():
                src = PROJECT / "data" / "raw" / "202608" / "receipts.json"
            shutil.copy2(src, data / name)
        shutil.copytree(gen / "symbols", data / "symbols")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=stage)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            environment = os.environ.copy()
            environment["SMOKE_BASE"] = f"http://127.0.0.1:{server.server_port}/"
            subprocess.run(
                ["node", str(PROJECT / "tests" / "browser_smoke.js")],
                check=True,
                timeout=240,
                env=environment,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("[smoke] browser smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

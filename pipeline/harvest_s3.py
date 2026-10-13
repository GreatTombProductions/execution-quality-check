"""Harvest August-2026 amended-format Rule 605 files from the shared Download Site host.

public.s3.com/rule605/<dir>/<RIC>202608.{zip,csv,pdf} — one directory per registered firm
(a firm may carry several RICs). Writes raw files under data/raw/<month>/<RIC>/ (detailed.zip
+ summary.csv, same layout fetch.py uses) and a tracked receipts file
data/raw/<month>/harvest-receipts.json (bytes, sha256, Last-Modified, HTTP status).
Idempotent: files already present with matching Content-Length are not re-downloaded.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import reporters  # noqa: E402
reporters.activate(reporters.month_from_argv())
MONTH = reporters.MONTH

RAW_DIR = PROJECT_DIR / "data" / "raw" / MONTH
HOST = "https://public.s3.com/rule605/"
UA = "curl/8.5.0"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def curl(args, timeout=30):
    return subprocess.run(["curl", "-s", "--max-time", str(timeout), "-A", UA, *args],
                          capture_output=True, text=True, errors="replace").stdout


def list_dir(d):
    html = curl([f"{HOST}{d}/?C=M;O=D"])
    files = re.findall(r'href="([^"?][^"]*)"', html)
    return [f for f in files if not f.endswith("/") and not f.startswith(("/", "?"))]


def root_dirs():
    html = curl([HOST], timeout=40)
    return sorted(set(re.findall(r'href="([a-z0-9._-]+)/"', html)) - {".."})


def head(url):
    out = curl(["-I", url])
    cl = re.search(r"content-length:\s*(\d+)", out, re.I)
    lm = re.search(r"last-modified:\s*(.+)", out, re.I)
    st = out.split()[1] if out.split() else None
    return st, int(cl.group(1)) if cl else None, lm.group(1).strip() if lm else None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def fetch_one(job):
    d, ric, kind, fname = job
    url = f"{HOST}{d}/{fname}"
    dest = RAW_DIR / ric / ("detailed.zip" if kind == "detailed" else "summary.csv")
    st, cl, lm = head(url)
    rec = {"dir": d, "ric": ric, "kind": kind, "url": url, "http_status": st,
           "content_length": cl, "last_modified": lm}
    if st != "200":
        rec["ok"] = False
        return rec
    if not (dest.exists() and dest.stat().st_size == cl):
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-s", "--max-time", "900", "-A", UA, "-o", str(dest), url])
    ok = dest.exists() and dest.stat().st_size == cl
    rec.update({"ok": ok, "bytes": dest.stat().st_size if dest.exists() else 0,
                "sha256": sha256(dest) if ok else None, "fetched_at": now_iso()})
    return rec


def main():
    dirs = root_dirs()
    jobs = []
    census = {}
    for d in dirs:
        files = list_dir(d)
        hits = [f for f in files if f"{MONTH}." in f]
        census[d] = {"n_files": len(files), f"files_{MONTH}": hits, "month_files": hits}
        for f in hits:
            m = re.fullmatch(rf"([A-Za-z0-9]+){MONTH}\.(zip|csv)", f)
            if m:
                jobs.append((d, m.group(1), "detailed" if m.group(2) == "zip" else "summary", f))
    print(f"[harvest] {len(dirs)} dirs, {sum(1 for c in census.values() if c.get(f'files_{MONTH}'))} with {MONTH}, {len(jobs)} files")
    with cf.ThreadPoolExecutor(4) as ex:
        recs = list(ex.map(fetch_one, jobs))
    bad = [r for r in recs if not r["ok"]]
    out = {"month": MONTH, "harvested_at": now_iso(), "host": HOST, "census": census, "files": recs}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "harvest-receipts.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"[harvest] ok {len(recs) - len(bad)}/{len(recs)}; failures: {[(r['ric'], r['kind'], r['http_status']) for r in bad]}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

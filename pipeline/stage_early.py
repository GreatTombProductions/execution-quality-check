"""Stage one month's early filings without publishing them.

Refuses August: that month is already harvested, and re-running it would rewrite
receipts. Writes under data/raw/<month>/ only. A partial month is not a build —
this script does not call build.py and does not pass --publish.

Shared-host files are downloaded by harvest_s3.py (same --month). This script
covers the exchange routes: MEMX's list API (report ids are not stable), Cboe,
IEX, MIAX, Nasdaq. HTTP 200 is saved. Anything else is a probe receipt, not a
failure and not a reused August file.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import reporters  # noqa: E402

UA = "curl/8.5.0"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def curl(args, timeout=60):
    return subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", UA, *args],
        capture_output=True, text=True, errors="replace",
    ).stdout


def head(url):
    out = curl(["-D", "-", "-o", "/dev/null", "-I", url], timeout=40)
    status = None
    for line in out.splitlines():
        if line.startswith("HTTP/"):
            parts = line.split()
            if len(parts) > 1:
                status = parts[1]
    cl = None
    for line in out.splitlines():
        if line.lower().startswith("content-length:"):
            cl = int(line.split(":", 1)[1].strip())
    redirect = None
    for line in out.splitlines():
        if line.lower().startswith("location:"):
            redirect = line.split(":", 1)[1].strip()
    return status, cl, redirect


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_memx(month, raw):
    body = curl(["https://www.memxtrading.com/api/v1/report/605"], timeout=40)
    try:
        rows = json.loads(body)
    except json.JSONDecodeError:
        rows = []
    if isinstance(rows, dict):
        rows = rows.get("reports") or rows.get("data") or []
    found = None
    for row in rows if isinstance(rows, list) else []:
        name = str(row.get("fileName") or row.get("filename") or "")
        rid = row.get("id")
        if month in name and rid is not None:
            found = str(rid)
            break
    if found and found.isdigit():
        (raw / "memx-report-id.txt").write_text(found + "\n")
    return {"http_body_bytes": len(body), "report_id": found, "rows": len(rows) if isinstance(rows, list) else None}


def main():
    if "--month" not in sys.argv:
        raise SystemExit("pass --month YYYYMM (this does not default to August)")
    month = reporters.month_from_argv()
    if month == "202608":
        raise SystemExit("stage_early refuses 202608; August is already harvested")
    raw = PROJECT_DIR / "data" / "raw" / month
    raw.mkdir(parents=True, exist_ok=True)
    memx = discover_memx(month, raw)
    reporters.activate(month)
    probes = []
    files = []
    for rep in reporters.LIVE:
        if rep.get("harvested"):
            continue
        for kind, url in (("detailed", rep["detailed_url"]), ("summary", rep["summary_url"])):
            if not url:
                probes.append({"ric": rep["ric"], "kind": kind, "url": None, "http_status": None,
                               "note": "no url for this month (MEMX id not published)"})
                continue
            status, cl, redirect = head(url)
            rec = {"ric": rep["ric"], "kind": kind, "url": url, "http_status": status,
                   "content_length": cl, "redirect": redirect, "checked_at": now_iso()}
            probes.append(rec)
            if status != "200" or kind != "detailed":
                # Summary CSVs that are the same URL as the zip (MEMX, IEX) are not a second file.
                if status == "200" and kind == "summary" and url != rep["detailed_url"]:
                    dest = raw / rep["ric"] / "summary.csv"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(["curl", "-s", "--max-time", "120", "-A", UA, "-o", str(dest), url], check=False)
                    if dest.exists() and dest.stat().st_size > 0:
                        files.append({**rec, "ok": True, "bytes": dest.stat().st_size, "sha256": sha256(dest),
                                      "path": str(dest.relative_to(raw))})
                continue
            dest = raw / rep["ric"] / "detailed.zip"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not (dest.exists() and cl is not None and dest.stat().st_size == cl):
                subprocess.run(["curl", "-s", "--max-time", "900", "-A", UA, "-o", str(dest), url], check=False)
            ok = dest.exists() and dest.stat().st_size > 0 and (cl is None or dest.stat().st_size == cl)
            files.append({**rec, "ok": ok, "bytes": dest.stat().st_size if dest.exists() else 0,
                          "sha256": sha256(dest) if ok else None, "path": str(dest.relative_to(raw))})
            print(f"[stage] {rep['ric']} {kind} HTTP {status} ok={ok}")
    (raw / "probes.json").write_text(json.dumps({
        "month": month, "checked_at": now_iso(), "memx": memx, "probes": probes,
    }, indent=1) + "\n")
    (raw / "receipts.json").write_text(json.dumps({
        "month": month, "fetched_at": now_iso(), "partial": True, "published": False, "files": files,
    }, indent=1) + "\n")
    print(f"[stage] {month}: {sum(1 for f in files if f.get('ok'))} files saved, {len(probes)} probes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
